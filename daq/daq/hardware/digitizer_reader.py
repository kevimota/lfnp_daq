import asyncio
import concurrent.futures
import logging
import os
import time
from datetime import datetime, UTC
from typing import Callable, Optional

import numpy as np
import awkward as ak
import uproot

from caen_libs.caendigitizer import (
    AcqMode,
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

# Read out with a slave-terminated MBLT cycle, exactly like CAEN's
# ReadoutTest sample. Never read on an empty FIFO (an MBLT read with no
# pending data can block indefinitely).
_READ_MODE = ReadMode.SLAVE_TERMINATED_READOUT_MBLT

# Max software triggers coalesced into a single step (protects against clock
# jumps / long pauses producing a huge catch-up burst at once).
_MAX_TRIGGERS_PER_STEP = 20

_LOG = logging.getLogger("daq.digitizer")

_STALL_LOG_SECONDS = 30.0
_MILESTONE_STEPS = 20  # log collected/target progress ~MILESTONE_STEPS times per point


class DigitizerScanner:
    """Acquires digitizer waveforms during the FSM RECORDING phase.

    Random trigger uses an internal periodic software trigger (one SendSWtrigger
    every 1/frequency seconds). External trigger reads from the trigger input;
    acquisition ends after ``number_of_triggers`` waveforms.

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

        # CAEN calls are synchronous and can block for a long time (optical/USB
        # I/O, decode, ROOT writes). Run them on a dedicated single worker thread
        # so the asyncio loop stays responsive (status/pause/resume always work).
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # Per-point acquisition state
        self._run_dir = ""
        self._point_index = 0
        self._run_id = 0
        self._hv_channels: list[dict] = []
        self._target = 0
        self._mode = "random"
        self._sw_interval: Optional[float] = None
        self._last_sw = 0.0
        self._collected = 0
        self._sw_triggers_sent = 0
        self._skipped_events = 0
        self._point_started = 0.0
        self._last_collect_at = 0.0
        self._stall_warned = False
        self._last_milestone = -1
        self._events: list[int] = []
        self._timestamps: list[float] = []
        self._time_tags: list[int] = []
        self._start_cells: list[int] = []
        self._tdcs: list[int] = []
        self._peaks: list[float] = []
        self._baselines: list[float] = []
        self._charges: list[float] = []
        self._waveforms: list[list[np.ndarray]] = []
        self._read_attempts = 0

    @property
    def collected(self) -> int:
        return self._collected

    # ── lifecycle ───────────────────────────────────────────────

    def _run(self, fn, *args):
        return asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _do_open(self):
        dev = open_device(
            self.connection_type,
            self.arg,
            self.conet_node,
            self.vme_base_address,
        )
        return dev, dev.get_info()

    async def open(self):
        if self.device is not None:
            return self
        t0 = time.monotonic()
        dev, info = await self._run(self._do_open)
        self.device = dev
        self.info = info
        self.is_drs4 = self.info.family_code in _DRS4_FAMILIES
        _LOG.info(
            "digitizer opened in %.2fs: %s serial=%s channels=%s drs4=%s",
            time.monotonic() - t0,
            getattr(info, "model_name", "?"),
            getattr(info, "serial_number", "?"),
            getattr(info, "channels", "?"),
            self.is_drs4,
        )
        return self

    def _do_close(self):
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None

    async def close(self):
        if self.device is not None:
            await self._run(self._do_close)
            _LOG.info("digitizer closed (collected this session: %s)", self._collected)
        self._executor.shutdown(wait=False)

    def _do_configure(self, cfg: dict):
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

        # Match CAEN's ReadoutTest sample: software-controlled acquisition
        # (required for the SendSWtrigger -> ReadData pattern) and a modest
        # max-events-per-BLT so each transfer completes promptly.
        dev.set_acquisition_mode(AcqMode.SW_CONTROLLED)
        self._attempt(lambda: dev.set_max_num_events_blt(64))

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
        _LOG.info(
            "digitizer configured: mode=%s enabled_channels=%s record_length=%s "
            "input_range_vpp=%s calibrated=%s",
            self._mode,
            self.enabled_channels,
            self.record_length,
            self.input_range_vpp,
            self.calibrated,
        )
        return self.enabled_channels

    async def configure(self, cfg: dict):
        return await self._run(self._do_configure, cfg)

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

    def _do_begin_point(self, run_dir: str, point_index: int, cfg: dict, hv_channels: list[dict], run_id: int):
        self._run_dir = run_dir
        self._point_index = point_index
        self._run_id = run_id
        self._hv_channels = hv_channels
        self._target = int(cfg.get("number_of_triggers", 0))
        self._mode = cfg.get("trigger_mode", "random")
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
        self._sw_triggers_sent = 0
        self._skipped_events = 0
        self._read_attempts = 0
        self._point_started = time.monotonic()
        self._last_collect_at = self._point_started
        self._stall_warned = False
        self._last_milestone = -1

        dev = self.device
        dev.clear_data()
        dev.sw_start_acquisition()
        self._last_sw = time.monotonic()

        _LOG.info(
            "run=%s point=%s: acquisition started mode=%s target=%s freq=%s sw_interval=%s",
            run_id,
            point_index,
            self._mode,
            self._target,
            freq,
            self._sw_interval,
        )

    async def begin_point(self, run_dir: str, point_index: int, cfg: dict, hv_channels: list[dict], run_id: int):
        return await self._run(self._do_begin_point, run_dir, point_index, cfg, hv_channels, run_id)

    def _do_step(self) -> dict:
        dev = self.device
        now = time.monotonic()

        read_now = True
        if self._sw_interval is not None:
            if now - self._last_sw >= self._sw_interval:
                due = int((now - self._last_sw) / self._sw_interval)
                due = max(1, min(due, _MAX_TRIGGERS_PER_STEP))
                t_sw = time.monotonic()
                for _ in range(due):
                    dev.send_sw_trigger()
                t_sw = time.monotonic() - t_sw
                self._sw_triggers_sent += due
                self._last_sw += due * self._sw_interval
                if now - self._last_sw >= self._sw_interval:
                    self._last_sw = now
                _LOG.debug(
                    "run=%s point=%s: sent %s sw_trigger(s) in %.3fs (interval=%.3fs) then reading",
                    self._run_id,
                    self._point_index,
                    due,
                    t_sw,
                    self._sw_interval,
                )
                if t_sw > 2.0:
                    _LOG.warning(
                        "run=%s point=%s: send_sw_trigger blocked %.2fs (%s triggers)",
                        self._run_id,
                        self._point_index,
                        t_sw,
                        due,
                    )
            else:
                # No software trigger due yet - the FIFO is empty, and an MBLT
                # read on an empty FIFO can block indefinitely. Skip the read.
                read_now = False
                _LOG.debug(
                    "run=%s point=%s: no sw_trigger due yet (%.0fms into %.0fms interval) - skip read",
                    self._run_id,
                    self._point_index,
                    (now - self._last_sw) * 1000,
                    self._sw_interval * 1000,
                )

        n_events = 0
        t0 = time.monotonic()
        if read_now:
            self._read_attempts += 1
            t0 = time.monotonic()
            _LOG.debug(
                "run=%s point=%s: read_data entering attempt=%s mode=%s",
                self._run_id,
                self._point_index,
                self._read_attempts,
                _READ_MODE.name,
            )
            dev.read_data(_READ_MODE)
            t_read = time.monotonic() - t0
            n_events = dev.get_num_events()
            if t_read > 2.0:
                _LOG.warning(
                    "run=%s point=%s: read_data blocked %.2fs (attempt=%s)",
                    self._run_id,
                    self._point_index,
                    t_read,
                    self._read_attempts,
                )
            else:
                _LOG.debug(
                    "run=%s point=%s: read_data returned in %.3fs attempt=%s events=%s",
                    self._run_id,
                    self._point_index,
                    t_read,
                    self._read_attempts,
                    n_events,
                )
        for i in range(n_events or 0):
            info, buf = dev.get_event_info(i)
            evt = dev.decode_event(buf)
            extracted = self._extract_event(evt)
            if extracted is None:
                self._skipped_events += 1
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
            if self._target > 0 and self._collected >= self._target:
                break

        if n_events:
            self._last_collect_at = time.monotonic()
            _LOG.debug(
                "run=%s point=%s: read_data returned %s events in %.3fs -> collected=%s/%s",
                self._run_id,
                self._point_index,
                n_events,
                time.monotonic() - t0,
                self._collected,
                self._target,
            )

        if self._target > 0:
            progress = self._collected / self._target
            milestone = int(progress * (_MILESTONE_STEPS - 1))
            if milestone != self._last_milestone:
                self._last_milestone = milestone
                _LOG.info(
                    "run=%s point=%s: progress %.0f%% (%s/%s) triggers sent=%s skipped=%s elapsed=%.1fs",
                    self._run_id,
                    self._point_index,
                    progress * 100,
                    self._collected,
                    self._target,
                    self._sw_triggers_sent,
                    self._skipped_events,
                    time.monotonic() - self._point_started,
                )

        if not self._stall_warned and now - self._last_collect_at > _STALL_LOG_SECONDS:
            self._stall_warned = True
            _LOG.warning(
                "run=%s point=%s: no new events collected for %.0fs (collected=%s/%s "
                "triggers sent=%s mode=%s) - scan may never finish",
                self._run_id,
                self._point_index,
                now - self._last_collect_at,
                self._collected,
                self._target,
                self._sw_triggers_sent,
                self._mode,
            )

        return {"collected": self._collected, "target": self._target}

    async def step(self) -> dict:
        """One readout iteration (runs on a worker thread)."""
        return await self._run(self._do_step)

    def _do_end_point(self) -> dict:
        dev = self.device
        try:
            dev.sw_stop_acquisition()
        except Error:
            pass

        path = os.path.join(self._run_dir, f"digitizer_point_{self._point_index}.root")
        self._write_root(path)
        _LOG.info(
            "run=%s point=%s: acquisition ended collected=%s/%s (skipped=%s, triggers sent=%s) -> %s",
            self._run_id,
            self._point_index,
            self._collected,
            self._target,
            self._skipped_events,
            self._sw_triggers_sent,
            path,
        )
        return {"n_events": self._collected, "path": path}

    async def end_point(self) -> dict:
        """Stop acquisition and write the per-point ROOT file (on a worker thread)."""
        return await self._run(self._do_end_point)

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

        meta_fields = {
            "run_id": int(self._run_id),
            "point_index": int(self._point_index),
            "digitizer_model": str(self.info.model_name),
            "board_model": int(self.info.model),
            "serial_number": int(self.info.serial_number),
            "family_code": int(self.info.family_code),
            "firmware_code": int(self.info.firmware_code),
            "adc_n_bits": int(self.info.adc_n_bits),
            "n_channels": int(self.info.channels),
            "channels_used": [int(c) for c in self.enabled_channels],
            "record_length": int(rec_len),
            "trigger_mode": self._mode,
            "number_of_triggers": int(self._target),
            "input_range_vpp": float(self.input_range_vpp or 0.0),
            "calibrated": 1 if self.calibrated else 0,
            "connection_type": int(self.connection_type),
            "link_used": str(self.arg),
        }
        if self._hv_channels:
            meta_fields["hv_slot"] = [int(c["slot"]) for c in self._hv_channels]
            meta_fields["hv_channel"] = [int(c["channel"]) for c in self._hv_channels]
            meta_fields["hv_voltage"] = [float(c["voltage"]) for c in self._hv_channels]
        if self.drs4_time:
            meta_fields["drs4_time"] = [float(t) for t in self.drs4_time]

        meta = ak.Array([meta_fields])

        with uproot.recreate(path) as f:
            f["digitizer"] = data
            f["meta"] = meta
