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
    Device,
    Error,
    IOLevel,
    ReadMode,
    TriggerMode,
)

from caen_libs._caendigitizertypes import BoardModel

from .digitizer_driver import DigitizerDriver, build_driver
from .digitizer_interface import open_device

#: Full-scale input range per board model, fixed by hardware (still written to
#: the output metadata for the analyst even though it is not configurable).
_FIXED_INPUT_RANGE_VPP = {
    BoardModel.DT5742: 1.0,
    BoardModel.DT5743: 2.5,
}

#: Scalar branches dropped per board model.
#:  - DT5742/X742 has no peak/baseline/charge/tdc scalars in hardware,
#:    so the driver leaves them at 0.0 (placeholders). Dropping them
#:    keeps the output file lean.
#:  - DT5743/X743 analysis does not need peak/charge (per the previous issue).
_UNUSED_SCALAR_BRANCHES = {
    BoardModel.DT5742: ("tdc", "peak", "baseline", "charge"),
    BoardModel.DT5743: ("peak", "charge"),
}

# Read out with a slave-terminated MBLT cycle, exactly like CAEN's
# ReadoutTest sample. Never read on an empty FIFO (an MBLT read with no
# pending data can block indefinitely).
_READ_MODE = ReadMode.SLAVE_TERMINATED_READOUT_MBLT

# Max software triggers coalesced into a single step (protects against clock
# jumps / long pauses producing a huge catch-up burst at once).
_MAX_TRIGGERS_PER_STEP = 20

# CAEN status register 0x8104: bit 3 indicates event ready (data available in FIFO).
# Used to avoid blocking on empty FIFO when reading in external/fast trigger modes.
_STATUS_REGISTER = 0x8104
_STATUS_EVENT_READY_BIT = 3

_LOG = logging.getLogger("daq.digitizer")

_STALL_LOG_SECONDS = 30.0
_MILESTONE_STEPS = 20  # log collected/target progress ~MILESTONE_STEPS times per point

#: Trigger-mode combinations each board supports.
_ALLOWED_TRIGGER_MODES = {
    18: {"random", "external", "fast", "random-external", "random-fast"},
    27: {"random", "external", "random-external"},
}

#: Human-readable names for the CAEN trigger-mode enum (logs / ROOT meta).
_TRIGGER_MODE_NAMES = {
    TriggerMode.DISABLED: "DISABLED",
    TriggerMode.ACQ_ONLY: "ACQ_ONLY",
    TriggerMode.EXTOUT_ONLY: "EXTOUT_ONLY",
    TriggerMode.ACQ_AND_EXTOUT: "ACQ_AND_EXTOUT",
}

#: Meta fields that hold a variable-length list of strings. The TTree writer
#: cannot represent these, so the meta tree is written as an RNTuple.
_STRING_LIST_META_FIELDS = {"trigger_fast_thresholds", "trigger_fast_dc_offsets"}


def _empty_list_array(dtype) -> ak.Array:
    """One empty list with a concrete element type.

    ``ak.Array([[]])`` infers ``var * unknown``, which Uproot's RNTuple writer
    cannot serialize.
    """
    return ak.Array(
        ak.contents.ListOffsetArray(
            ak.index.Index64(np.array([0, 0], dtype=np.int64)),
            ak.contents.NumpyArray(np.array([], dtype=dtype)),
        )
    )


def _empty_string_list_array() -> ak.Array:
    """One empty list of strings with a concrete element type."""
    chars = ak.contents.NumpyArray(
        np.array([], dtype=np.uint8), parameters={"__array__": "char"}
    )
    strings = ak.contents.ListOffsetArray(
        ak.index.Index64(np.array([0], dtype=np.int64)),
        chars,
        parameters={"__array__": "string"},
    )
    return ak.Array(
        ak.contents.ListOffsetArray(
            ak.index.Index64(np.array([0, 0], dtype=np.int64)), strings
        )
    )


def _meta_to_awkward(meta_fields: dict) -> ak.Array:
    """Build the single-record meta array, typing empty lists explicitly."""
    branches = {}
    for name, value in meta_fields.items():
        if isinstance(value, list):
            if value:
                branches[name] = ak.Array([value])
            elif name in _STRING_LIST_META_FIELDS:
                branches[name] = _empty_string_list_array()
            else:
                branches[name] = _empty_list_array(np.int64)
        else:
            branches[name] = np.asarray([value])
    return ak.Array(branches)


