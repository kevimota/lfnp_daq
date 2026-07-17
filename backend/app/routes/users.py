from typing import Annotated, Any

from fastapi import HTTPException, APIRouter, Depends, status

import jwt
from pydantic import ValidationError
from uuid import UUID

from ..core.db import SessionDep
from ..core import auth
from ..core.config import settings

from .login import TokenDep

from ..models.auth import (
    TokenPayload,
    User,
    UserCreate,
    UserPublic,
    UserUpdate,
    UserUpdateId,
    UpdatePassword,
    UsersPublic,
    Message
)

from sqlmodel import col, func, select


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (jwt.InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    user = session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
def read_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    """
    Retrieve users.
    """

    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()

    statement = (
        select(User).order_by(col(User.created_at).desc()).offset(skip).limit(limit)
    )
    users = session.exec(statement).all()

    users_public = [UserPublic.model_validate(user) for user in users]
    return UsersPublic(data=users_public, count=count)


@router.post(
    "/", dependencies=[Depends(get_current_active_superuser)], response_model=UserPublic
)
def create_user(session: SessionDep, user_in: UserCreate) -> Any:
    """
    Create new user.
    """
    user = auth.get_user_by_username(session=session, username=user_in.username)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system.",
        )
    user = auth.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )

    user = auth.create_user(session=session, user_create=user_in)

    return user


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    """
    Get current user.
    """
    return current_user


@router.patch("/me", response_model=UserPublic)
def patch_user_me(
    session: SessionDep, user_in: UserUpdate, current_user: CurrentUser
) -> Any:

    if user_in.username:
        existing_user = auth.get_user_by_username(
            session=session, username=user_in.username
        )
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    if user_in.email:
        existing_user = auth.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )

    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.patch("/me/password", response_model=Message)
def update_password_me(
    session: SessionDep,
    body: UpdatePassword,
    current_user: CurrentUser,
) -> Any:
    verified, _ = auth.verify_password(body.current_password, current_user.hashed_password)
    if not verified:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.hashed_password = auth.get_password_hash(body.new_password)
    session.add(current_user)
    session.commit()
    return Message(message="Password updated successfully")


@router.get(
    "/{user_id}",
    response_model=UserPublic,
    dependencies=[Depends(get_current_active_superuser)],
)
def get_user_by_id(session: SessionDep, user_id: UUID):
    statement = select(User).where(User.id == user_id)
    session_user = session.exec(statement).first()
    return session_user


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    dependencies=[Depends(get_current_active_superuser)],
)
def update_user_by_id(session: SessionDep, user_id: UUID, user_in: UserUpdateId):
    db_user = session.get(User, user_id)

    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )

    if user_in.username:
        existing_user = auth.get_user_by_username(
            session=session, username=user_in.username
        )
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    if user_in.email:
        existing_user = auth.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )

    db_user = auth.update_user(session, db_user, user_in)

    return db_user


@router.delete("/{user_id}", dependencies=[Depends(get_current_active_superuser)])
def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: UUID
) -> Message:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user == current_user:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    session.delete(user)
    session.commit()
    return Message(message="User deleted successfully")
