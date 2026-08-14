from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlmodel import select, func
from typing import List, Optional
from datetime import datetime, UTC
from ..routes.users import get_current_user
import httpx
import math

from ..core.config import config
from ..core.db import SessionDep
from ..models.daq import (
    DAQConfiguration,
    DAQRuns,
    DAQConfigResponse,
    DAQRunResponse,
    PaginatedRunsResponse,
    RunCreateRequest,
    RunUpdateRequest,
    RunActionResponse,
)

router = APIRouter(prefix="/daq", tags=["DAQ"])


# ── Check daq health ────────────────────────────────────────────


@router.get("/health")
async def health_check():
    daq_url = config.DAQ_URL
    url = f"{daq_url}/health"

    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            detail = "DAQ error"
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()


# ── Configurations (read-only browse) ───────────────────────────


@router.get("/configs", response_model=List[DAQConfigResponse])
def list_configurations(session: SessionDep):
    return session.exec(select(DAQConfiguration)).all()


@router.get("/configs/{config_id}", response_model=DAQConfigResponse)
def get_configuration(config_id: int, session: SessionDep):
    config = session.get(DAQConfiguration, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")
    return config


@router.put("/configs/{config_id}", dependencies=[Depends(get_current_user)])
def update_configuration(config_id: int, req: RunCreateRequest, session: SessionDep):
    config = session.get(DAQConfiguration, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")
    _validate_scan_params(req, session)
    config.type = req.type or config.type
    config.voltage_points = req.voltage_points
    config.wait_time_seconds = req.wait_time_seconds
    config.sample_interval_seconds = req.sample_interval_seconds
    config.number_of_samples = req.number_of_samples
    config.end_voltage = req.end_voltage
    config.power_supply = req.power_supply
    config.digitizer_id = req.digitizer_id
    config.trigger_mode = req.trigger_mode
    config.trigger_frequency_hz = req.trigger_frequency_hz
    config.sampling_frequency_hz = req.sampling_frequency_hz
    config.number_of_triggers = req.number_of_triggers
    config.record_length = req.record_length
    config.post_trigger_size = req.post_trigger_size
    config.input_range_vpp = req.input_range_vpp
    config.channels = req.channels
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


@router.delete("/configs/{config_id}", dependencies=[Depends(get_current_user)])
def delete_configuration(config_id: int, session: SessionDep):
    config = session.get(DAQConfiguration, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    runs = session.exec(
        select(DAQRuns).where(DAQRuns.configuration_id == config_id)
    ).all()
    if runs:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete configuration: {len(runs)} run(s) reference it",
        )

    session.delete(config)
    session.commit()
    return {"success": True, "message": "Configuration deleted"}


# ── Runs ────────────────────────────────────────────────────────

_ACTIVE_RUN_STATUSES = {"running"}

#: Valid sampling frequencies (Hz) per digitizer board model (18 = DT5742 DRS4,
#: 27 = DT5743 SAMLONG).
_DIGITIZER_SAMPLING_FREQUENCIES = {
    18: {5_000_000_000, 2_500_000_000, 1_000_000_000, 750_000_000},
    27: {3_200_000_000, 1_600_000_000, 800_000_000, 400_000_000},
}

#: Number of channels per digitizer board model (DT5742: 2 groups x 8,
#: DT5743: 4 groups x 2).
_DIGITIZER_CHANNEL_COUNTS = {18: 16, 27: 8}


async def _proxy_to_daq(run_id: int, action: str, method: str = "POST") -> dict:
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/{action}"
    try:
        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.post(url)
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=503,
            detail=f"DAQ service did not respond for run {run_id} within 60s (it may be busy recording)",
        )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="DAQ service is unreachable",
        )
    if resp.status_code != 200:
        detail = "DAQ error"
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


