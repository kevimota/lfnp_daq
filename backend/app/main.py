from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from .core.config import config
from .core.db import *
from .routes import login, users, daq, hardware, system, sensor, settings
from .models.daq import DAQRuns
from sqlmodel import Session, select
from datetime import datetime, UTC
import httpx


app = FastAPI()


@app.on_event("startup")
async def reconcile_phantom_runs():
    """On startup, mark DB runs stuck in 'running' as failed if the DAQ is not active for them."""
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        daq_url = config.DAQ_URL
        url = f"{daq_url}/health"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return  # DAQ unreachable – leave runs untouched
            data = resp.json()
            active_run_ids = set(data.get("active_runs", []))
        except Exception:
            return  # DAQ unreachable – leave runs untouched

    with Session(engine) as session:
        running_runs = session.exec(
            select(DAQRuns).where(DAQRuns.status == "running")
        ).all()
        for run in running_runs:
            if run.id not in active_run_ids:
                run.status = "failed"
                run.stopped_at = datetime.now(UTC)
                session.add(run)
        session.commit()


app.include_router(login.router)
app.include_router(users.router)
app.include_router(daq.router)
app.include_router(hardware.router)
app.include_router(system.router)
app.include_router(sensor.router)
app.include_router(settings.router)

with Session(engine) as session:
    init_db(session)

if config.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
