from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from typing import List

from ..routes.users import get_current_user

from ..core.db import SessionDep
from ..models.hardware import CaenPS, CaenPSCreate, CaenPSUpdate, CaenPSResponse

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
