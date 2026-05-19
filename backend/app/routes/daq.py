from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlmodel import select, func
from typing import List, Optional
from datetime import datetime, UTC
from ..routes.users import get_current_user
import httpx
import math

from ..core.config import settings
from ..core.db import SessionDep
from ..models.daq import (
    DAQConfiguration,
    DAQRuns,
    DAQConfigResponse,
    DAQRunResponse,
    PaginatedRunsResponse,
    RunCreateRequest,
    RunActionResponse,
)

router = APIRouter(prefix="/daq")


# ── Check daq health ────────────────────────────────────────────


@router.get("/health")
async def health_check():
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/health"

    async with httpx.AsyncClient(timeout=10) as client:
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
    config.voltage_points = req.voltage_points
    config.wait_time_seconds = req.wait_time_seconds
    config.sample_interval_seconds = req.sample_interval_seconds
    config.number_of_samples = req.number_of_samples
    config.end_voltage = req.end_voltage
    config.power_supply = req.power_supply
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


async def _proxy_to_daq(run_id: int, action: str, method: str = "POST") -> dict:
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/{action}"
    async with httpx.AsyncClient(timeout=10) as client:
        if method == "GET":
            resp = await client.get(url)
        else:
            resp = await client.post(url)
        if resp.status_code != 200:
            detail = "DAQ error"
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()


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
    config = DAQConfiguration(
        type="hv_scan",
        voltage_points=req.voltage_points,
        wait_time_seconds=req.wait_time_seconds,
        sample_interval_seconds=req.sample_interval_seconds,
        number_of_samples=req.number_of_samples,
        end_voltage=req.end_voltage,
        power_supply=req.power_supply,
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
    status: str,
    data_path: Optional[str] = None,
    session: SessionDep = None,
):
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run.status = status
    if data_path:
        run.data_path = data_path
    if status == "running" and not run.started_at:
        run.started_at = datetime.now(UTC)
    if status in ["finished", "completed", "stopped", "failed"]:
        run.stopped_at = datetime.now(UTC)

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
    _check_run_exists(run_id, session)
    result = await _proxy_to_daq(run_id, "stop")
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
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/log"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return Response(content=resp.text, media_type="text/plain")
    except Exception:
        pass
    return Response(content="", media_type="text/plain")


@router.get("/runs/{run_id}/files")
async def list_run_files(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/files"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {"files": []}


@router.get("/runs/{run_id}/files/{filename:path}")
async def download_run_file(run_id: int, filename: str, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/files/{filename}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "application/octet-stream")
                return Response(
                    content=resp.content,
                    media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="File not found")


@router.get("/runs/{run_id}/download")
async def download_run_archive(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    daq_url = settings.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}/download"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="run_{run_id}.zip"'},
                )
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Run data not found")
