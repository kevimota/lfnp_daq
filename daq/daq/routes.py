from fastapi import APIRouter, HTTPException, Response
import os
from typing import Optional
import asyncio
import io
import mimetypes
import zipfile
from datetime import datetime, UTC
from fastapi.responses import FileResponse

from .fsm import DAQFSM, DAQState
from .models import DAQStatusResponse, DAQInfoResponse
from .power_interface import PowerSystemInterface
from .data_writer import DataWriter
from .websocket import DataBroadcaster
from .scans import CurrentScanner
from .db import get_session, DAQConfigurationDB, CaenPS, DAQRuns
from caen_libs._caenhvwrappertypes import SystemType, LinkType


class ScanContext:
    fsm: DAQFSM
    power_interface: PowerSystemInterface
    scanner: CurrentScanner
    task: asyncio.Task
    broadcaster: DataBroadcaster

    def __init__(
        self,
        fsm: DAQFSM,
        power_interface: PowerSystemInterface,
        scanner: CurrentScanner,
        task: asyncio.Task,
        broadcaster: DataBroadcaster,
    ):
        self.fsm = fsm
        self.power_interface = power_interface
        self.scanner = scanner
        self.task = task
        self.broadcaster = broadcaster


data_writer = DataWriter()
scan_manager: dict[int, ScanContext] = {}

router = APIRouter()


async def _background_scan(config: dict, run_id: int, ps_row: CaenPS):
    """Run scan in background and clean up on completion."""
    ctx = scan_manager.get(run_id)
    if ctx is None:
        return

    try:
        result = await ctx.scanner.run_current_scan(config, run_id)
        with get_session() as session:
            run = session.get(DAQRuns, run_id)
            if run:
                if result.get("success"):
                    run.status = "finished"
                elif result.get("error") == "Stopped by user":
                    run.status = "stopped"
                else:
                    run.status = "failed"
                run.stopped_at = datetime.now(UTC)
                if result.get("data_path"):
                    run.data_path = result["data_path"]
                session.add(run)
                session.commit()
    except Exception as e:
        fsm = ctx.fsm
        fsm.fail(str(e))
        with get_session() as session:
            run = session.get(DAQRuns, run_id)
            if run:
                run.status = "failed"
                run.stopped_at = datetime.now(UTC)
                session.add(run)
                session.commit()
    finally:
        scan_manager.pop(run_id, None)


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


DATA_ROOT = "/data/daq/raw"

@router.get("/runs/{run_id}/log")
async def get_run_log(run_id: int):
    # Try active run first
    ctx = scan_manager.get(run_id)
    if ctx is not None and ctx.fsm.get_run_dir():
        log_path = os.path.join(ctx.fsm.get_run_dir(), "daq.log")
    else:
        log_path = os.path.join(DATA_ROOT, f"run_{run_id}", "daq.log")

    if not os.path.isfile(log_path):
        return Response(content="", media_type="text/plain")

    with open(log_path) as f:
        content = f.read()
    return Response(content=content, media_type="text/plain")


@router.get("/runs/{run_id}/files")
async def list_run_files(run_id: int):
    run_dir = os.path.join(DATA_ROOT, f"run_{run_id}")
    if not os.path.isdir(run_dir):
        return {"files": []}
    files = []
    for name in sorted(os.listdir(run_dir)):
        path = os.path.join(run_dir, name)
        if os.path.isfile(path):
            files.append({
                "name": name,
                "size": os.path.getsize(path),
            })
    return {"files": files}


@router.get("/runs/{run_id}/files/{filename:path}")
async def download_run_file(run_id: int, filename: str):
    ctx = scan_manager.get(run_id)
    if ctx is not None and ctx.fsm.get_run_dir():
        run_dir = ctx.fsm.get_run_dir()
    else:
        run_dir = os.path.join(DATA_ROOT, f"run_{run_id}")

    file_path = os.path.normpath(os.path.join(run_dir, filename))
    if not file_path.startswith(os.path.normpath(run_dir)):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, media_type=media_type or "application/octet-stream", filename=filename)


@router.get("/runs/{run_id}/download")
async def download_run_archive(run_id: int):
    ctx = scan_manager.get(run_id)
    if ctx is not None and ctx.fsm.get_run_dir():
        run_dir = ctx.fsm.get_run_dir()
    else:
        run_dir = os.path.join(DATA_ROOT, f"run_{run_id}")

    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run directory not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(run_dir):
            for name in files:
                file_path = os.path.join(root, name)
                arcname = os.path.relpath(file_path, run_dir)
                zf.write(file_path, arcname)
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id}.zip"'},
    )


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

    system_type = SystemType(ps_row.system_type)
    link_type = LinkType(ps_row.link_type)
    power_interface = PowerSystemInterface(
        system_type, link_type, ps_row.arg, ps_row.username, ps_row.password
    )

    fsm = DAQFSM()
    fsm.initialize()
    broadcaster = DataBroadcaster()
    scanner = CurrentScanner(fsm, power_interface, data_writer, broadcaster)
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


@router.get("/storage")
async def daq_storage():
    import shutil
    import os
    try:
        usage = shutil.disk_usage("/data/daq")
        data_size = 0
        for dirpath, dirnames, filenames in os.walk("/data/daq"):
            for f in filenames:
                try:
                    data_size += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return {
            "path": "/data/daq",
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": round(usage.used / usage.total * 100, 1),
            "data_size_bytes": data_size,
        }
    except FileNotFoundError:
        return {
            "path": "/data/daq",
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "percent_used": 0,
            "data_size_bytes": 0,
        }


