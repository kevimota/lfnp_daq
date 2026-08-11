import io
import mimetypes
import os
import zipfile

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from . import scan_manager

router = APIRouter()

DATA_ROOT = "/data/daq/raw"


def _run_dir(run_id: int) -> str:
    ctx = scan_manager.get(run_id)
    if ctx is not None and ctx.fsm.get_run_dir():
        return ctx.fsm.get_run_dir()
    return os.path.join(DATA_ROOT, f"run_{run_id}")


@router.get("/runs/{run_id}/log")
async def get_run_log(run_id: int):
    log_path = os.path.join(_run_dir(run_id), "daq.log")
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
    run_dir = _run_dir(run_id)
    file_path = os.path.normpath(os.path.join(run_dir, filename))
    if not file_path.startswith(os.path.normpath(run_dir)):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, media_type=media_type or "application/octet-stream", filename=filename)


@router.get("/runs/{run_id}/download")
async def download_run_archive(run_id: int):
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


@router.get("/storage")
async def daq_storage():
    import shutil

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