def _validate_scan_params(req: RunCreateRequest, session) -> None:
    """Validate the required parameters for the given scan type."""
    from ..models.hardware import CaenDigitizer

    check_digitizer = req.type == "digitizer_scan"
    if check_digitizer:
        if not req.digitizer_id:
            raise HTTPException(
                status_code=400,
                detail="Digitizer scan requires a digitizer_id",
            )
        if req.trigger_mode not in ("random", "external"):
            raise HTTPException(
                status_code=400,
                detail="trigger_mode must be 'random' or 'external'",
            )
        if not req.number_of_triggers or req.number_of_triggers <= 0:
            raise HTTPException(
                status_code=400,
                detail="number_of_triggers must be > 0 for digitizer scan",
            )
        if req.trigger_mode == "random" and not (
            req.trigger_frequency_hz and req.trigger_frequency_hz > 0
        ):
            raise HTTPException(
                status_code=400,
                detail="trigger_frequency_hz must be > 0 for random trigger mode",
            )
        if req.sampling_frequency_hz is not None and req.sampling_frequency_hz <= 0:
            raise HTTPException(
                status_code=400,
                detail="sampling_frequency_hz must be > 0",
            )
        dig_row = session.get(CaenDigitizer, req.digitizer_id)
        if not dig_row:
            raise HTTPException(
                status_code=404,
                detail=f"Digitizer (id={req.digitizer_id}) not found",
            )

        board_model = dig_row.board_model
        if board_model not in _DIGITIZER_CHANNEL_COUNTS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported digitizer board_model {board_model}: only "
                    "DT5742 and DT5743 are supported"
                ),
            )

        if req.sampling_frequency_hz is not None:
            freqs = _DIGITIZER_SAMPLING_FREQUENCIES[board_model]
            if round(float(req.sampling_frequency_hz)) not in freqs:
                allowed = ", ".join(f"{f / 1e9:g} GS/s ({f} Hz)" for f in sorted(freqs))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"sampling_frequency_hz {req.sampling_frequency_hz} not "
                        f"supported for board_model {board_model}: allowed values "
                        f"are {allowed}"
                    ),
                )

        if req.post_trigger_size is not None and not (0 <= req.post_trigger_size <= 100):
            raise HTTPException(
                status_code=400,
                detail="post_trigger_size must be a percentage 0-100",
            )

        if req.channels:
            n_channels = _DIGITIZER_CHANNEL_COUNTS[board_model]
            for ch in req.channels:
                if ch.get("enabled") and not (0 <= int(ch["channel"]) < n_channels):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"channel {ch['channel']} out of range for board_model "
                            f"{board_model} (0..{n_channels - 1})"
                        ),
                    )
    elif req.type != "hv_scan":
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scan type: {req.type}",
        )


def _extract_channels(voltage_points: list) -> set[tuple[int, int]]:
    channels = set()
    for point in voltage_points:
        for ch in point:
            channels.add((int(ch["slot"]), int(ch["channel"])))
    return channels


@router.post(
    "/runs", response_model=DAQRunResponse, dependencies=[Depends(get_current_user)]
)
def create_run(req: RunCreateRequest, session: SessionDep):
    _validate_scan_params(req, session)
    config = DAQConfiguration(
        type=req.type or "hv_scan",
        voltage_points=req.voltage_points,
        wait_time_seconds=req.wait_time_seconds,
        sample_interval_seconds=req.sample_interval_seconds,
        number_of_samples=req.number_of_samples,
        end_voltage=req.end_voltage,
        power_supply=req.power_supply,
        digitizer_id=req.digitizer_id,
        trigger_mode=req.trigger_mode,
        trigger_frequency_hz=req.trigger_frequency_hz,
        sampling_frequency_hz=req.sampling_frequency_hz,
        number_of_triggers=req.number_of_triggers,
        record_length=req.record_length,
        post_trigger_size=req.post_trigger_size,
        input_range_vpp=req.input_range_vpp,
        channels=req.channels,
    )
    session.add(config)
    session.commit()
    session.refresh(config)

    run = DAQRuns(
        configuration_id=config.id,
        label=req.label,
        comments=req.comments,
        status="created",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("/runs", response_model=PaginatedRunsResponse)
def list_runs(
    session: SessionDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
):
    query = select(DAQRuns)
    if search:
        query = query.where(DAQRuns.label.ilike(f"%{search}%"))
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    query = query.order_by(DAQRuns.created_at.desc())
    items = session.exec(query.offset((page - 1) * per_page).limit(per_page)).all()
    return PaginatedRunsResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total else 1,
    )


@router.get("/runs/{run_id}", response_model=DAQRunResponse)
def get_run(run_id: int, session: SessionDep):
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.patch(
    "/runs/{run_id}",
    response_model=DAQRunResponse,
    dependencies=[Depends(get_current_user)],
)
def update_run(
    run_id: int,
    req: RunUpdateRequest,
    session: SessionDep,
):
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if req.label is not None:
        run.label = req.label
    if req.comments is not None:
        run.comments = req.comments

    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.delete("/runs/{run_id}", dependencies=[Depends(get_current_user)])
