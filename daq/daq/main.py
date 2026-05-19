from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .routes import router as daq_router, scan_manager

app = FastAPI(title="DAQ System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(daq_router, prefix="/daq", tags=["DAQ"])


@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: int):
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
