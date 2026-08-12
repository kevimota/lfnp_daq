"""Per-family digitizer drivers.

Each board family gets its own driver so the divergent behaviour is isolated
from the acquisition orchestration in DigitizerScanner: channel vs group
enable masks, event layout (X742 / X743 / uint16-uint8), sampling frequency
and calibration.

``build_driver(info)`` picks the driver for a board from its family code.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from caen_libs.caendigitizer import (
    DRS4Frequency,
    Error,
    Uint16Event,
    Uint8Event,
    X742Event,
    X743Event,
)
from caen_libs._caendigitizertypes import BoardFamilyCode

_LOG = logging.getLogger("daq.digitizer")

#: Supported DRS4 sampling frequencies, keyed by Hz (see DRS4Frequency).
_DRS4_FREQUENCIES = {
    5_000_000_000: DRS4Frequency.F_5GHz,
    2_500_000_000: DRS4Frequency.F_2_5GHz,
    1_000_000_000: DRS4Frequency.F_1GHz,
    750_000_000: DRS4Frequency.F_750MHz,
}


class DigitizerDriver(ABC):
    """Board geometry, calibration state and per-event extraction."""

    #: True when the board needs the DRS4 sampling/correction API
    drs4 = False
    #: physical channels per group (DRS4 boards only, else 1)
    channels_per_group = 1
    #: default sampling frequency in Hz when the config omits it
    default_frequency_hz: Optional[int] = None

    def __init__(self, info):
        self.info = info
        self.enabled_channels: list[int] = []
        self.input_range_vpp: Optional[float] = None
        self.calibrated = False
        self.drs4_time: Optional[list[float]] = None

    @property
    def n_groups(self) -> int:
        """Number of acquisition groups (for DRS4 boards this is the number
        reported by BoardInfo.channels)."""
        return int(self.info.channels)

    @property
    def n_total(self) -> int:
        return self.n_groups * self.channels_per_group

    def config_defaults(self) -> dict:
        """Per-digitizer config defaults applied when the run config omits them."""
        defaults = {"post_trigger_size": 50}
        if self.default_frequency_hz is not None:
            defaults["sampling_frequency_hz"] = self.default_frequency_hz
        return defaults

    @abstractmethod
    def set_enable_mask(self, dev, mask: int) -> None:
        """Enable channels (bit per physical channel) via the board's API."""

    @abstractmethod
    def configure_frequency(self, dev, cfg: dict) -> None:
        """Apply sampling frequency and calibration for this board."""

    @abstractmethod
    def extract(self, evt):
        """Decode one event -> (waveforms aligned to enabled_channels, scalars)
        or None if the board produced no usable waveform."""


class DRS4Driver(DigitizerDriver):
    """Shared behaviour for boards that expose the DRS4 sampling/correction
    API (X742 and X743 families)."""

    drs4 = True

    def set_enable_mask(self, dev, mask: int) -> None:
        group_mask = 0
        for g in range(self.n_groups):
            group_channels = ((1 << self.channels_per_group) - 1) << (g * self.channels_per_group)
            if mask & group_channels:
                group_mask |= 1 << g
        dev.set_group_enable_mask(group_mask)

    def _resolve_frequency(self, cfg: dict) -> DRS4Frequency:
        hz = cfg.get("sampling_frequency_hz")
        if hz is not None:
            freq = _DRS4_FREQUENCIES.get(round(float(hz)))
            if freq is not None:
                return freq
            _LOG.warning(
                "unsupported sampling_frequency_hz=%s for %s, using default",
                hz,
                self.info.model_name,
            )
        return _DRS4_FREQUENCIES[self.default_frequency_hz]

    def configure_frequency(self, dev, cfg: dict) -> None:
        freq = self._resolve_frequency(cfg)
        try:
            dev.set_drs4_sampling_frequency(freq)
            dev.load_drs4_correction_data(freq)
            dev.enable_drs4_correction()
        except Error:
            self.drs4_time = None
            self.calibrated = False
            return
        try:
            self.drs4_time = list(dev.get_correction_tables(freq).time)
        except Error:
            self.drs4_time = None
        self.calibrated = self.drs4_time is not None


class X743Driver(DRS4Driver):
    """DT5743 / SAMLONG (X743 family): groups of 2 channels."""

    channels_per_group = 2
    default_frequency_hz = 5_000_000_000

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


class X742Driver(DRS4Driver):
    """DT5742 (X742 family): groups of 8 channels plus a TR0 reference channel
    (data_channel index 8) that is excluded from the enabled channel set."""

    channels_per_group = 8
    default_frequency_hz = 5_000_000_000

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


class StandardDriver(DigitizerDriver):
    """Non-DRS4 digitizers (uint16/uint8 events): channel mask and ADC-count to
    volts conversion based on the configured input range."""

    def set_enable_mask(self, dev, mask: int) -> None:
        dev.set_channel_enable_mask(mask)

    def configure_frequency(self, dev, cfg: dict) -> None:
        self.calibrated = self.input_range_vpp is not None

    def extract(self, evt):
        if not isinstance(evt, (Uint16Event, Uint8Event)):
            return None
        arrays = evt.data_channel
        waveforms = [None] * len(self.enabled_channels)
        for ch in self.enabled_channels:
            if ch < len(arrays):
                arr = np.asarray(arrays[ch], dtype=np.float32)
                if self.calibrated and self.input_range_vpp:
                    arr = (arr / (1 << self.info.adc_n_bits) - 0.5) * self.input_range_vpp
                waveforms[self.enabled_channels.index(ch)] = arr
        if not any(w is not None for w in waveforms):
            return None
        scalars = {"tdc": 0, "peak": 0.0, "baseline": 0.0, "charge": 0.0, "start_cell": 0}
        return waveforms, scalars


def build_driver(info) -> DigitizerDriver:
    """Pick the driver matching a board's reported info."""
    family = info.family_code
    if family == BoardFamilyCode.XX743:
        return X743Driver(info)
    if family == BoardFamilyCode.XX742:
        return X742Driver(info)
    return StandardDriver(info)