def delete_run(run_id: int, session: SessionDep):
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    session.delete(run)
    session.commit()
    return {"success": True, "message": "Run deleted"}


# ── Per-run control (proxy to DAQ) ──────────────────────────────


def _check_run_exists(run_id: int, session) -> DAQRuns:
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post(
    "/runs/{run_id}/start",
    response_model=RunActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def start_run(run_id: int, session: SessionDep):
    run = _check_run_exists(run_id, session)

    config = session.get(DAQConfiguration, run.configuration_id)
    if not config:
        raise HTTPException(status_code=400, detail="Run has no configuration")

    requested = _extract_channels(config.voltage_points)

    # Check conflict with other active runs
    active_runs = session.exec(
        select(DAQRuns).where(
            DAQRuns.status.in_(_ACTIVE_RUN_STATUSES),
            DAQRuns.id != run_id,
        )
    ).all()

    busy_channels = []
    conflicting_run_ids = []

    for other in active_runs:
        other_config = session.get(DAQConfiguration, other.configuration_id)
        if not other_config:
            continue
        other_channels = _extract_channels(other_config.voltage_points)
        overlap = requested & other_channels
        if overlap:
            busy_channels.extend(list(overlap))
            conflicting_run_ids.append(other.id)

    if busy_channels:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Channels already in use by another active run",
                "busy_channels": list(set(busy_channels)),
                "conflicting_run_ids": conflicting_run_ids,
            },
        )

    requested_digitizer = config.digitizer_id
    if requested_digitizer:
        conflicting_run_ids = []
        for other in active_runs:
            other_config = session.get(DAQConfiguration, other.configuration_id)
            if not other_config:
                continue
            other_digitizer = other_config.digitizer_id
            if other_digitizer == requested_digitizer:
                conflicting_run_ids.append(other.id)
        if conflicting_run_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Digitizer already in use by another active run",
                    "digitizer_id": requested_digitizer,
                    "conflicting_run_ids": conflicting_run_ids,
                },
            )

    try:
        result = await _proxy_to_daq(run_id, "start")
        run.status = "running"
        run.started_at = datetime.now(UTC)
        session.add(run)
        session.commit()
    except Exception:
        run.status = "failed"
        session.add(run)
        session.commit()
        raise

    return RunActionResponse(success=True, run_id=run_id, message="Scan started")


@router.post(
    "/runs/{run_id}/stop",
    response_model=RunActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def stop_run(run_id: int, session: SessionDep):
    run = _check_run_exists(run_id, session)
    result = await _proxy_to_daq(run_id, "stop")
    run.status = "stopped"
    run.stopped_at = datetime.now(UTC)
    session.add(run)
    session.commit()
    return RunActionResponse(
        success=True,
        run_id=run_id,
        message=result.get("message", "Stop requested"),
    )


@router.post(
    "/runs/{run_id}/pause",
    response_model=RunActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def pause_run(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    result = await _proxy_to_daq(run_id, "pause")
    return RunActionResponse(
        success=True,
        run_id=run_id,
        message=result.get("message", "Scan paused"),
    )


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def resume_run(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    result = await _proxy_to_daq(run_id, "resume")
    return RunActionResponse(
        success=True,
        run_id=run_id,
        message=result.get("message", "Scan resumed"),
    )


@router.get("/runs/{run_id}/status")
async def get_run_status(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    return await _proxy_to_daq(run_id, "status", method="GET")


@router.get("/runs/{run_id}/info")
async def get_run_info(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    return await _proxy_to_daq(run_id, "info", method="GET")


@router.get("/runs/{run_id}/log")
async def get_run_log(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/log"
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return Response(content=resp.text, media_type="text/plain")
    except Exception:
        pass
    return Response(content="", media_type="text/plain")


@router.get("/runs/{run_id}/files")
async def list_run_files(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/files"
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {"files": []}


@router.get("/runs/{run_id}/files/{filename:path}")
async def download_run_file(run_id: int, filename: str, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/files/{filename}"
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                content_type = resp.headers.get(
                    "content-type", "application/octet-stream"
                )
                return Response(
                    content=resp.content,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    },
                )
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="File not found")


@router.get("/runs/{run_id}/download")
async def download_run_archive(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/download"
    try:
        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": f'attachment; filename="run_{run_id}.zip"'
                    },
                )
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Run data not found")
