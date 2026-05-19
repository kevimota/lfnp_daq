from typing import Optional

from sqlmodel import Field, SQLModel


class CaenPS(SQLModel, table=True):
    __tablename__ = "caen_ps"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    system_type: int
    link_type: int
    arg: str
    username: str
    password: str

class CaenPSCreate(SQLModel):
    name: str
    system_type: int
    link_type: int
    arg: str
    username: str
    password: str


class CaenPSUpdate(SQLModel):
    name: str | None = None
    system_type: int | None = None
    link_type: int | None = None
    arg: str | None = None
    username: str | None = None
    password: str | None = None


class CaenPSResponse(SQLModel):
    id: int
    name: str
    system_type: int
    link_type: int
    arg: str
    username: str