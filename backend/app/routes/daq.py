from fastapi import APIRouter, Depends, HTTPException, Response, Query, WebSocket
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from typing import List, Optional
from datetime import datetime, UTC
from ..routes.users import get_current_user
import asyncio
import httpx
import json
import jwt
import math
import websockets
from pydantic import ValidationError
from urllib.parse import quote
from uuid import UUID

from ..core.config import config
from ..core.db import SessionDep, engine
from ..models.auth import TokenPayload, User
from ..models.daq import (
    DAQConfiguration,
    DAQRuns,
    DAQConfigResponse,
    DAQRunResponse,
    PaginatedRunsResponse,
    RunCreateRequest,
    RunStartRequest,
    RunUpdateRequest,
    RunActionResponse,
)

router = APIRouter(prefix="/daq", tags=["DAQ"])


@router.websocket("/ws/{run_id}")
async def proxy_run_websocket(
    websocket: WebSocket,
    run_id: int,
    token: str = Query(...),
):
    """Relay live run data to the browser without exposing the DAQ service.

    Browsers cannot send an Authorization header on a WebSocket, so the JWT
    arrives as a query parameter and is validated here. The shared DAQ key
    never leaves the backend.

    shortcut: the JWT can appear in access logs; use Sec-WebSocket-Protocol
    or a one-time ticket if logs are exposed.
    """
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        token_data = TokenPayload(**payload)
        if not token_data.sub:
            raise ValueError
        with Session(engine) as session:
            user = session.get(User, UUID(token_data.sub))
    except (jwt.InvalidTokenError, ValidationError, ValueError):
        user = None

    if not user or not user.is_active:
        await websocket.close(code=4401)
        return

    daq_ws_url = f"ws://{config.DAQ_HOST}:{config.DAQ_PORT}/ws/{run_id}"
    try:
        upstream = await websockets.connect(
            daq_ws_url, additional_headers=config.DAQ_HEADERS
        )
    except Exception:
        await websocket.close(code=1011)
        return

    await websocket.accept()

    async def client_to_upstream():
        while True:
            await upstream.send(await websocket.receive_text())

    async def upstream_to_client():
        async for message in upstream:
            await websocket.send_text(message)

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()


# ── Check daq health ────────────────────────────────────────────


@router.get("/health")
async def health_check():
    daq_url = config.DAQ_URL
    url = f"{daq_url}/health"

    async with httpx.AsyncClient(
        timeout=10, verify=False, headers=config.DAQ_HEADERS
    ) as client:
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


@router.put(
    "/configs/{config_id}",
    response_model=DAQConfigResponse,
    dependencies=[Depends(get_current_user)],
)
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
    config.dc_offset = req.dc_offset
    config.channels = req.channels
    config.fast_trigger_digitizing = req.fast_trigger_digitizing if req.trigger_mode and "fast" in req.trigger_mode else False
    config.fast_trigger_threshold = req.fast_trigger_threshold
    config.fast_trigger_dc_offset = req.fast_trigger_dc_offset
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


async def _proxy_to_daq(
    run_id: int,
    action: str | None = None,
    method: str = "POST",
    payload: dict | None = None,
) -> dict:
    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/runs/{run_id}" + (f"/{action}" if action else "")
    try:
        async with httpx.AsyncClient(
            timeout=60, verify=False, headers=config.DAQ_HEADERS
        ) as client:
            if method == "GET":
                resp = await client.get(url)
            elif method == "DELETE":
                resp = await client.delete(url)
            else:
                resp = await client.post(url, json=payload)
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

    if req.type == "hv_scan":
        if not req.voltage_points:
            raise HTTPException(
                status_code=400,
                detail="HV scan requires at least one voltage point",
            )
        for idx, point in enumerate(req.voltage_points):
            if not point:
                raise HTTPException(
                    status_code=400,
                    detail=f"HV scan point {idx + 1} has no HV channel",
                )

    check_digitizer = req.type == "digitizer_scan"
    if check_digitizer:
        if not req.digitizer_id:
            raise HTTPException(
                status_code=400,
                detail="Digitizer scan requires a digitizer_id",
            )
        dig_row = session.get(CaenDigitizer, req.digitizer_id)
        if not dig_row:
            raise HTTPException(
                status_code=404,
                detail=f"Digitizer (id={req.digitizer_id}) not found",
            )

        board_model = dig_row.board_model
        _ALLOWED_TRIGGER_MODES = {
            18: ["random", "external", "fast", "random-external", "random-fast"],
            27: ["random", "external", "random-external"],
        }
        allowed = _ALLOWED_TRIGGER_MODES.get(board_model, [])
        if req.trigger_mode not in allowed:
            allowed_str = ", ".join(f'"{m}"' for m in allowed)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"trigger_mode '{req.trigger_mode}' not allowed for "
                    f"board_model {board_model} (DT5742=18, DT5743=27). "
                    f"Allowed values: {allowed_str}"
                ),
            )
        if "fast" in req.trigger_mode and board_model != 18:
            raise HTTPException(
                status_code=400,
                detail="fast trigger mode (TR0) only supported on DT5742 (board_model 18)",
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

        for field_name in ("fast_trigger_threshold", "fast_trigger_dc_offset"):
            value = getattr(req, field_name)
            if value is not None and not (0 <= int(value) <= 0xFFFF):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{field_name} must be a 16-bit DAC value 0-65535, "
                        f"got {value}"
                    ),
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
    fast_digitizing = req.fast_trigger_digitizing
    if not (req.trigger_mode and "fast" in req.trigger_mode):
        fast_digitizing = False
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
        dc_offset=req.dc_offset,
        channels=req.channels,
        fast_trigger_digitizing=fast_digitizing,
        fast_trigger_threshold=req.fast_trigger_threshold,
        fast_trigger_dc_offset=req.fast_trigger_dc_offset,
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
async def delete_run(run_id: int, session: SessionDep):
    # 1️⃣ Ask the DAQ service to wipe the run directory + cache.
    try:
        await _proxy_to_daq(run_id, method="DELETE")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DAQ delete failed: {exc}")

    # 2️⃣ If the DAQ succeeded, clean up the DB row.
    run = session.get(DAQRuns, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # 3️⃣ Remove the DB row.
    session.delete(run)
    session.commit()

    # 4️⃣ Optional: delete the DAQConfiguration if no other run references it.
    config = session.get(DAQConfiguration, run.configuration_id)
    if config is not None:
        other_runs = session.exec(
            select(DAQRuns).where(DAQRuns.configuration_id == config.id, DAQRuns.id != run_id)
        ).all()
        if not other_runs:
            session.delete(config)
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
async def start_run(
    run_id: int,
    session: SessionDep,
    req: RunStartRequest | None = None,
):
    req = req or RunStartRequest()
    run = _check_run_exists(run_id, session)

    config = session.get(DAQConfiguration, run.configuration_id)
    if not config:
        raise HTTPException(status_code=400, detail="Run has no configuration")

    scan_type = config.type or "hv_scan"
    voltage_points = config.voltage_points or []
    if not voltage_points and scan_type == "digitizer_scan":
        # A digitizer-only run still needs one acquisition point.
        voltage_points = [[]]
    total_points = len(voltage_points)
    if total_points == 0:
        raise HTTPException(
            status_code=400, detail="Configuration has no voltage points"
        )

    if req.points is not None:
        if not req.points:
            raise HTTPException(status_code=400, detail="points must not be empty")
        selected_points = sorted({int(i) for i in req.points})
        if selected_points[0] < 0 or selected_points[-1] >= total_points:
            raise HTTPException(
                status_code=400,
                detail=f"points must be between 0 and {total_points - 1}",
            )
        payload = {"points": selected_points}
        started_message = f"Redo started for {len(selected_points)} point(s)"
    elif req.start_point is not None:
        start_point = int(req.start_point)
        if not 0 <= start_point < total_points:
            raise HTTPException(
                status_code=400,
                detail=f"start_point must be between 0 and {total_points - 1}",
            )
        selected_points = list(range(start_point, total_points))
        payload = {"start_point": start_point}
        started_message = (
            f"Resumed from point {start_point + 1}"
            if start_point > 0
            else "Scan started"
        )
    else:
        selected_points = list(range(total_points))
        payload = None
        started_message = "Scan started"

    if scan_type == "hv_scan":
        for index in selected_points:
            if not voltage_points[index]:
                raise HTTPException(
                    status_code=400,
                    detail=f"HV scan point {index + 1} has no HV channel",
                )

    requested = _extract_channels(
        [voltage_points[index] for index in selected_points]
    )

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
        result = await _proxy_to_daq(run_id, "start", payload=payload)
        # Query DAQ status to set accurate run status
        status_resp = await _proxy_to_daq(run_id, "status", method="GET")
        run.status = status_resp.get("state", "running")
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        run.stopped_at = None
        session.add(run)
        session.commit()
    except Exception:
        run.status = "failed"
        session.add(run)
        session.commit()
        raise

    return RunActionResponse(
        success=True,
        run_id=run_id,
        message=result.get("message", started_message),
    )


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


@router.get("/runs/{run_id}/progress")
async def get_run_progress(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    return await _proxy_to_daq(run_id, "progress", method="GET")


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
        async with httpx.AsyncClient(timeout=10, verify=False, headers=config.DAQ_HEADERS) as client:
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
        async with httpx.AsyncClient(timeout=10, verify=False, headers=config.DAQ_HEADERS) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {"files": []}


async def _stream_from_daq(
    url: str,
    fallback_media_type: str,
    filename: str | None = None,
) -> StreamingResponse:
    """Proxy a DAQ download as a stream.

    The archive/file is never buffered in this service: chunks are relayed as
    they arrive, so large downloads start immediately and use constant memory.
    Read timeouts are disabled (a big archive can legitimately take minutes);
    only the connection attempt is bounded.
    """
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=None),
        verify=False,
        headers=config.DAQ_HEADERS,
    )
    try:
        request = client.build_request("GET", url)
        resp = await client.send(request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="DAQ service is unreachable")

    if resp.status_code != 200:
        detail = "DAQ error"
        try:
            payload = await resp.aread()
            detail = json.loads(payload).get("detail", detail)
        except Exception:
            pass
        await resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=resp.status_code, detail=detail)

    headers: dict[str, str] = {}
    for key in ("content-length", "content-disposition"):
        value = resp.headers.get(key)
        if value:
            headers[key] = value
    if filename and "content-disposition" not in headers:
        headers["content-disposition"] = f'attachment; filename="{filename}"'
    media_type = resp.headers.get("content-type", fallback_media_type)

    async def iterator():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(iterator(), media_type=media_type, headers=headers)


@router.get("/runs/{run_id}/files/{filename:path}")
async def download_run_file(run_id: int, filename: str, session: SessionDep):
    _check_run_exists(run_id, session)
    url = f"{config.DAQ_URL}/daq/runs/{run_id}/files/{quote(filename)}"
    basename = filename.rsplit("/", 1)[-1]
    return await _stream_from_daq(url, "application/octet-stream", filename=basename)


@router.get("/runs/{run_id}/download")
async def download_run_archive(run_id: int, session: SessionDep):
    _check_run_exists(run_id, session)
    url = f"{config.DAQ_URL}/daq/runs/{run_id}/download"
    return await _stream_from_daq(url, "application/zip", filename=f"run_{run_id}.zip")
