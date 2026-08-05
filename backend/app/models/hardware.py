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


class CaenDigitizer(SQLModel, table=True):
    __tablename__ = "caen_digitizer"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    board_model: int
    connection_type: int
    arg: str
    conet_node: int = Field(default=0)
    vme_base_address: int = Field(default=0)
    comment: str = Field(default="")


class CaenDigitizerCreate(SQLModel):
    name: str
    board_model: int
    connection_type: int
    arg: str
    conet_node: int = 0
    vme_base_address: int = 0
    comment: str = ""


class CaenDigitizerUpdate(SQLModel):
    name: str | None = None
    board_model: int | None = None
    connection_type: int | None = None
    arg: str | None = None
    conet_node: int | None = None
    vme_base_address: int | None = None
    comment: str | None = None


class CaenDigitizerResponse(SQLModel):
    id: int
    name: str
    board_model: int
    connection_type: int
    arg: str
    conet_node: int
    vme_base_address: int
    comment: str


class CaenDigitizerScan(SQLModel):
    connection_type: int = 0
    conet_node: int = 0
    vme_base_address: int = 0