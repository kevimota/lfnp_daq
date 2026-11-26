from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from datetime import datetime, UTC
from typing import Optional
from sqlalchemy import select as sa_select
from sqlmodel import select

from ..core.config import config
from ..core.db import SessionDep
from ..models.sensor import SensorData, SensorDataCreate

router = APIRouter(prefix="/sensor", tags=["Sensor"])

MAX_SENSOR_BODY_BYTES = 1_000_000
MAX_SENSOR_LIMIT = 10_000


async def verify_sensor_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if x_api_key != config.SENSOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


async def limit_sensor_body_size(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_SENSOR_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Sensor payload too large")


@router.post(
    "",
    dependencies=[Depends(verify_sensor_api_key), Depends(limit_sensor_body_size)],
)
def post_sensor_data(req: SensorDataCreate, session: SessionDep):
    record = SensorData(name=req.name, data=req.data)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@router.get("/names")
def get_sensor_names(session: SessionDep):
    # PostgreSQL DISTINCT ON: latest row per sensor name in one query.
    latest = session.exec(
        sa_select(SensorData)
        .distinct(SensorData.name)
        .order_by(SensorData.name, SensorData.timestamp.desc())
    ).all()
    return [
        {
            "name": row.name,
            "attributes": list(row.data.keys()) if row.data else [],
        }
        for row in latest
    ]


@router.get("")
def get_sensor_data(
    session: SessionDep,
    name: Optional[str] = Query(None, description="Filter by sensor name"),
    from_: Optional[datetime] = Query(None, alias="from", description="Start of time range"),
    to: Optional[datetime] = Query(None, description="End of time range"),
    limit: Optional[int] = Query(None, ge=1, description="Max results (default 100, hard cap 10000)"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    now = datetime.now(UTC)
    if from_ and from_ > now:
        raise HTTPException(status_code=422, detail="'from' cannot be in the future")
    if to and to > now:
        raise HTTPException(status_code=422, detail="'to' cannot be in the future")
    if from_ and to and to <= from_:
        raise HTTPException(status_code=422, detail="'to' must be later than 'from'")

    if limit is None:
        limit = 100 if (from_ is None and to is None) else MAX_SENSOR_LIMIT
    else:
        limit = min(limit, MAX_SENSOR_LIMIT)

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
