from collections.abc import Generator
from typing import Annotated
from sqlmodel import Session, create_engine, select
from fastapi import Depends

from .config import settings
from ..models.auth import User, UserCreate
from .auth import create_user

engine = create_engine(str(settings.DATABASE_URL))


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

SessionDep = Annotated[Session, Depends(get_db)]

def init_db(session: Session) -> None:
    from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.username == settings.FIRST_SUPERUSER)
    ).first()
    
    if not user:
        user_in = UserCreate(
            username=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            email=settings.FIRST_SUPERUSER_EMAIL,
            is_superuser=True,
        )
        user = create_user(session, user_in)
