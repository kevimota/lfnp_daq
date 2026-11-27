import asyncio
import os
import re

from fastapi import APIRouter, HTTPException

from caen_libs._caenhvwrappertypes import SystemType, LinkType

from ..core.db import get_session, DAQConfigurationDB, CaenPS, CaenDigitizer, DAQRuns
from ..core.fsm import DAQFSM, DAQState
from ..core.websocket import DataBroadcaster
from ..models import DAQStatusResponse, RunStartRequest
from ..hardware import CaenPSInterface, DigitizerScanner
from ..scans import SCAN_TYPES
from . import ScanContext, data_writer, scan_manager, _background_scan
from .files import _wipe_run, invalidate_archive_cache

router = APIRouter()


def _log_fallback_progress(
    log_path: str, run_id: int, total_points: int
) -> tuple[set[int], bool]:
    """Best-effort progress for runs created before the progress manifest."""
    if not os.path.isfile(log_path):
        return set(), False
    try:
        with open(log_path) as f:
            text = f.read()
    except OSError:
        return set(), False

    marker = f"Run {run_id} started"
    idx = text.rfind(marker)
    segment = text[idx:] if idx >= 0 else text
    if "All points completed" in segment:
        return set(range(total_points)), True

    candidates = []
    for pattern in (
        r"Configuring Point (\d+)",
        r"Recording started \(point (\d+)\)",
    ):
        for match in re.finditer(pattern, segment):
            candidates.append((match.start(), int(match.group(1)) - 1))

    if not candidates:
        return set(), True

    _pos, failed_index = max(candidates)
    if failed_index < 0 or failed_index >= total_points:
        return set(range(total_points)), True
    return set(range(failed_index)), True


@router.get("/runs/{run_id}/progress")
async def get_progress(run_id: int):
    with get_session() as session:
        run_row = session.get(DAQRuns, run_id)
        if not run_row:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        config_row = session.get(DAQConfigurationDB, run_row.configuration_id)
        if not config_row:
            raise HTTPException(
                status_code=404,
                detail=f"Configuration not found for run_id {run_id}",
            )

        voltage_points = config_row.voltage_points or []
        total_points = len(voltage_points)
        scan_type = config_row.type or "hv_scan"
        run_status = run_row.status

        run_dir = os.path.join(data_writer.data_path, f"run_{run_id}")
        manifest = data_writer.read_progress(run_dir)
        manifest_points = manifest.get("points") or {}
        fallback_used = False

        completed: set[int] = set()
        point_meta: dict[int, dict] = {}

        if manifest.get("last_run") or manifest_points:
            for index in range(total_points):
                entry = manifest_points.get(str(index))
                if not isinstance(entry, dict):
                    continue
                point_meta[index] = entry
                if entry.get("status") == "completed":
                    completed.add(index)
        else:
            completed, fallback_used = _log_fallback_progress(
                os.path.join(run_dir, "daq.log"), run_id, total_points
            )
            for index in completed:
                point_meta[index] = {"status": "completed"}

        for index in range(total_points):
            primary = (
                f"digitizer_point_{index}.root"
                if scan_type == "digitizer_scan"
                else f"power_data_point_{index}.csv"
            )
            file_exists = os.path.isfile(os.path.join(run_dir, primary))
            entry = point_meta.setdefault(index, {})
            entry["file_exists"] = file_exists
            if index in completed and not file_exists:
                completed.discard(index)
                entry["status"] = "missing"

        active_point = None
        ctx = scan_manager.get(run_id)
        if ctx is not None:
            current = ctx.fsm.current_point_index
            if 0 <= current < total_points:
                active_point = current

        resume_point = next(
            (index for index in range(total_points) if index not in completed), None
        )
        failed_point = (
            resume_point if run_status in ("failed", "stopped") else None
        )

        points = []
        for index in range(total_points):
            entry = point_meta.get(index, {})
            if index in completed:
                point_status = "completed"
            elif active_point == index:
                point_status = "running"
            elif failed_point == index:
                point_status = "failed"
            else:
                point_status = "pending"

            voltages = entry.get("voltages")
            if voltages is None and index < len(voltage_points):
                voltages = voltage_points[index]

            points.append({
                "index": index,
                "number": index + 1,
                "status": point_status,
                "started_at": entry.get("started_at"),
                "completed_at": entry.get("completed_at"),
                "samples": entry.get("samples"),
                "triggers": entry.get("triggers"),
                "target": entry.get("target"),
                "random_triggers": entry.get("random_triggers"),
                "total_triggers": entry.get("total_triggers"),
                "count_physical_only": entry.get("count_physical_only"),
                "voltages": voltages or [],
                "data_files": entry.get("data_files") or [],
                "file_exists": bool(entry.get("file_exists")),
            })

        return {
            "run_id": run_id,
            "status": run_status,
            "scan_type": scan_type,
            "total_points": total_points,
            "completed_points": sorted(completed),
            "failed_point": failed_point,
            "resume_point": resume_point,
            "active_point": active_point,
            "fallback_used": fallback_used,
            "last_run": manifest.get("last_run"),
            "points": points,
        }


