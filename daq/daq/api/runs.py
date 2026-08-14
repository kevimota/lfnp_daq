import asyncio

from fastapi import APIRouter, HTTPException

from caen_libs._caenhvwrappertypes import SystemType, LinkType

from ..core.db import get_session, DAQConfigurationDB, CaenPS, CaenDigitizer, DAQRuns
from ..core.fsm import DAQFSM, DAQState
from ..core.websocket import DataBroadcaster
from ..models import DAQStatusResponse
from ..hardware import CaenPSInterface, DigitizerScanner
from ..scans import SCAN_TYPES
from . import ScanContext, data_writer, scan_manager, _background_scan

router = APIRouter()

#: Valid sampling frequencies (Hz) per digitizer board model (18 = DT5742 DRS4,
#: 27 = DT5743 SAMLONG). Mirrors backend/app/routes/daq.py.
_DIGITIZER_SAMPLING_FREQUENCIES = {
    18: {5_000_000_000, 2_500_000_000, 1_000_000_000, 750_000_000},
    27: {3_200_000_000, 1_600_000_000, 800_000_000, 400_000_000},
}

#: Number of channels per digitizer board model (DT5742: 2 groups x 8,
#: DT5743: 4 groups x 2).
_DIGITIZER_CHANNEL_COUNTS = {18: 16, 27: 8}


def _validate_digitizer_board_specific(config_row, dig_row) -> None:
    """Validate board-specific digitizer config (sampling frequency, post-trigger
    size, channel range) against the digitizer board model."""
    board_model = dig_row.board_model
    if board_model not in _DIGITIZER_CHANNEL_COUNTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported digitizer board_model {board_model}: only "
                "DT5742 and DT5743 are supported"
            ),
        )

    if config_row.sampling_frequency_hz is not None:
        freqs = _DIGITIZER_SAMPLING_FREQUENCIES[board_model]
        if round(float(config_row.sampling_frequency_hz)) not in freqs:
            allowed = ", ".join(f"{f / 1e9:g} GS/s ({f} Hz)" for f in sorted(freqs))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"sampling_frequency_hz {config_row.sampling_frequency_hz} not "
                    f"supported for board_model {board_model}: allowed values "
                    f"are {allowed}"
                ),
            )

    if config_row.post_trigger_size is not None and not (0 <= config_row.post_trigger_size <= 100):
        raise HTTPException(
            status_code=400,
            detail="post_trigger_size must be a percentage 0-100",
        )

    if config_row.channels:
        n_channels = _DIGITIZER_CHANNEL_COUNTS[board_model]
        for ch in config_row.channels:
            if ch.get("enabled") and not (0 <= int(ch["channel"]) < n_channels):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"channel {ch['channel']} out of range for board_model "
                        f"{board_model} (0..{n_channels - 1})"
                    ),
                )


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
async def start_scan(run_id: int):
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

        if not config_row.power_supply:
            raise HTTPException(
                status_code=400, detail="Configuration has no power supply assigned"
            )

        ps_row = session.get(CaenPS, config_row.power_supply)
        if not ps_row:
            raise HTTPException(
                status_code=404,
                detail=f"Power supply (id={config_row.power_supply}) not found",
            )

        raw_points = config_row.voltage_points or []
        config = {
            "type": config_row.type or "hv_scan",
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

        scan_type = config_row.type or "hv_scan"
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
            if config_row.trigger_mode not in ("random", "external"):
                raise HTTPException(
                    status_code=400,
                    detail="Digitizer scan trigger_mode must be 'random' or 'external'",
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
            _validate_digitizer_board_specific(config_row, dig_row)
            digitizer_scanner = DigitizerScanner(dig_row)
            config["digitizer_id"] = config_row.digitizer_id
            config["trigger_mode"] = config_row.trigger_mode
            config["trigger_frequency_hz"] = config_row.trigger_frequency_hz
            config["sampling_frequency_hz"] = config_row.sampling_frequency_hz
            config["number_of_triggers"] = config_row.number_of_triggers
            config["record_length"] = config_row.record_length
            config["post_trigger_size"] = config_row.post_trigger_size
            config["input_range_vpp"] = config_row.input_range_vpp
            config["channels"] = config_row.channels or []

    system_type = SystemType(ps_row.system_type)
    link_type = LinkType(ps_row.link_type)
    power_interface = CaenPSInterface(
        system_type, link_type, ps_row.arg, ps_row.username, ps_row.password
    )

    fsm = DAQFSM()
    fsm.initialize()
    broadcaster = DataBroadcaster()
    scanner_cls = SCAN_TYPES[scan_type]
    if scan_type == "digitizer_scan":
        scanner = scanner_cls(fsm, power_interface, data_writer, broadcaster, digitizer=digitizer_scanner)
    else:
        scanner = scanner_cls(fsm, power_interface, data_writer, broadcaster)
    task = asyncio.create_task(_background_scan(config, run_id, ps_row))

    ctx = ScanContext(
        fsm=fsm,
        power_interface=power_interface,
        scanner=scanner,
        task=task,
        broadcaster=broadcaster,
    )
    scan_manager[run_id] = ctx

    return {"success": True, "run_id": run_id, "message": "Scan started"}


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