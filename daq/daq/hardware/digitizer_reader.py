import asyncio
import os
from datetime import datetime, UTC
from typing import Callable, Optional

import numpy as np
import uproot

from caen_libs.caendigitizer import (
    DRS4Frequency,
    Device,
    Error,
    ReadMode,
    TriggerMode,
    Uint16Event,
    Uint8Event,
    X743Event,
)
from caen_libs._caendigitizertypes import BoardFamilyCode

from .digitizer_interface import open_device

_CHANNELS_PER_X743_GROUP = 2  # MAX_X743_CHANNELS_X_GROUP

_DRS4_FAMILIES = {BoardFamilyCode.XX742, BoardFamilyCode.XX743}

_DEFAULT_DRS4_FREQUENCY = DRS4Frequency.F_5GHz
_DEFAULT_ACQUISITION_TIMEOUT_S = 3600.0


class DigitizerScanner:
    """Acquires digitizer waveforms during the FSM RECORDING phase.

    Random trigger uses an internal periodic software trigger (one SendSWtrigger
    every 1/frequency seconds). External trigger reads from the trigger input;
    acquisition ends after ``number_of_triggers`` waveforms or a safety timeout.

    Waveforms are voltage-calibrated where the hardware supports it (DRS4 boards
    with firmware correction; otherwise a linear counts->volts conversion using
    ``input_range_vpp``). Per HV point a ROOT file ``digitizer_point_{i}.root`` is
    written via uproot.
    """

    def __init__(self, digitizer_row):
        self.connection_type = digitizer_row.connection_type
        self.arg = digitizer_row.arg
        self.conet_node = digitizer_row.conet_node
        self.vme_base_address = digitizer_row.vme_base_address

        self.device: Optional[Device] = None
        self.info = None
        self.is_drs4 = False
        self.enabled_channels: list[int] = []
        self.record_length = 0
        self.input_range_vpp: Optional[float] = None
        self.calibrated = False
        self.drs4_time: Optional[list[float]] = None

        # Per-point acquisition state
        self._run_dir = ""
        self._point_index = 0
        self._run_id = 0
        self._hv_channels: list[dict] = []
        self._target = 0
        self._mode = "random"
        self._sw_interval: Optional[float] = None
        self._timeout_s = _DEFAULT_ACQUISITION_TIMEOUT_S
        self._started_at = 0.0
        self._last_sw = 0.0
        self._collected = 0
        self._events: list[int] = []
        self._timestamps: list[float] = []
        self._time_tags: list[int] = []
        self._start_cells: list[int] = []
        self._tdcs: list[int] = []
        self._peaks: list[float] = []
        self._baselines: list[float] = []
        self._charges: list[float] = []
        self._waveforms: list[list[np.ndarray]] = []

    @property
    def collected(self) -> int:
        return self._collected

    # ── lifecycle ───────────────────────────────────────────────

    def open(self):
        if self.device is not None:
            return self
        self.device = open_device(
            self.connection_type,
            self.arg,
            self.conet_node,
            self.vme_base_address,
        )
        self.info = self.device.get_info()
        self.is_drs4 = self.info.family_code in _DRS4_FAMILIES
        return self

    def close(self):
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None

    def configure(self, cfg: dict):
        if self.device is None:
            raise RuntimeError("Digitizer not open")

        dev = self.device
        dev.reset()

        self._mode = cfg.get("trigger_mode", "random")
        if self._mode == "external":
            dev.set_ext_trigger_input_mode(TriggerMode.ACQ_ONLY)
        else:
            dev.set_sw_trigger_mode(TriggerMode.ACQ_ONLY)
            dev.set_ext_trigger_input_mode(TriggerMode.DISABLED)

        n_total = self.info.channels
        mask = 0
        enabled = []
        for ch in cfg.get("channels", []):
            ch_num = int(ch["channel"])
            if ch.get("enabled") and 0 <= ch_num < n_total:
                mask |= 1 << ch_num
                enabled.append(ch_num)
        if not enabled:
            mask = (1 << n_total) - 1
            enabled = list(range(n_total))
        self.enabled_channels = enabled
        dev.set_channel_enable_mask(mask)

        self._attempt(lambda: dev.set_post_trigger_size(int(cfg.get("post_trigger_size", 50))))

        if not self.is_drs4:
            record_length = cfg.get("record_length")
            if record_length:
                self._attempt(lambda: dev.set_record_length(int(record_length)))
        self.record_length = self._attempt(dev.get_record_length) or 0

        self.input_range_vpp = float(cfg["input_range_vpp"]) if cfg.get("input_range_vpp") else None
        if self.is_drs4:
            self._configure_drs4()
            self.calibrated = self.drs4_time is not None
        else:
            self.calibrated = self.input_range_vpp is not None

        dev.malloc_readout_buffer()
        dev.allocate_event()
        return self.enabled_channels

    def _configure_drs4(self):
        dev = self.device
        try:
            dev.set_drs4_sampling_frequency(_DEFAULT_DRS4_FREQUENCY)
            dev.load_drs4_correction_data(_DEFAULT_DRS4_FREQUENCY)
            dev.enable_drs4_correction()
        except Error:
            self.drs4_time = None
            return
        try:
            corr = dev.get_correction_tables(_DEFAULT_DRS4_FREQUENCY)
            self.drs4_time = list(corr.time)
        except Error:
            self.drs4_time = None

    def _attempt(self, fn: Callable[[], int]):
        try:
            return fn()
        except Error:
            return None

    # ── per-point acquisition ───────────────────────────────────

    def begin_point(self, run_dir: str, point_index: int, cfg: dict, hv_channels: list[dict], run_id: int):
        self._run_dir = run_dir
        self._point_index = point_index
        self._run_id = run_id
        self._hv_channels = hv_channels
        self._target = int(cfg.get("number_of_triggers", 0))
        self._mode = cfg.get("trigger_mode", "random")
        self._timeout_s = float(cfg.get("acquisition_timeout_s", _DEFAULT_ACQUISITION_TIMEOUT_S))
        freq = float(cfg.get("trigger_frequency_hz", 1.0))
        self._sw_interval = (1.0 / freq) if (self._mode == "random" and freq > 0) else None

        self._events = []
        self._timestamps = []
        self._time_tags = []
        self._start_cells = []
        self._tdcs = []
        self._peaks = []
        self._baselines = []
        self._charges = []
        self._waveforms = []
        self._collected = 0

        dev = self.device
        dev.clear_data()
        dev.sw_start_acquisition()
        now = asyncio.get_event_loop().time()
        self._started_at = now
        self._last_sw = now

    async def step(self) -> dict:
        """One readout iteration. Returns progress {'collected', 'target', 'timed_out'}."""
        dev = self.device
        if self._sw_interval is not None:
            now = asyncio.get_event_loop().time()
            if now - self._last_sw >= self._sw_interval:
                dev.send_sw_trigger()
                self._last_sw = now

        dev.read_data(ReadMode.POLLING_MBLT)
        n_events = dev.get_num_events()
        if n_events:
            for i in range(n_events):
                info, buf = dev.get_event_info(i)
                evt = dev.decode_event(buf)
                extracted = self._extract_event(evt)
                if extracted is None:
                    continue
                waveforms, scalars = extracted
                self._events.append(self._collected)
                self._timestamps.append(datetime.now(UTC).timestamp())
                self._time_tags.append(info.trigger_time_tag)
                self._start_cells.append(scalars["start_cell"])
                self._tdcs.append(scalars["tdc"])
                self._peaks.append(scalars["peak"])
                self._baselines.append(scalars["baseline"])
                self._charges.append(scalars["charge"])
                self._waveforms.append(waveforms)
                self._collected += 1
                if self._collected >= self._target:
                    break

        timed_out = (
            self._target > 0
            and self._collected < self._target
            and (asyncio.get_event_loop().time() - self._started_at) > self._timeout_s
        )
        return {"collected": self._collected, "target": self._target, "timed_out": timed_out}

    async def end_point(self) -> dict:
        """Stop acquisition and write the per-point ROOT file."""
        dev = self.device
        try:
            dev.sw_stop_acquisition()
        except Error:
            pass

        path = os.path.join(self._run_dir, f"digitizer_point_{self._point_index}.root")
        self._write_root(path)
        return {"n_events": self._collected, "path": path}

    # ── event handling ──────────────────────────────────────────

    def _extract_event(self, evt):
        """Return (waveforms: list[np.ndarray] aligned to enabled_channels, scalars: dict) or None."""
        if isinstance(evt, X743Event):
            return self._extract_x743(evt)
        if isinstance(evt, (Uint16Event, Uint8Event)):
            return self._extract_uint(evt)
        return None

    def _extract_x743(self, evt):
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
            arrays = group.data_channel
            for j, arr in enumerate(arrays):
                ch = g_idx * _CHANNELS_PER_X743_GROUP + j
                if ch in self.enabled_channels:
                    waveforms[self.enabled_channels.index(ch)] = np.asarray(arr, dtype=np.float32)
        if not any(w is not None for w in waveforms):
            return None
        return waveforms, scalars

    def _extract_uint(self, evt):
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

    # ── ROOT output ─────────────────────────────────────────────

    def _write_root(self, path: str):
        n = len(self._events)
        rec_len = 1
        for wf in self._waveforms:
            for arr in wf:
                if arr is not None:
                    rec_len = max(rec_len, arr.shape[0])

        data = {
            "event": np.asarray(self._events, dtype=np.int32),
            "timestamp": np.asarray(self._timestamps, dtype=np.float64),
            "time_tag": np.asarray(self._time_tags, dtype=np.uint32),
            "start_cell": np.asarray(self._start_cells, dtype=np.uint16),
            "tdc": np.asarray(self._tdcs, dtype=np.uint64),
            "peak": np.asarray(self._peaks, dtype=np.float32),
            "baseline": np.asarray(self._baselines, dtype=np.float32),
            "charge": np.asarray(self._charges, dtype=np.float32),
        }
        for idx, ch in enumerate(self.enabled_channels):
            arr = np.full((n, rec_len), np.nan, dtype=np.float32)
            for e in range(n):
                w = self._waveforms[e][idx]
                if w is not None:
                    arr[e, : w.shape[0]] = w[:rec_len]
            data[f"ch{ch}"] = arr

        meta = {
            "run_id": np.array([self._run_id], dtype=np.int32),
            "point_index": np.array([self._point_index], dtype=np.int32),
            "digitizer_model": [self.info.model_name],
            "board_model": np.array([int(self.info.model)], dtype=np.uint32),
            "serial_number": np.array([self.info.serial_number], dtype=np.uint32),
            "family_code": np.array([int(self.info.family_code)], dtype=np.uint32),
            "firmware_code": np.array([int(self.info.firmware_code)], dtype=np.uint32),
            "adc_n_bits": np.array([self.info.adc_n_bits], dtype=np.uint16),
            "n_channels": np.array([self.info.channels], dtype=np.uint16),
            "channels_used": np.asarray(self.enabled_channels, dtype=np.uint16),
            "record_length": np.array([rec_len], dtype=np.uint32),
            "trigger_mode": [self._mode],
            "number_of_triggers": np.array([self._target], dtype=np.uint32),
            "input_range_vpp": np.array([self.input_range_vpp or 0.0], dtype=np.float64),
            "calibrated": np.array([1 if self.calibrated else 0], dtype=np.int32),
            "connection_type": np.array([int(self.connection_type)], dtype=np.int32),
            "link_used": [str(self.arg)],
        }
        if self._hv_channels:
            meta["hv_slot"] = np.array([c["slot"] for c in self._hv_channels], dtype=np.int32)
            meta["hv_channel"] = np.array([c["channel"] for c in self._hv_channels], dtype=np.int32)
            meta["hv_voltage"] = np.array([c["voltage"] for c in self._hv_channels], dtype=np.float32)
        if self.drs4_time:
            meta["drs4_time"] = np.asarray(self.drs4_time, dtype=np.float32)

        with uproot.recreate(path) as f:
            f["digitizer"] = data
            f["meta"] = meta
