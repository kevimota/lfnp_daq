"""Per-family digitizer drivers.

Each board family gets its own driver so the divergent behaviour is isolated
from the acquisition orchestration in DigitizerScanner:

* DT5742 (X742 family, DRS4 sampling chip): group-based configuration
  (``set_group_enable_mask``, ``set_group_dc_offset``, fast-trigger TR0),
  DRS4 sampling/correction API, ``set_post_trigger_size`` (global, percent),
  X742Event decode.
* DT5743 (X743 family, SAMLONG sampling chip): per-channel configuration
  (``set_channel_enable_mask``, ``set_channel_dc_offset``,
  ``set_channel_self_trigger``), SAM sampling/correction API,
  ``set_sam_post_trigger_size`` (per channel, percent), X743Event decode.

``build_driver(info)`` picks the driver from the board model
(``get_info().model``) and raises a clear error for any other board.

DRS4 and SAM calls are never mixed: each driver only ever calls the API family
of its own board. A ``caen_libs.error.Error`` raised by a board-specific call
is re-raised with a clear message (it usually means a board-detection bug).
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from caen_libs.caendigitizer import (
    DRS4Frequency,
    EnaDis,
    Error,
    SAMCorrectionLevel,
    SAMPulseSourceType,
    SAMFrequency,
    TriggerMode,
    X742Event,
    X743Event,
)
from caen_libs._caendigitizertypes import BoardModel

_LOG = logging.getLogger("daq.digitizer")

#: Supported DRS4 sampling frequencies, keyed by Hz (see DRS4Frequency).
_DRS4_FREQUENCIES_BY_HZ = {
    5_000_000_000: DRS4Frequency.F_5GHz,
    2_500_000_000: DRS4Frequency.F_2_5GHz,
    1_000_000_000: DRS4Frequency.F_1GHz,
    750_000_000: DRS4Frequency.F_750MHz,
}

#: Supported SAMLONG sampling frequencies, keyed by Hz (see SAMFrequency).
_SAM_FREQUENCIES_BY_HZ = {
    3_200_000_000: SAMFrequency.F_3_2GHz,
    1_600_000_000: SAMFrequency.F_1_6GHz,
    800_000_000: SAMFrequency.F_800MHz,
    400_000_000: SAMFrequency.F_400MHz,
}


class DigitizerDriver(ABC):
    """Board geometry, calibration state and per-event extraction.

    Common setup (record length, acquisition mode, max events per BLT) is kept
    in DigitizerScanner._do_configure; this class owns the board-specific part
    via ``configure()``.
    """

    #: True when the board uses the DRS4 sampling/correction API
    drs4 = False
    #: True when the board uses the SAMLONG sampling/correction API
    sam = False
    #: physical channels per group (X743 group geometry, else 1)
    channels_per_group = 1
    #: whether ``set_record_length()`` is supported (SAM yes, DRS4 fixed 1024)
    record_length_configurable = False
    #: default sampling frequency in Hz when the config omits it
    default_frequency_hz: Optional[int] = None
    #: accepted values for ``sampling_frequency_hz`` (in Hz)
    supported_frequencies_hz: list[int] = []
    #: default post-trigger size in percent
    post_trigger_default_percent = 50
    #: on-board DRS4 correction (X742 only; off by default, see below)
    correction_enabled = False

    def __init__(self, info):
        self.info = info
        self.enabled_channels: list[int] = []
        self.input_range_vpp: Optional[float] = None
        self.calibrated = False
        self.drs4_time: Optional[list[float]] = None

    @property
    def model(self) -> BoardModel:
        return self.info.model

    @property
    def model_name(self) -> str:
        return getattr(self.info, "model_name", str(self.info.model))

    @property
    def n_groups(self) -> int:
        """Number of acquisition groups (for DRS4 boards this is the number
        reported by BoardInfo.channels)."""
        return int(self.info.channels)

    @property
    def n_total(self) -> int:
        return self.n_groups * self.channels_per_group

    def _format_frequencies(self) -> str:
        return ", ".join(
            f"{hz / 1e9:g} GS/s ({hz} Hz)" for hz in self.supported_frequencies_hz
        )

    def validate_config(self, cfg: dict) -> None:
        """Raise ValueError with a clear message when the config is invalid for
        this board (unsupported sampling frequency, out-of-range channel,
        invalid post-trigger size)."""
        errors: list[str] = []

        freq = cfg.get("sampling_frequency_hz")
        if freq is not None:
            hz = round(float(freq))
            if hz not in self.supported_frequencies_hz:
                errors.append(
                    f"sampling_frequency_hz {freq} not supported for {self.model_name}: "
                    f"allowed values are {self._format_frequencies()}"
                )

        post = cfg.get("post_trigger_size")
        if post is not None:
            percent = int(post)
            if not 0 <= percent <= 100:
                errors.append(
                    f"post_trigger_size must be a percentage 0-100, got {post}"
                )

        for ch in cfg.get("channels", []):
            if not ch.get("enabled"):
                continue
            n = int(ch["channel"])
            if not 0 <= n < self.n_total:
                errors.append(
                    f"channel {n} out of range for {self.model_name} (0..{self.n_total - 1})"
                )

        if errors:
            raise ValueError(self.model_name + ": " + "; ".join(errors))

    def _resolve_frequency_hz(self, cfg: dict) -> int:
        hz = cfg.get("sampling_frequency_hz")
        if hz is not None:
            hz = round(float(hz))
            if hz in self.supported_frequencies_hz:
                return hz
            _LOG.warning(
                "unsupported sampling_frequency_hz=%s for %s, using default %s Hz",
                cfg.get("sampling_frequency_hz"),
                self.model_name,
                self.default_frequency_hz,
            )
        if self.default_frequency_hz is None:
            raise ValueError(f"{self.model_name}: no default sampling frequency defined")
        return self.default_frequency_hz

    def _channel_mask(self, cfg: dict) -> int:
        """Resolve the config channels into a bit mask and remember the
        enabled channels (as reported to the caller / used for extraction)."""
        mask = 0
        enabled: list[int] = []
        for ch in cfg.get("channels", []):
            n = int(ch["channel"])
            if ch.get("enabled") and 0 <= n < self.n_total:
                mask |= 1 << n
                enabled.append(n)
        if not enabled:
            mask = (1 << self.n_total) - 1
            enabled = list(range(self.n_total))
        self.enabled_channels = enabled
        return mask

    def _caen_call(self, fn, *args):
        """Run a board-specific CAEN call, surfacing failures clearly. An Error
        here usually means the wrong API family was used for the connected board
        (a board-detection bug), so we re-raise with an explicit message."""
        name = getattr(fn, "__name__", str(fn))
        _LOG.debug("%s: CAEN call %s%r", self.model_name, name, tuple(args))
        try:
            res = fn(*args)
        except Error as e:
            raise RuntimeError(
                f"{self.model_name}: CAEN call {getattr(e, 'func', name)} "
                f"unsupported or failed — check digitizer board detection "
                f"(model={int(self.model)}) — {e}"
            ) from e
        _LOG.debug("%s: CAEN call %s -> OK", self.model_name, name)
        return res

    def configure(self, dev, cfg: dict) -> None:
        """Board-specific configuration. Validate the config, then apply the
        enable mask, post-trigger size, sampling frequency/calibration and any
        optional trigger/offset settings."""
        self.validate_config(cfg)
        self.correction_enabled = bool(cfg.get("correction", False))
        mask = self._channel_mask(cfg)
        self.set_enable_mask(dev, mask)
        self.configure_post_trigger(dev, cfg)
        self.configure_frequency(dev, cfg)
        self.configure_triggers(dev, cfg)

    @abstractmethod
    def set_enable_mask(self, dev, mask: int) -> None:
        """Enable channels (bit per physical channel) via the board's API."""

    @abstractmethod
    def configure_post_trigger(self, dev, cfg: dict) -> None:
        """Apply the post-trigger size for this board."""

    @abstractmethod
    def configure_frequency(self, dev, cfg: dict) -> None:
        """Apply sampling frequency and calibration for this board."""

    def configure_triggers(self, dev, cfg: dict) -> None:
        """Optional board-specific trigger/offset settings (no-op by default)."""

    @abstractmethod
    def extract(self, evt):
        """Decode one event -> (waveforms aligned to enabled_channels, scalars)
        or None if the board produced no usable waveform."""


