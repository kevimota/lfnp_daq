from pydantic import BaseModel, Field
from typing import List, Optional


class HVPointChannel(BaseModel):
    slot: int = Field(description="Slot number")
    channel: int = Field(description="Channel number")
    voltage: float = Field(description="Voltage in volts")


class ScanStartRequest(BaseModel):
    run_id: int = Field(description="Run ID from database")


class ScanStartResponse(BaseModel):
    success: bool
    run_id: Optional[int] = None
    data_path: Optional[str] = None
    message: str = ""


class DAQStatusResponse(BaseModel):
    state: str
    hv_point: str = ""
    run_id: str = ""


class DAQInfoResponse(BaseModel):
    state: str
    run_id: Optional[int] = None
    current_point: Optional[int] = None
    total_points: int = 0
    run_dir: Optional[str] = None