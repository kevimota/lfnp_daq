from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from typing import Any


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: Any = Field(default={}, sa_column=Column(JSONB))