class X742Driver(DigitizerDriver):
    """DT5742 (X742 family, DRS4): groups of 8 analog channels plus a TR0
    reference channel (data_channel index 8) that is excluded from the enabled
    channel set."""

    drs4 = True
    channels_per_group = 8
    record_length_configurable = False
    default_frequency_hz = 5_000_000_000
    supported_frequencies_hz = list(_DRS4_FREQUENCIES_BY_HZ)

    def set_enable_mask(self, dev, mask: int) -> None:
        group_mask = 0
        for g in range(self.n_groups):
            group_channels = ((1 << self.channels_per_group) - 1) << (g * self.channels_per_group)
            if mask & group_channels:
                group_mask |= 1 << g
        self._caen_call(dev.set_group_enable_mask, group_mask)

    def configure_post_trigger(self, dev, cfg: dict) -> None:
        percent = max(0, min(100, int(cfg.get("post_trigger_size", self.post_trigger_default_percent))))
        self._caen_call(dev.set_post_trigger_size, percent)

    def configure_frequency(self, dev, cfg: dict) -> None:
        freq = _DRS4_FREQUENCIES_BY_HZ[self._resolve_frequency_hz(cfg)]
        self._caen_call(dev.set_drs4_sampling_frequency, freq)
        if not self.correction_enabled:
            # On-board DRS4 correction (load/enable/get_correction_tables) is
            # DISABLED by default: it corrupts the heap on this DT5742
            # ("malloc(): corrupted top size" right after enable_drs4_correction,
            # detected at the next allocation). CAEN ships the X742 correction
            # as OFFLINE routines (samples/x742_DataCorrection) for exactly this
            # reason. Decoding raw is always safe; X742Events decode to float
            # either way, so extraction is unchanged.
            self.drs4_time = None
            self.calibrated = False
            _LOG.info(
                "DT5742: on-board DRS4 correction DISABLED (raw acquisition; "
                "set 'correction': true to enable, but it may corrupt the heap)"
            )
            return
        self._caen_call(dev.load_drs4_correction_data, freq)
        self._caen_call(dev.enable_drs4_correction)
        try:
            _LOG.debug(
                "%s: CAEN call get_correction_tables%r",
                self.model_name,
                (freq,),
            )
            self.drs4_time = list(dev.get_correction_tables(freq).time)
        except Error:
            self.drs4_time = None
        self.calibrated = self.drs4_time is not None
        if not self.calibrated:
            _LOG.warning(
                "DT5742: DRS4 correction tables unavailable after load — "
                "waveforms will be recorded without correction"
            )

    def configure_triggers(self, dev, cfg: dict) -> None:
        """Optional DRS4 fast-trigger / group DC-offset settings (applied only
        when the config provides them)."""
        for item in cfg.get("groups", []):
            g = int(item["group"])
            if not 0 <= g < self.n_groups:
                raise ValueError(f"DT5742: group {g} out of range (0..{self.n_groups - 1})")
            if "dc_offset" in item:
                self._caen_call(dev.set_group_dc_offset, g, int(item["dc_offset"]))
            if "fast_trigger_threshold" in item:
                self._caen_call(dev.set_group_fast_trigger_threshold, g, int(item["fast_trigger_threshold"]))
        if cfg.get("fast_trigger_mode") is not None:
            self._caen_call(dev.set_fast_trigger_mode, TriggerMode(cfg["fast_trigger_mode"]))
        if cfg.get("fast_trigger_digitizing") is not None:
            self._caen_call(dev.set_fast_trigger_digitizing, EnaDis(cfg["fast_trigger_digitizing"]))

    def extract(self, evt):
        if not isinstance(evt, X742Event):
            return None
        waveforms = [None] * len(self.enabled_channels)
        scalars = {"tdc": 0, "peak": 0.0, "baseline": 0.0, "charge": 0.0, "start_cell": 0}
        found_scalars = False
        for g_idx, group in enumerate(evt.data_group):
            if group is None:
                continue
            if not found_scalars:
                scalars["start_cell"] = int(group.start_index_cell)
                found_scalars = True
            for j in range(self.channels_per_group):
                arr = group.data_channel[j]
                if arr.size == 0:
                    continue
                ch = g_idx * self.channels_per_group + j
                if ch in self.enabled_channels:
                    waveforms[self.enabled_channels.index(ch)] = np.asarray(arr, dtype=np.float32)
        if not any(w is not None for w in waveforms):
            return None
        return waveforms, scalars


