from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, UTC
from typing import Optional, List


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DAQConfigResponse(SQLModel):
    id: Optional[int]
    type: str
    voltage_points: List
    wait_time_seconds: int
    sample_interval_seconds: float
    number_of_samples: int
    end_voltage: int
    power_supply: Optional[int]
    created_at: datetime


class RunCreateRequest(SQLModel):
    voltage_points: List = []
    wait_time_seconds: int
    sample_interval_seconds: float
    number_of_samples: int = 60
    end_voltage: int = 0
    power_supply: Optional[int] = None
    label: Optional[str] = None
    comments: Optional[str] = None


class DAQRunResponse(SQLModel):
    id: Optional[int]
    configuration_id: int
    status: str
    data_path: Optional[str]
    label: Optional[str]
    comments: Optional[str]
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    created_at: datetime


class PaginatedRunsResponse(SQLModel):
    items: List[DAQRunResponse]
    total: int
    page: int
    per_page: int
    pages: int


class RunActionResponse(SQLModel):
    success: bool
    run_id: int
    message: str


class DAQConfiguration(SQLModel, table=True):
    __tablename__ = "daq_configuration"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(default="hv_scan")
    voltage_points: List = Field(default=[], sa_column=Column(JSONB))
    wait_time_seconds: int
    sample_interval_seconds: float
    number_of_samples: int = Field(default=60)
    end_voltage: int = Field(default=0)
    power_supply: Optional[int] = Field(default=None, foreign_key="caen_ps.id")
    created_at: datetime = Field(default_factory=_utcnow)


class DAQRuns(SQLModel, table=True):
    __tablename__ = "daq_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    configuration_id: int = Field(default=0)
    status: str = Field(default="running")
    data_path: Optional[str] = None
    label: Optional[str] = None
    comments: Optional[str] = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)

    class Config:
        arbitrary_types_allowed = True
