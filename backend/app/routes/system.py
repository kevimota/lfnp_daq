from fastapi import APIRouter
import httpx

from ..core.config import config

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/health")
async def system_health():
    daq_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.DAQ_URL}/health")
            daq_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "backend_status": "ok",
        "daq_status": "ok" if daq_ok else "unreachable",
    }


@router.get("/storage")
async def system_storage():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.DAQ_URL}/daq/storage")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {
        "path": "/data/daq",
        "total_bytes": 0,
        "used_bytes": 0,
        "free_bytes": 0,
        "percent_used": 0,
    }