class X743Driver(DigitizerDriver):
    """DT5743 (X743 family, SAMLONG): each group carries 2 data channels but the
    channels are configured independently (per-channel enable/DC offset/
    threshold), unlike the group-level configuration of the DRS4 boards."""

    sam = True
    channels_per_group = 2
    record_length_configurable = True
    default_frequency_hz = 3_200_000_000
    supported_frequencies_hz = list(_SAM_FREQUENCIES_BY_HZ)
    sam_correction_level = SAMCorrectionLevel.ALL

    def set_enable_mask(self, dev, mask: int) -> None:
        self._caen_call(dev.set_channel_enable_mask, mask)

    def configure_post_trigger(self, dev, cfg: dict) -> None:
        percent = max(0, min(100, int(cfg.get("post_trigger_size", self.post_trigger_default_percent))))
        for ch in self.enabled_channels:
            self._caen_call(dev.set_sam_post_trigger_size, ch, percent)

    def configure_frequency(self, dev, cfg: dict) -> None:
        freq = _SAM_FREQUENCIES_BY_HZ[self._resolve_frequency_hz(cfg)]
        self._caen_call(dev.set_sam_sampling_frequency, freq)
        # SAM-specific calibration: a completely different API from DRS4.
        # Using the DRS4 correction functions on this board is a bug.
        self._caen_call(dev.set_sam_correction_level, self.sam_correction_level)
        self._caen_call(dev.load_sam_correction_data)
        self.calibrated = True

    def configure_triggers(self, dev, cfg: dict) -> None:
        """Optional per-channel DC offset / threshold / self-trigger / test
        pulser settings (applied only when the config provides them)."""
        dc_offsets = cfg.get("dc_offsets") or {}
        thresholds = cfg.get("trigger_thresholds") or {}
        for ch in self.enabled_channels:
            if ch in dc_offsets:
                self._caen_call(dev.set_channel_dc_offset, ch, int(dc_offsets[ch]))
            if ch in thresholds:
                self._caen_call(dev.set_channel_trigger_threshold, ch, int(thresholds[ch]))
        if cfg.get("self_trigger"):
            mask = sum(1 << ch for ch in self.enabled_channels)
            self._caen_call(dev.set_channel_self_trigger, TriggerMode.ACQ_ONLY, mask)
        if cfg.get("test_pulses"):
            for ch in self.enabled_channels:
                self._caen_call(dev.enable_sam_pulse_gen, ch, 0, SAMPulseSourceType.SOFTWARE)

    def extract(self, evt):
        if not isinstance(evt, X743Event):
            return None
        waveforms = [None] * len(self.enabled_channels)
        scalars = {"tdc": 0, "peak": 0.0, "baseline": 0.0, "charge": 0.0, "start_cell": 0}
        found_scalars = False
        for g_idx, group in enumerate(evt.data_group):
            if group is None:
                continue
            if not found_scalars:
                scalars = {
                    "tdc": int(group.tdc),
                    "peak": float(group.peak),
                    "baseline": float(group.baseline),
                    "charge": float(group.charge),
                    "start_cell": int(group.start_index_cell),
                }
                found_scalars = True
            for j, arr in enumerate(group.data_channel):
                ch = g_idx * self.channels_per_group + j
                if ch in self.enabled_channels:
                    waveforms[self.enabled_channels.index(ch)] = np.asarray(arr, dtype=np.float32)
        if not any(w is not None for w in waveforms):
            return None
        return waveforms, scalars


def build_driver(info) -> DigitizerDriver:
    """Pick the driver matching a board's reported model.

    Raises ValueError for any board other than a DT5742 / DT5743 instead of
    silently defaulting to one configuration path."""
    model = info.model
    if model == BoardModel.DT5742:
        return X742Driver(info)
    if model == BoardModel.DT5743:
        return X743Driver(info)
    raise ValueError(
        f"Unsupported digitizer model {getattr(info, 'model_name', model)} "
        f"(model={int(model)}): only DT5742 and DT5743 are supported"
    )
