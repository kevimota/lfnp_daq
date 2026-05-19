from datetime import timedelta
from typing import Annotated

from ..core.db import SessionDep
from ..core import auth
from ..core.config import settings
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from ..models.auth import Token


router = APIRouter(prefix="/login")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login/token")

TokenDep = Annotated[str, Depends(oauth2_scheme)]

@router.post("/token")
def login(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
):
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    user = auth.authenticate(
        session=session, username=form_data.username, password=form_data.password
    )
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=auth.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )