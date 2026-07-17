from fastapi import APIRouter, Body, Depends, Query
from sqlmodel import select
from typing import Any, Optional

from ..core.db import SessionDep
from ..models.settings import AppSetting
from .users import get_current_active_superuser

router = APIRouter(
    prefix="/settings",
    tags=["Settings"],
    dependencies=[],
)


@router.get("")
def get_settings(
    session: SessionDep,
    key: Optional[str] = Query(None, description="Filter by setting key"),
):
    if key:
        setting = session.get(AppSetting, key)
        return {key: setting.value if setting else {}}
    settings = session.exec(select(AppSetting)).all()
    return {s.key: s.value for s in settings}


@router.put("/{key}", dependencies=[Depends(get_current_active_superuser)])
def upsert_setting(key: str, session: SessionDep, value: Any = Body(...)):
    setting = session.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=key, value=value)
        session.add(setting)
    session.commit()
    return {key: value}
