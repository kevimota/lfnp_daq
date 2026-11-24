import logging
import os
import secrets

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router as daq_router, scan_manager

_DAQ_API_KEY = os.getenv("DAQ_API_KEY")
if not _DAQ_API_KEY:
    raise RuntimeError("DAQ_API_KEY is not set; refusing to start without authentication")

logging.basicConfig(
    level=os.getenv("DAQ_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="DAQ System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _key_matches(value: str) -> bool:
    return secrets.compare_digest(value.encode(), _DAQ_API_KEY.encode())


@app.middleware("http")
async def require_daq_key(request: Request, call_next):
    # CORS preflight requests cannot carry custom headers.
    key_ok = _key_matches(request.headers.get("x-daq-key", ""))
    if request.method != "OPTIONS" and not key_ok:
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(daq_router, prefix="/daq", tags=["DAQ"])


@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: int):
    if not _key_matches(websocket.headers.get("x-daq-key", "")):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    ctx = scan_manager.get(run_id)
    if ctx is None:
        await websocket.send_json({"type": "error", "detail": "Run not active"})
        await websocket.close()
        return

    ctx.broadcaster.add_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ctx.broadcaster.remove_client(websocket)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "active_runs": list(scan_manager.keys()),
    }
