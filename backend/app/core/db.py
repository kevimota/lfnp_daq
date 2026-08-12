from collections.abc import Generator
from typing import Annotated
from sqlmodel import Session, create_engine, select, text
from fastapi import Depends

from .config import config
from ..models.auth import User, UserCreate
from .auth import create_user

engine = create_engine(str(config.DATABASE_URL))


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

SessionDep = Annotated[Session, Depends(get_db)]

def init_db(session: Session) -> None:
    from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    SQLModel.metadata.create_all(engine)

    # Migration: create_all does not alter existing tables
    for statement in (
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS digitizer_id INTEGER REFERENCES caen_digitizer (id)",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS trigger_mode VARCHAR",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS trigger_frequency_hz DOUBLE PRECISION",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS number_of_triggers INTEGER",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS record_length INTEGER",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS post_trigger_size INTEGER",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS input_range_vpp DOUBLE PRECISION",
        "ALTER TABLE daq_configuration ADD COLUMN IF NOT EXISTS channels JSONB",
        "ALTER TABLE daq_configuration DROP COLUMN IF EXISTS digitizer_config",
    ):
        with engine.begin() as conn:
            conn.execute(text(statement))

    user = session.exec(
        select(User).where(User.username == config.FIRST_SUPERUSER)
    ).first()
    
    if not user:
        user_in = UserCreate(
            username=config.FIRST_SUPERUSER,
            password=config.FIRST_SUPERUSER_PASSWORD,
            email=config.FIRST_SUPERUSER_EMAIL,
            is_superuser=True,
        )
        user = create_user(session, user_in)
