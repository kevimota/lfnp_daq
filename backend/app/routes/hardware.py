from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from typing import List

from ..routes.users import get_current_user
from ..core.config import config

from ..core.db import SessionDep
from ..models.hardware import (
    CaenPS,
    CaenPSCreate,
    CaenPSUpdate,
    CaenPSResponse,
    CaenDigitizer,
    CaenDigitizerCreate,
    CaenDigitizerUpdate,
    CaenDigitizerResponse,
    CaenDigitizerScan,
)

router = APIRouter(prefix="/hardware", dependencies=[Depends(get_current_user)], tags=["Hardware"])


@router.post("/caen-ps", response_model=CaenPSResponse)
def create_caen_ps(ps: CaenPSCreate, session: SessionDep):
    db_ps = CaenPS(**ps.model_dump())
    session.add(db_ps)
    session.commit()
    session.refresh(db_ps)
    return db_ps


@router.get("/caen-ps", response_model=List[CaenPSResponse])
def list_caen_ps(session: SessionDep):
    return session.exec(select(CaenPS)).all()


@router.get("/caen-ps/{ps_id}", response_model=CaenPSResponse)
def get_caen_ps(ps_id: int, session: SessionDep):
    ps = session.get(CaenPS, ps_id)
    if not ps:
        raise HTTPException(status_code=404, detail="Power supply not found")
    return ps


@router.put("/caen-ps/{ps_id}", response_model=CaenPSResponse)
def update_caen_ps(ps_id: int, update: CaenPSUpdate, session: SessionDep):
    ps = session.get(CaenPS, ps_id)
    if not ps:
        raise HTTPException(status_code=404, detail="Power supply not found")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(ps, field, value)
    session.add(ps)
    session.commit()
    session.refresh(ps)
    return ps


@router.delete("/caen-ps/{ps_id}")
def delete_caen_ps(ps_id: int, session: SessionDep):
    ps = session.get(CaenPS, ps_id)
    if not ps:
        raise HTTPException(status_code=404, detail="Power supply not found")
    session.delete(ps)
    session.commit()
    return {"success": True, "message": "Power supply deleted"}


@router.post("/caen-digitizers", response_model=CaenDigitizerResponse)
def create_caen_digitizer(digitizer: CaenDigitizerCreate, session: SessionDep):
    db_digitizer = CaenDigitizer(**digitizer.model_dump())
    session.add(db_digitizer)
    session.commit()
    session.refresh(db_digitizer)
    return db_digitizer


@router.get("/caen-digitizers", response_model=List[CaenDigitizerResponse])
def list_caen_digitizers(session: SessionDep):
    return session.exec(select(CaenDigitizer)).all()


@router.get("/caen-digitizers/{digitizer_id}", response_model=CaenDigitizerResponse)
def get_caen_digitizer(digitizer_id: int, session: SessionDep):
    digitizer = session.get(CaenDigitizer, digitizer_id)
    if not digitizer:
        raise HTTPException(status_code=404, detail="Digitizer not found")
    return digitizer


@router.put("/caen-digitizers/{digitizer_id}", response_model=CaenDigitizerResponse)
def update_caen_digitizer(digitizer_id: int, update: CaenDigitizerUpdate, session: SessionDep):
    digitizer = session.get(CaenDigitizer, digitizer_id)
    if not digitizer:
        raise HTTPException(status_code=404, detail="Digitizer not found")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(digitizer, field, value)
    session.add(digitizer)
    session.commit()
    session.refresh(digitizer)
    return digitizer


@router.delete("/caen-digitizers/{digitizer_id}")
def delete_caen_digitizer(digitizer_id: int, session: SessionDep):
    digitizer = session.get(CaenDigitizer, digitizer_id)
    if not digitizer:
        raise HTTPException(status_code=404, detail="Digitizer not found")
    session.delete(digitizer)
    session.commit()
    return {"success": True, "message": "Digitizer deleted"}


@router.post("/caen-digitizers/{digitizer_id}/test")
async def test_caen_digitizer(digitizer_id: int, session: SessionDep):
    digitizer = session.get(CaenDigitizer, digitizer_id)
    if not digitizer:
        raise HTTPException(status_code=404, detail="Digitizer not found")

    import httpx

    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/digitizers/test"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json={"digitizer_id": digitizer_id})
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@router.post("/caen-digitizers/scan")
async def scan_caen_digitizers(
    payload: CaenDigitizerScan,
):
    import httpx

    daq_url = config.DAQ_URL
    url = f"{daq_url}/daq/digitizers/scan"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload.model_dump())
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