@router.get("/runs/{run_id}/status", response_model=DAQStatusResponse)
async def get_status(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Run not active")

    total = ctx.fsm.total_points
    current = ctx.fsm.current_point_index + 1 if ctx.fsm.current_point_index < total else 0
    hv_point = f"{current}/{total}" if total > 0 else "0/0"

    return DAQStatusResponse(
        state=ctx.fsm.get_state(),
        hv_point=hv_point,
        run_id=str(run_id),
    )


@router.get("/runs/{run_id}/info")
async def get_info(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Run not active")
    return ctx.fsm.get_info()


@router.post("/runs/{run_id}/start")
async def start_scan(run_id: int, req: RunStartRequest | None = None):
    req = req or RunStartRequest()
    if run_id in scan_manager:
        ctx = scan_manager[run_id]
        if ctx.fsm.state in (DAQState.HALTED, DAQState.FINISHED, DAQState.FAILED):
            ctx.task.cancel()
            try:
                await asyncio.wait_for(ctx.task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            scan_manager.pop(run_id, None)
        else:
            raise HTTPException(status_code=400, detail="Run already active")

    with get_session() as session:
        run_row = session.get(DAQRuns, run_id)
        if not run_row:
            raise HTTPException(
                status_code=404,
                detail=f"Run {run_id} not found",
            )

        config_row = session.get(DAQConfigurationDB, run_row.configuration_id)
        if not config_row:
            raise HTTPException(
                status_code=404,
                detail=f"Configuration not found for run_id {run_id}",
            )

        scan_type = config_row.type or "hv_scan"
        raw_points = config_row.voltage_points or []
        if not raw_points and scan_type == "digitizer_scan":
            # A digitizer-only run still needs one acquisition point.
            raw_points = [[]]
        has_hv = any(point for point in raw_points)

        if has_hv and not config_row.power_supply:
            raise HTTPException(
                status_code=400, detail="Configuration has no power supply assigned"
            )

        ps_row = None
        if config_row.power_supply:
            ps_row = session.get(CaenPS, config_row.power_supply)
            if not ps_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Power supply (id={config_row.power_supply}) not found",
                )

        config = {
            "type": scan_type,
            "voltage_points": [
                [
                    {"slot": int(ch["slot"]), "channel": int(ch["channel"]), "voltage": float(ch["voltage"])}
                    for ch in point
                ]
                for point in raw_points
            ],
            "wait_time_seconds": config_row.wait_time_seconds,
            "sample_interval_seconds": config_row.sample_interval_seconds,
            "number_of_samples": config_row.number_of_samples,
            "end_voltage": config_row.end_voltage,
        }

        total_points = len(config["voltage_points"])
        if total_points == 0:
            raise HTTPException(
                status_code=400, detail="Configuration has no voltage points"
            )
        if scan_type == "hv_scan" and any(
            not point for point in config["voltage_points"]
        ):
            raise HTTPException(
                status_code=400,
                detail="HV scan requires at least one HV channel in every point",
            )

        if req.points is not None:
            if not req.points:
                raise HTTPException(
                    status_code=400, detail="points must not be empty"
                )
            run_points = sorted({int(i) for i in req.points})
            if run_points[0] < 0 or run_points[-1] >= total_points:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"points must be between 0 and {total_points - 1}"
                    ),
                )
            mode = "redo"
        elif req.start_point is not None:
            start_point = int(req.start_point)
            if not 0 <= start_point < total_points:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"start_point must be between 0 and {total_points - 1}"
                    ),
                )
            run_points = list(range(start_point, total_points))
            mode = "resume" if start_point > 0 else "start"
        else:
            run_points = list(range(total_points))
            mode = "start"

        if scan_type not in SCAN_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scan type: {scan_type}",
            )

        digitizer_scanner = None
        if scan_type == "digitizer_scan":
            if not config_row.digitizer_id:
                raise HTTPException(
                    status_code=400,
                    detail="Digitizer scan has no digitizer configured",
                )
            dig_row = session.get(CaenDigitizer, int(config_row.digitizer_id))
            if not dig_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Digitizer (id={config_row.digitizer_id}) not found",
                )
            if dig_row.board_model == 27:  # DT5743: fast trigger not supported
                allowed_trigger_modes = ("random", "external", "random-external")
            else:
                allowed_trigger_modes = (
                    "random", "external", "fast", "random-external", "random-fast",
                )
            if config_row.trigger_mode not in allowed_trigger_modes:
                allowed_str = ", ".join(f'"{m}"' for m in allowed_trigger_modes)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"trigger_mode '{config_row.trigger_mode}' not allowed. "
                        f"Allowed values: {allowed_str}"
                    ),
                )
            if not config_row.number_of_triggers or config_row.number_of_triggers <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Digitizer scan number_of_triggers must be > 0",
                )
            if config_row.trigger_mode == "random" and not (
                config_row.trigger_frequency_hz and config_row.trigger_frequency_hz > 0
            ):
                raise HTTPException(
                    status_code=400,
                    detail="trigger_frequency_hz must be > 0 for random trigger mode",
                )
            digitizer_scanner = DigitizerScanner(dig_row)
            config["digitizer_id"] = config_row.digitizer_id
            config["trigger_mode"] = config_row.trigger_mode
            config["trigger_frequency_hz"] = config_row.trigger_frequency_hz
            config["sampling_frequency_hz"] = config_row.sampling_frequency_hz
            config["number_of_triggers"] = config_row.number_of_triggers
            config["record_length"] = config_row.record_length
            config["post_trigger_size"] = config_row.post_trigger_size
            config["channels"] = config_row.channels or []
            if config_row.dc_offset is not None:
                if not (0 <= config_row.dc_offset <= 0xFFFF):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"dc_offset must be a 16-bit DAC value 0-65535, "
                            f"got {config_row.dc_offset}"
                        ),
                    )
                config["dc_offset"] = config_row.dc_offset
            # TR0 digitization only makes sense when a fast trigger is active.
            # Force it off for random / external / random-external modes
            # regardless of what was persisted, so the board never digitizes
            # TR0 needlessly.
            fast_mode = bool(config_row.trigger_mode and "fast" in config_row.trigger_mode)
            if not fast_mode:
                config["fast_trigger_digitizing"] = False
            elif config_row.fast_trigger_digitizing is not None:
                config["fast_trigger_digitizing"] = config_row.fast_trigger_digitizing
            else:
                config["fast_trigger_digitizing"] = True
            if config_row.fast_trigger_threshold is not None:
                config["fast_trigger_thresholds"] = [config_row.fast_trigger_threshold] * 2
            if config_row.fast_trigger_dc_offset is not None:
                config["fast_trigger_dc_offsets"] = [config_row.fast_trigger_dc_offset] * 2

    power_interface = None
    if ps_row is not None:
        system_type = SystemType(ps_row.system_type)
        link_type = LinkType(ps_row.link_type)
        power_interface = CaenPSInterface(
            system_type, link_type, ps_row.arg, ps_row.username, ps_row.password
        )

    fsm = DAQFSM()
    fsm.initialize()
    # A (re)start rewrites/extends the run directory, so any cached archive is
    # now stale. Drop it before new data is written.
    invalidate_archive_cache(run_id)
    broadcaster = DataBroadcaster()
    scanner_cls = SCAN_TYPES[scan_type]
    if scan_type == "digitizer_scan":
        scanner = scanner_cls(fsm, power_interface, data_writer, broadcaster, digitizer=digitizer_scanner)
    else:
        scanner = scanner_cls(fsm, power_interface, data_writer, broadcaster)
    task = asyncio.create_task(
        _background_scan(config, run_id, ps_row, run_points=run_points, mode=mode)
    )

    ctx = ScanContext(
        fsm=fsm,
        power_interface=power_interface,
        scanner=scanner,
        task=task,
        broadcaster=broadcaster,
    )
    scan_manager[run_id] = ctx

    if mode == "redo":
        message = f"Redo started for {len(run_points)} point(s)"
    elif mode == "resume":
        message = f"Resumed from point {run_points[0] + 1}"
    else:
        message = "Scan started"

    return {"success": True, "run_id": run_id, "message": message}


