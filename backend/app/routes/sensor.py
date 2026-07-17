from fastapi import APIRouter, Query
from datetime import datetime, UTC
from typing import Optional
from sqlmodel import select

from ..core.db import SessionDep
from ..models.sensor import SensorData, SensorDataCreate

router = APIRouter(prefix="/sensor", tags=["Sensor"])


@router.post("")
def post_sensor_data(req: SensorDataCreate, session: SessionDep):
    record = SensorData(name=req.name, data=req.data)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@router.get("/names")
def get_sensor_names(session: SessionDep):
    names = session.exec(select(SensorData.name).distinct()).all()
    result = []
    for name in names:
        latest = session.exec(
            select(SensorData.data)
            .where(SensorData.name == name)
            .order_by(SensorData.timestamp.desc())
            .limit(1)
        ).first()
        attributes = list(latest.keys()) if latest else []
        result.append({"name": name, "attributes": attributes})
    return result


@router.get("")
def get_sensor_data(
    session: SessionDep,
    name: Optional[str] = Query(None, description="Filter by sensor name"),
    from_: Optional[datetime] = Query(None, alias="from", description="Start of time range"),
    to: Optional[datetime] = Query(None, description="End of time range"),
    limit: Optional[int] = Query(None, ge=1, description="Max results (default: all)"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    if limit is None:
        if from_ is None and to is None:
            limit = 100

    query = select(SensorData)

    if name:
        query = query.where(SensorData.name == name)
    if from_:
        query = query.where(SensorData.timestamp >= from_)
    if to:
        query = query.where(SensorData.timestamp <= to)

    if order == "desc":
        query = query.order_by(SensorData.timestamp.desc())
    else:
        query = query.order_by(SensorData.timestamp.asc())

    if limit is not None:
        query = query.limit(limit)
    return session.exec(query).all()
