from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .core.config import config
from .core.db import *
from .routes import login, users, daq, hardware, system, sensor, settings

from sqlmodel import Session

app = FastAPI()
app.include_router(login.router)
app.include_router(users.router)
app.include_router(daq.router)
app.include_router(hardware.router)
app.include_router(system.router)
app.include_router(sensor.router)
app.include_router(settings.router)

with Session(engine) as session:
    init_db(session)

if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
