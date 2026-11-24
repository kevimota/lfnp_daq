import hashlib
import json
import mimetypes
import os
import tempfile
import threading
import zipfile

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from . import scan_manager

router = APIRouter()

DATA_ROOT = "/data/daq/raw"
#: Cached archives live outside DATA_ROOT so they are never zipped into
#: themselves. One zip + one JSON manifest per run.
CACHE_ROOT = "/data/daq/cache"

_cache_guard = threading.Lock()
_build_locks: dict[int, threading.Lock] = {}


def _run_dir(run_id: int) -> str:
    ctx = scan_manager.get(run_id)
    if ctx is not None and ctx.fsm.get_run_dir():
        return ctx.fsm.get_run_dir()
    return os.path.join(DATA_ROOT, f"run_{run_id}")


def _cache_root() -> str:
    try:
        os.makedirs(CACHE_ROOT, exist_ok=True)
        return CACHE_ROOT
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), "lfnp_daq_cache")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _cache_paths(run_id: int) -> tuple[str, str]:
    root = _cache_root()
    return (
        os.path.join(root, f"run_{run_id}.zip"),
        os.path.join(root, f"run_{run_id}.json"),
    )


def _build_lock(run_id: int) -> threading.Lock:
    with _cache_guard:
        lock = _build_locks.get(run_id)
        if lock is None:
            lock = threading.Lock()
            _build_locks[run_id] = lock
        return lock


def _wipe_run(run_dir: str, run_id: int) -> None:
    """Remove a run directory and its cached archive files (if they exist)."""
    import shutil
    # Remove the run directory tree (raw data, configs, logs, etc.)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    # Remove cached zip + manifest for this run
    cache_dir = os.path.join("/data/daq/cache")
    for ext in (".zip", ".json"):
        p = os.path.join(cache_dir, f"run_{run_id}{ext}")
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _run_fingerprint(run_dir: str) -> str:
    """Fingerprint the run directory contents (path + size + mtime).

    Any added, removed or rewritten point file changes the digest, so a cached
    archive is rebuilt automatically when a run is redone, resumed or extended.
    """
    entries = []
    for root, _dirs, files in os.walk(run_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            entries.append((os.path.relpath(path, run_dir), st.st_size, st.st_mtime_ns))
    digest = hashlib.sha256()
    for rel, size, mtime in sorted(entries):
        digest.update(f"{rel}\0{size}\0{mtime}\n".encode())
    return digest.hexdigest()


def invalidate_archive_cache(run_id: int) -> None:
    """Drop the cached archive for a run.

    Called when a run is (re)started so a redo cannot serve a stale archive.
    """
    zip_path, meta_path = _cache_paths(run_id)
    with _build_lock(run_id):
        for path in (zip_path, meta_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _cache_is_fresh(run_id: int, fingerprint: str) -> bool:
    zip_path, meta_path = _cache_paths(run_id)
    if not (os.path.isfile(zip_path) and os.path.isfile(meta_path)):
        return False
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        return (
            meta.get("fingerprint") == fingerprint
            and meta.get("size") == os.path.getsize(zip_path)
        )
    except (OSError, ValueError):
        return False


def _build_archive(run_id: int, run_dir: str, fingerprint: str) -> str:
    zip_path, meta_path = _cache_paths(run_id)
    tmp_path = f"{zip_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        # ZIP_STORED: ROOT files are already compressed, so deflating them costs
        # CPU for almost no size gain. Storing keeps archive creation near
        # disk-copy speed.
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
            for root, _dirs, files in os.walk(run_dir):
                for name in files:
                    file_path = os.path.join(root, name)
                    arcname = os.path.relpath(file_path, run_dir)
                    zf.write(file_path, arcname)
        os.replace(tmp_path, zip_path)
        with open(meta_path, "w") as f:
            json.dump(
                {"fingerprint": fingerprint, "size": os.path.getsize(zip_path)}, f
            )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return zip_path


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
    run_dir = _run_dir(run_id)
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
def download_run_archive(run_id: int):
    # Sync endpoint: FastAPI runs it in a worker thread, so a large archive
    # build never blocks the event loop (status polling / websockets stay live).
    run_dir = _run_dir(run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run directory not found")

    fingerprint = _run_fingerprint(run_dir)
    if not _cache_is_fresh(run_id, fingerprint):
        with _build_lock(run_id):
            # Re-check under the lock: a concurrent request may have built it.
            fingerprint = _run_fingerprint(run_dir)
            if not _cache_is_fresh(run_id, fingerprint):
                _build_archive(run_id, run_dir, fingerprint)

    zip_path, _ = _cache_paths(run_id)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"run_{run_id}.zip",
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