@router.post("/runs/{run_id}/stop")
async def stop_scan(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Run not active")

    if ctx.fsm.state not in [
        DAQState.CONFIGURING,
        DAQState.WAITING,
        DAQState.RECORDING,
        DAQState.PAUSED,
    ]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop from state: {ctx.fsm.get_state()}",
        )

    ctx.scanner.stop()
    return {"success": True, "message": "Stop requested"}


@router.post("/runs/{run_id}/pause")
async def pause_scan(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Run not active")

    if ctx.fsm.state not in [DAQState.CONFIGURING, DAQState.WAITING, DAQState.RECORDING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause from state: {ctx.fsm.get_state()}",
        )

    ctx.fsm.pause()
    return {"success": True, "message": "Scan paused"}


@router.post("/runs/{run_id}/resume")
async def resume_scan(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Run not active")

    if ctx.fsm.state != DAQState.PAUSED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume from state: {ctx.fsm.get_state()}",
        )

    ctx.fsm.resume()
    return {"success": True, "message": "Scan resumed, redoing current point"}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int):
    ctx = scan_manager.pop(run_id, None)
    if ctx is not None:
        # Best effort stop before wiping the run directory.
        try:
            ctx.scanner.stop()
        except Exception:
            pass

    run_dir = os.path.join(data_writer.data_path, f"run_{run_id}")
    _wipe_run(run_dir, run_id)
    return {"success": True, "message": "Run deleted"}