class DigitizerScanner:
    """Acquires digitizer waveforms during the FSM RECORDING phase.

    Simultaneous trigger mode: multiple trigger sources (random SW, external,
    fast TR0) are enabled together; hardware ORs them. Every event is tagged
    "mixed" since DT5742 event headers contain no source bits. Per-event TR0
    digitization (ch16/ch17) allows post-acquisition discrimination of fast
    vs random triggers.

    Waveforms are voltage-calibrated where the hardware supports it (DRS4 boards
    with firmware correction; otherwise a linear counts->volts conversion using
    the board's fixed full-scale input range). Per HV point a ROOT file
    ``digitizer_point_{i}.root`` is written via uproot.
    """

    def __init__(self, digitizer_row):
        self.connection_type = digitizer_row.connection_type
        self.arg = digitizer_row.arg
        self.conet_node = digitizer_row.conet_node
        self.vme_base_address = digitizer_row.vme_base_address

        self.device: Optional[Device] = None
        self.info = None
        self.driver: Optional[DigitizerDriver] = None
        self.is_drs4 = False
        self.is_sam = False
        self.enabled_channels: list[int] = []
        self.record_length = 0
        self.input_range_vpp: Optional[float] = None
        self.dc_offset: Optional[int] = None
        self.calibrated = False
        self.supports_fast_trigger = False
        #: Verified trigger-source state and effective post-trigger position
        #: (populated by _do_configure, written to the ROOT metadata).
        self._trigger_state: dict[str, str] = {}
        self._post_trigger_size = 0

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
        self._trigger_mode: str = "random"
        self._has_random = False
        self._has_physical = False
        self._sw_interval: Optional[float] = None
        self._last_sw = 0.0
        self._collected = 0
        #: Events that count toward the goal. In 2-trigger modes (random +
        #: fast/external) only physical triggers count; random ones are still
        #: recorded but do not advance the goal.
        self._physical_collected = 0
        self._random_collected = 0
        self._count_physical_only = False
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
        self._tr0_waveforms: list = []
        self._read_attempts = 0

    @property
    def collected(self) -> int:
        return self._collected

    @property
    def physical_collected(self) -> int:
        """Physical (fast/external) events counted toward the goal."""
        return self._physical_collected

    @property
    def goal_collected(self) -> int:
        """Count that drives the goal/progress (physical in 2-trigger modes)."""
        return self._physical_collected if self._count_physical_only else self._collected

    @property
    def random_collected(self) -> int:
        """Random-triggered events recorded in this point."""
        return self._random_collected

    @property
    def count_physical_only(self) -> bool:
        return self._count_physical_only

    # ── lifecycle ───────────────────────────────────────────────

    def _run(self, fn, *args):
        return asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _do_open(self):
        self._trace(
            "open_digitizer2",
            self.connection_type,
            self.arg,
            self.conet_node,
            self.vme_base_address,
        )
        dev = open_device(
            self.connection_type,
            self.arg,
            self.conet_node,
            self.vme_base_address,
        )
        self._trace("get_info")
        return dev, dev.get_info()

    async def open(self):
        if self.device is not None:
            return self
        t0 = time.monotonic()
        dev, info = await self._run(self._do_open)
        self.device = dev
        self.info = info
        self.driver = build_driver(info)
        self.is_drs4 = self.driver.drs4
        self.is_sam = self.driver.sam
        _LOG.info(
            "digitizer opened in %.2fs: %s model=%s serial=%s groups=%s "
            "channels_per_group=%s drs4=%s sam=%s",
            time.monotonic() - t0,
            getattr(info, "model_name", "?"),
            int(info.model),
            getattr(info, "serial_number", "?"),
            self.driver.n_groups,
            self.driver.channels_per_group,
            self.is_drs4,
            self.is_sam,
        )
        return self

    def _do_close(self):
        if self.device is not None:
            try:
                self._trace(
                    "close_digitizer (free_event + free_readout_buffer + close)"
                )
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
        self._trace("reset")
        dev.reset()

        # ── Determine trigger mode from config ──
        self._trigger_mode = cfg.get("trigger_mode", "random")

        # Verify allowed trigger_mode for the board before proceeding.
        board_model = int(self.info.model)
        allowed = _ALLOWED_TRIGGER_MODES.get(board_model, set())
        if self._trigger_mode not in allowed:
            raise ValueError(
                f"DT{board_model}: trigger_mode '{self._trigger_mode}' not allowed. "
                f"Allowed: {sorted(allowed)}"
            )

        # Load driver once to check capabilities and ensure trigger setup
        # happens only after the driver has initialized its registers.
        driver = self.driver

        # Board-specific configuration: enable mask, post-trigger size,
        # sampling frequency/calibration and any optional trigger/offset
        # settings. All DRS4/SAM API calls are isolated in the driver so they
        # are never mixed across boards.
        driver.configure(dev, cfg)
        self.enabled_channels = list(driver.enabled_channels)

        # Now enable the trigger sources *after* all board registers are
        # configured, including TR0 threshold/offset/digitizing. This ordering
        # eliminates the enable-before-threshold bug.
        self._enable_simultaneous_triggers(dev, cfg)

        # ── Read back and verify trigger state ──
        self._verify_trigger_state(dev)

        # Shared setup: acquisition mode (software-controlled, required for the
        # SendSWtrigger -> ReadData pattern) and a modest max-events-per-BLT so
        # each transfer completes promptly.
        dev.set_acquisition_mode(AcqMode.SW_CONTROLLED)
        self._trace("set_max_num_events_blt", 64)
        self._attempt(lambda: dev.set_max_num_events_blt(64))

        # Record length is a shared call, but only meaningful where the board
        # supports it (SAM yes; DRS4 is fixed at 1024 samples).
        if driver.record_length_configurable:
            record_length = cfg.get("record_length")
            if record_length:
                self._trace("set_record_length", int(record_length))
                self._attempt(lambda: dev.set_record_length(int(record_length)))
        self._trace("get_record_length")
        self.record_length = self._attempt(dev.get_record_length) or 0

        self.input_range_vpp = _FIXED_INPUT_RANGE_VPP.get(self.info.model, 0.0)
        self.dc_offset = (
            int(cfg["dc_offset"]) if cfg.get("dc_offset") is not None else None
        )
        self.calibrated = driver.calibrated
        self.sampling_frequency_hz = float(self.driver._resolved_frequency_hz or 0.0)
        self.supports_fast_trigger = getattr(self.driver, "supports_fast_trigger", False)
        self.tr0_attenuation = int(getattr(self.driver, "tr0_attenuation", 1))

        self._trace("malloc_readout_buffer")
        readout_size = dev.malloc_readout_buffer()
        self._trace("allocate_event")
        dev.allocate_event()
        _LOG.info(
            "digitizer configured: mode=%s groups=%s channels_per_group=%s total_channels=%s "
            "enabled_channels=%s record_length=%s readout_buffer_bytes=%s input_range_vpp=%s calibrated=%s fast_trigger_supported=%s",
            self._trigger_mode,
            driver.n_groups,
            driver.channels_per_group,
            driver.n_total,
            self.enabled_channels,
            self.record_length,
            readout_size,
            self.input_range_vpp,
            self.calibrated,
            self.supports_fast_trigger,
        )
        return self.enabled_channels

    def _enable_simultaneous_triggers(self, dev, cfg: dict):
        """Configure the trigger hardware for simultaneous mode.

        Reset all trigger sources to DISABLED first, then enable only the ones
        implied by trigger_mode. Any failure is logged (no silent swallow) so
        configure() can raise instead of starting a run with the wrong trigger.
        The trigger_mode string encodes the combo.

        Rules per board:
          DT5742: random, external, fast, random-external, random-fast
          DT5743: random, external, random-external
        """
        board_model = int(self.info.model)
        trigger_mode = self._trigger_mode

        # Determine which trigger paths to enable
        has_random = trigger_mode in ("random",) or "random" in trigger_mode
        has_external = trigger_mode in ("external",) or "external" in trigger_mode
        has_fast = trigger_mode in ("fast",) or "fast" in trigger_mode

        # ── Reset ALL sources unconditionally (no try/except swallow) ──

        # External trigger input mode
        try:
            dev.set_ext_trigger_input_mode(TriggerMode.DISABLED)
            _LOG.info("DT5742: ext trigger mode -> DISABLED")
        except Exception as e:
            _LOG.warning("DT5742: failed to set ext trigger mode DISABLED (%s)", e)

        # Disable internal SW trigger
        try:
            dev.set_sw_trigger_mode(TriggerMode.DISABLED)
            _LOG.info("DT5742: sw trigger mode -> DISABLED")
        except Exception as e:
            _LOG.warning("DT5742: failed to set sw trigger mode DISABLED (%s)", e)

        # Fast/TR0 trigger mode
        try:
            dev.set_fast_trigger_mode(TriggerMode.DISABLED)
            _LOG.info("DT5742: fast trigger mode -> DISABLED")
        except Exception as e:
            _LOG.warning("DT5742: failed to set fast trigger mode DISABLED (%s)", e)

        # ── Enable only the selected sources ──

        if has_random:
            # Software trigger is always enabled when random is part of the combo;
            # periodic SendSWtrigger is driven from _do_step based on trigger_frequency_hz
            try:
                dev.set_sw_trigger_mode(TriggerMode.ACQ_ONLY)
                _LOG.info("DT5742: sw trigger mode -> ACQ_ONLY")
            except Exception as e:
                _LOG.warning("DT5742: failed to set sw trigger mode ACQ_ONLY (%s)", e)

        if has_external:
            # External trigger input mode
            try:
                dev.set_ext_trigger_input_mode(TriggerMode.ACQ_ONLY)
                _LOG.info("DT5742: ext trigger mode -> ACQ_ONLY")
            except Exception as e:
                _LOG.warning("DT5742: failed to set ext trigger mode ACQ_ONLY (%s)", e)
            # Ensure NIM I/O level for external trigger
            try:
                dev.set_io_level(IOLevel.NIM)
                _LOG.info("DT5742: I/O level set to NIM")
            except Exception as e:
                _LOG.warning("DT5742: failed to set I/O level to NIM (%s)", e)

        if has_fast and board_model == 18:
            # TR0 fast trigger is enabled - the comparator will fire when TR0
            # crosses the configured threshold. Digitization is controlled separately
            # by fast_trigger_digitizing in the cfg passed to driver.configure().
            try:
                dev.set_fast_trigger_mode(TriggerMode.ACQ_ONLY)
                _LOG.info("DT5742: fast trigger mode -> ACQ_ONLY")
            except Exception as e:
                _LOG.warning("DT5742: failed to set fast trigger mode ACQ_ONLY (%s)", e)

    @staticmethod
    def _desired_trigger_state(trigger_mode: str, board_model: int) -> dict:
        """Resolve the expected enable state of every trigger source for a
        trigger_mode string. Single source of truth for both enabling and
        verification: anything not selected must be DISABLED."""
        has_random = "random" in trigger_mode
        has_external = "external" in trigger_mode
        has_fast = "fast" in trigger_mode and board_model == 18
        return {
            "ext": TriggerMode.ACQ_ONLY if has_external else TriggerMode.DISABLED,
            "sw": TriggerMode.ACQ_ONLY if has_random else TriggerMode.DISABLED,
            "fast": TriggerMode.ACQ_ONLY if has_fast else TriggerMode.DISABLED,
        }

    def _verify_trigger_state(self, dev):
        """Read every trigger-source mode back from the board, compare it to
        what trigger_mode requires, and raise if they disagree. Also records
        the effective post-trigger, I/O level, self-trigger state and TR0
        settings for the run metadata. A run that cannot prove its trigger
        sources must not start, so a mismatch raises RuntimeError."""
        board_model = int(self.info.model)
        desired = self._desired_trigger_state(self._trigger_mode, board_model)

        def _read(fn):
            try:
                return fn()
            except Exception as e:  # a getter failing is itself a problem
                _LOG.warning("trigger readback call %s failed (%s)",
                             getattr(fn, "__name__", fn), e)
                return None

        actual = {
            "ext": _read(dev.get_ext_trigger_input_mode),
            "sw": _read(dev.get_sw_trigger_mode),
        }
        if board_model == 18:
            actual["fast"] = _read(dev.get_fast_trigger_mode)
        else:
            actual["fast"] = TriggerMode.DISABLED

        # Post-trigger / IO level / TR0 digitizing (diagnostic, not a mismatch).
        self._post_trigger_size = _read(dev.get_post_trigger_size) or 0
        io_level = _read(dev.get_io_level)
        digitizing = getattr(self.driver, "fast_trigger_digitizing", False)
        if io_level is None:
            _LOG.warning("DT5742: failed to read I/O level")
            io_level_str = "UNKNOWN"
        else:
            io_level_str = "NIM" if io_level == IOLevel.NIM else (
                "TTL" if io_level == IOLevel.TTL else str(io_level)
            )

        # Self-trigger state (diagnostic only; not forced, because DT5742
        # requires individual trigger, and DT5743 legitimately uses it).
        ch_self = 0
        grp_self = 0
        if board_model == 18:
            try:
                ch_self = int(dev.get_channel_self_trigger(0))
            except Exception as e:
                _LOG.debug("channel self-trigger readback failed (%s)", e)
        else:
            try:
                grp_self = int(dev.get_group_self_trigger(0))
            except Exception as e:
                _LOG.debug("group self-trigger readback failed (%s)", e)

        # ── Compare and raise on mismatch ──
        problems: list[str] = []
        for key in ("ext", "sw", "fast"):
            got = actual.get(key)
            want = desired[key]
            if got != want:
                problems.append(
                    f"{key} trigger: wanted {_TRIGGER_MODE_NAMES.get(want, want)}, "
                    f"got {_TRIGGER_MODE_NAMES.get(got, got)}"
                )
        if problems:
            raise RuntimeError(
                "trigger source verification failed for mode "
                f"'{self._trigger_mode}': " + "; ".join(problems)
            )

        self._trigger_state = {
            "ext": _TRIGGER_MODE_NAMES.get(actual["ext"], str(actual["ext"])),
            "sw": _TRIGGER_MODE_NAMES.get(actual["sw"], str(actual["sw"])),
            "fast": _TRIGGER_MODE_NAMES.get(actual["fast"], str(actual["fast"])),
            "io_level": io_level_str,
            "post_trigger_size": str(int(self._post_trigger_size)),
            "ch_self_trigger": f"0x{ch_self:04X}",
            "grp_self_trigger": f"0x{grp_self:04X}",
            "digitizing": "ON" if digitizing else "OFF",
            "thresholds": [
                f"0x{v:04X}" for v in (self.driver.fast_trigger_thresholds or [])
            ],
            "dc_offsets": [
                f"0x{v:04X}" for v in (self.driver.fast_trigger_dc_offsets or [])
            ],
        }

        if (ch_self or grp_self) and board_model == 27:
            _LOG.warning(
                "trigger self-trigger state unexpected: ch=0x%04X grp=0x%04X",
                ch_self, grp_self,
            )

        _LOG.info(
            "trigger state verified: ext=%s sw=%s fast=%s ch_self=0x%04X "
            "grp_self=0x%04X io=%s post=%s digitizing=%s thresholds=%s "
            "dc_offsets=%s",
            self._trigger_state["ext"],
            self._trigger_state["sw"],
            self._trigger_state["fast"],
            ch_self,
            grp_self,
            self._trigger_state["io_level"],
            self._trigger_state["post_trigger_size"],
            self._trigger_state["digitizing"],
            self._trigger_state["thresholds"],
            self._trigger_state["dc_offsets"],
        )

    async def configure(self, cfg: dict):
        return await self._run(self._do_configure, cfg)

    def _attempt(self, fn: Callable[[], int]):
        try:
            return fn()
        except Error:
            return None

    def _trace(self, name: str, *args) -> None:
        """Debug-trace a CAEN C call just before invoking it. The last trace
        line before a glibc 'malloc(): corrupted top size' crash identifies the
        exact C call that corrupted the heap (glibc reports late, so without
        this the crash log points at the wrong line)."""
        _LOG.debug("CAEN call %s%r", name, tuple(args) if args else "")

    # ── per-point acquisition ───────────────────────────────────

    def _do_begin_point(
        self,
        run_dir: str,
        point_index: int,
        cfg: dict,
        hv_channels: list[dict],
        run_id: int,
    ):
        self._run_dir = run_dir
        self._point_index = point_index
        self._run_id = run_id
        self._hv_channels = hv_channels
        self._target = int(cfg.get("number_of_triggers", 0))
        self._trigger_mode = cfg.get("trigger_mode", "random")
        # SW trigger interval for random trigger (used when random is part of combo)
        freq = float(cfg.get("trigger_frequency_hz", 1.0))
        has_random = "random" in self._trigger_mode
        has_external = "external" in self._trigger_mode
        has_fast = "fast" in self._trigger_mode
        has_physical = has_external or has_fast
        self._has_random = has_random
        self._has_physical = has_physical
        # 2-trigger mode: random events are recorded but only physical
        # (fast/external) triggers advance the goal.
        self._count_physical_only = has_random and has_physical
        self._sw_interval = (
            (1.0 / freq) if (has_random and freq > 0) else None
        )

        self._events = []
        self._timestamps = []
        self._time_tags = []
        self._start_cells = []
        self._tdcs = []
        self._peaks = []
        self._baselines = []
        self._charges = []
        self._waveforms = []
        self._tr0_waveforms = []
        self._trigger_sources = []  # per-event trigger attribution (always "mixed" in simultaneous)
        self._collected = 0
        self._physical_collected = 0
        self._random_collected = 0
        self._sw_triggers_sent = 0
        self._skipped_events = 0
        self._read_attempts = 0
        self._point_started = time.monotonic()
        self._last_collect_at = self._point_started
        self._stall_warned = False
        self._last_milestone = -1

        dev = self.device
        self._trace("clear_data")
        dev.clear_data()
        self._trace("sw_start_acquisition")
        dev.sw_start_acquisition()
        self._last_sw = time.monotonic()

        _LOG.info(
            "run=%s point=%s: acquisition started mode=%s target=%s freq=%s sw_interval=%s",
            run_id,
            point_index,
            self._trigger_mode,
            self._target,
            freq,
            self._sw_interval,
        )

    async def begin_point(
        self,
        run_dir: str,
        point_index: int,
        cfg: dict,
        hv_channels: list[dict],
        run_id: int,
    ):
        return await self._run(
            self._do_begin_point, run_dir, point_index, cfg, hv_channels, run_id
        )

    def _do_pause_acquisition(self):
        """Stop acquisition while paused to prevent FIFO overflow from hardware
        triggers. Resume will restart acquisition via begin_point."""
        if self.device is None:
            return
        try:
            self._trace("sw_stop_acquisition (pause)")
            self.device.sw_stop_acquisition()
        except Error:
            pass

    async def pause_acquisition(self):
        """Stop acquisition while the scan is paused."""
        await self._run(self._do_pause_acquisition)

    def _update_counts(self) -> None:
        """Recompute goal and random event counts from the raw totals.

        Every software trigger produces one event, so random events add one to
        both ``_collected`` and ``_sw_triggers_sent`` and cancel out, while
        physical events only add to ``_collected``. If the board drops or
        coalesces a software trigger the physical count lags (the point takes
        slightly longer) but can never stop early on random triggers.
        """
        if self._count_physical_only:
            self._physical_collected = max(0, self._collected - self._sw_triggers_sent)
            self._random_collected = self._collected - self._physical_collected
        elif self._has_random and not self._has_physical:
            self._physical_collected = 0
            self._random_collected = self._collected
        else:
            self._physical_collected = self._collected
            self._random_collected = 0

    def _do_step(self) -> dict:
        dev = self.device
        now = time.monotonic()

        read_now = True
        # In simultaneous mode, software triggers fire whenever "random" is active
        # (governed by trigger_frequency_hz). External and fast events arrive at any time.
        if "random" in self._trigger_mode and self._sw_interval is not None:
            if now - self._last_sw >= self._sw_interval:
                due = int((now - self._last_sw) / self._sw_interval)
                due = max(1, min(due, _MAX_TRIGGERS_PER_STEP))
                t_sw = time.monotonic()
                for _ in range(due):
                    self._trace("send_sw_trigger")
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
            # No random component; in simultaneous mode the FIFO could be empty
            # if only external/fast sources are present. Check the event-ready
            # bit (status register 0x8104, bit 3) before reading to avoid blocking
            # indefinitely on an empty FIFO.
            try:
                status = dev.read_register(_STATUS_REGISTER)
                read_now = bool((int(status) >> _STATUS_EVENT_READY_BIT) & 1)
            except Error:
                # If the register read fails, skip this step and retry next iteration.
                read_now = False

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
            self._trace("read_data", _READ_MODE)
            dev.read_data(_READ_MODE)
            t_read = time.monotonic() - t0
            self._trace("get_num_events")
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
            self._trace("get_event_info", i)
            info, buf = dev.get_event_info(i)
            self._trace("decode_event", i)
            evt = dev.decode_event(buf)
            extracted = self._extract_event(evt)
            if extracted is None:
                self._skipped_events += 1
                continue
            waveforms, scalars, tr0 = extracted
            self._events.append(self._collected)
            self._timestamps.append(datetime.now(UTC).timestamp())
            self._time_tags.append(info.trigger_time_tag)
            self._start_cells.append(scalars["start_cell"])
            self._tdcs.append(scalars["tdc"])
            self._peaks.append(scalars["peak"])
            self._baselines.append(scalars["baseline"])
            self._charges.append(scalars["charge"])
            self._waveforms.append(waveforms)
            self._tr0_waveforms.append(tr0)
            self._collected += 1
            # In simultaneous mode every event is tagged "mixed"; TR0 digitization
            # (tr0_g0/tr0_g1) is stored separately and can be used post-acquisition
            # to differentiate fast vs random triggers.
            self._trigger_sources.append("mixed")
            self._update_counts()
            if self._target > 0 and self._physical_collected >= self._target:
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
            # SW triggers may have been sent without producing events, so
            # refresh the derived counts before reporting progress.
            self._update_counts()
            progress = self._physical_collected / self._target
            milestone = int(progress * (_MILESTONE_STEPS - 1))
            if milestone != self._last_milestone:
                self._last_milestone = milestone
                _LOG.info(
                    "run=%s point=%s: progress %.0f%% (physical=%s/%s random=%s total=%s) "
                    "triggers sent=%s skipped=%s elapsed=%.1fs",
                    self._run_id,
                    self._point_index,
                    progress * 100,
                    self._physical_collected,
                    self._target,
                    self._random_collected,
                    self._collected,
                    self._sw_triggers_sent,
                    self._skipped_events,
                    time.monotonic() - self._point_started,
                )

        if not self._stall_warned and now - self._last_collect_at > _STALL_LOG_SECONDS:
            self._stall_warned = True
            _LOG.warning(
                "run=%s point=%s: no new events collected for %.0fs (physical=%s/%s "
                "random=%s total=%s triggers sent=%s mode=%s) - scan may never finish",
                self._run_id,
                self._point_index,
                now - self._last_collect_at,
                self._physical_collected,
                self._target,
                self._random_collected,
                self._collected,
                self._sw_triggers_sent,
                self._trigger_mode,
            )

        return {
            "collected": (
                self._physical_collected
                if self._count_physical_only
                else self._collected
            ),
            "physical_collected": self._physical_collected,
            "random_collected": self._random_collected,
            "total_collected": self._collected,
            "sw_triggers_sent": self._sw_triggers_sent,
            "count_physical_only": self._count_physical_only,
            "target": self._target,
        }

    async def step(self) -> dict:
        """One readout iteration (runs on a worker thread)."""
        return await self._run(self._do_step)

    def _do_end_point(self) -> dict:
        dev = self.device
        try:
            self._trace("sw_stop_acquisition")
            dev.sw_stop_acquisition()
        except Error:
            pass

        path = os.path.join(self._run_dir, f"digitizer_point_{self._point_index}.root")
        self._update_counts()
        self._write_root(path)
        _LOG.info(
            "run=%s point=%s: acquisition ended physical=%s/%s random=%s total=%s "
            "(skipped=%s, triggers sent=%s) -> %s",
            self._run_id,
            self._point_index,
            self._physical_collected,
            self._target,
            self._random_collected,
            self._collected,
            self._skipped_events,
            self._sw_triggers_sent,
            path,
        )
        return {
            "n_events": self._collected,
            "physical_collected": self._physical_collected,
            "random_collected": self._random_collected,
            "path": path,
        }

    async def end_point(self) -> dict:
        """Stop acquisition and write the per-point ROOT file (on a worker thread)."""
        return await self._run(self._do_end_point)

    # ── event handling ──────────────────────────────────────────

    def _extract_event(self, evt):
        """Return (waveforms: list[np.ndarray] aligned to enabled_channels, scalars: dict) or None."""
        return self.driver.extract(evt)

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
            "trigger_source": np.asarray(self._trigger_sources, dtype="<U16"),
        }
        for idx, ch in enumerate(self.enabled_channels):
            arr = np.full((n, rec_len), np.nan, dtype=np.float32)
            for e in range(n):
                w = self._waveforms[e][idx]
                if w is not None:
                    arr[e, : w.shape[0]] = w[:rec_len]
            data[f"ch{ch}"] = arr

        # Drop scalar branches the board model does not provide / the analysis does not use.
        board_model = int(self.info.model)
        for name in _UNUSED_SCALAR_BRANCHES.get(BoardModel(board_model), ()):
            data.pop(name, None)

        # TR0 (fast trigger digitizing) waveforms per acquisition group. Only
        # written when the driver actually enabled digitization, so a config
        # with fast_trigger_digitizing=false/None produces no tr0_g* branches.
        if self._tr0_waveforms and getattr(self.driver, "fast_trigger_digitizing", False):
            for g in range(self.driver.n_groups):
                arr = np.full((n, rec_len), np.nan, dtype=np.float32)
                for e in range(n):
                    w = None
                    if self._tr0_waveforms[e] is not None and g < len(self._tr0_waveforms[e]):
                        w = self._tr0_waveforms[e][g]
                    if w is not None:
                        arr[e, : w.shape[0]] = w[:rec_len]
                data[f"tr0_g{g}"] = arr

        meta_fields = {
            "run_id": int(self._run_id),
            "point_index": int(self._point_index),
            "digitizer_model": str(self.info.model_name),
            "board_model": int(self.info.model),
            "serial_number": int(self.info.serial_number),
            "family_code": int(self.info.family_code),
            "firmware_code": int(self.info.firmware_code),
            "adc_n_bits": int(self.info.adc_n_bits),
            "n_channels": int(self.driver.n_total),
            "channels_used": [int(c) for c in self.enabled_channels],
            "record_length": int(rec_len),
            "trigger_mode": self._trigger_mode,
            "trigger_ext_mode": self._trigger_state.get("ext", ""),
            "trigger_sw_mode": self._trigger_state.get("sw", ""),
            "trigger_fast_mode": self._trigger_state.get("fast", ""),
            "trigger_io_level": self._trigger_state.get("io_level", ""),
            "trigger_post_trigger_size": int(self._trigger_state.get("post_trigger_size", 0)),
            "trigger_ch_self_trigger": self._trigger_state.get("ch_self_trigger", ""),
            "trigger_grp_self_trigger": self._trigger_state.get("grp_self_trigger", ""),
            "trigger_fast_digitizing": self._trigger_state.get("digitizing", ""),
            "trigger_fast_thresholds": self._trigger_state.get("thresholds", []),
            "trigger_fast_dc_offsets": self._trigger_state.get("dc_offsets", []),
            "number_of_triggers": int(self._target),
            "physical_collected": int(self._physical_collected),
            "random_collected": int(self._random_collected),
            "total_collected": int(self._collected),
            "sw_triggers_sent": int(self._sw_triggers_sent),
            "count_physical_only": bool(self._count_physical_only),
            "input_range_vpp": float(self.input_range_vpp or 0.0),
            "tr0_attenuation": int(self.tr0_attenuation),
            "tr0_input_range_vpp": (
                float(self.tr0_attenuation * (self.input_range_vpp or 0.0))
                if self.supports_fast_trigger
                else 0.0
            ),
            "dc_offset": int(self.dc_offset or 0),
            "sampling_frequency_hz": float(self.sampling_frequency_hz or 0.0),
            "dc_offset_v": (
                ((float(self.dc_offset) - 0x8000) / 0x8000 * (self.input_range_vpp / 2))
                if self.dc_offset is not None
                else 0.0
            ),
            "calibrated": 1 if self.calibrated else 0,
            "connection_type": int(self.connection_type),
        }
        if self._hv_channels:
            meta_fields["hv_slot"] = [int(c["slot"]) for c in self._hv_channels]
            meta_fields["hv_channel"] = [int(c["channel"]) for c in self._hv_channels]
            meta_fields["hv_voltage"] = [float(c["voltage"]) for c in self._hv_channels]

        meta = _meta_to_awkward(meta_fields)

        # uproot >= 5.7 writes RNTuples for dict-style assignment
        # (``f[name] = data``). Write the digitizer TTree explicitly to keep
        # the established layout and to avoid the RNTuple writer failing on
        # empty list fields (e.g. fast-trigger settings on boards that do not
        # support it). The meta tree stays an RNTuple because the TTree writer
        # cannot represent ``var * string`` (the fast-trigger hex strings).
        with uproot.recreate(path) as f:
            f.mktree("digitizer", data)
            f["meta"] = meta
