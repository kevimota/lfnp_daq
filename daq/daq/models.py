from pydantic import BaseModel, Field
from typing import Optional


class RunStartRequest(BaseModel):
    start_point: Optional[int] = Field(
        default=None, description="Redo this point and continue to the end"
    )
    points: Optional[list[int]] = Field(
        default=None, description="Redo exactly these point indexes"
    )


class DAQStatusResponse(BaseModel):
    state: str
    hv_point: str = ""
    run_id: str = ""
