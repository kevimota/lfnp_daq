from sqlmodel import Field, SQLModel, Column
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, UTC
from typing import Optional, Any


class SensorData(SQLModel, table=True):
    __tablename__ = "sensor_data"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    data: dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        index=True,
    )


class SensorDataCreate(SQLModel):
    name: str
    data: dict[str, Any] = {}
