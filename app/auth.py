from typing import Protocol
import secrets
from fastapi import Header, HTTPException
from app.config import settings

class AuthProvider(Protocol):
    def authenticate(self, authorization: str) -> str: ...

class LocalTokenAuth:
    def authenticate(self, authorization):
        if not settings.api_token or not secrets.compare_digest(authorization,'Bearer '+settings.api_token):
            raise HTTPException(status_code=401,detail='Требуется авторизация')
        return 'local-owner'

provider: AuthProvider=LocalTokenAuth()

def current_user(authorization: str=Header(default='')):
    return provider.authenticate(authorization)
