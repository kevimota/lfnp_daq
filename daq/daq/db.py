import os
from sqlmodel import Session, create_engine, Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional, List
from datetime import datetime, UTC

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

engine = create_engine(DATABASE_URL)


def get_session() -> Session:
    return Session(engine)


# ── Table definitions (read-only; schema managed by backend) ────

class CaenPS(SQLModel, table=True):
    __tablename__ = "caen_ps"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    system_type: int
    link_type: int
    arg: str
    username: str
    password: str


class DAQConfigurationDB(SQLModel, table=True):
    __tablename__ = "daq_configuration"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(default="hv_scan")
    voltage_points: List = Field(default=[], sa_column=Column(JSONB))
    wait_time_seconds: int
    sample_interval_seconds: float
    number_of_samples: int = Field(default=60)
    end_voltage: int = Field(default=0)
    power_supply: Optional[int] = Field(default=None, foreign_key="caen_ps.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DAQRuns(SQLModel, table=True):
    __tablename__ = "daq_runs"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    configuration_id: int = Field(default=0)
    status: str = Field(default="running")
    data_path: Optional[str] = None
    label: Optional[str] = None
    comments: Optional[str] = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
