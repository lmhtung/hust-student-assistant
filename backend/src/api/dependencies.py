"""FastAPI dependency: lấy bearer rồi chuyển lỗi xác thực thành HTTP 401."""

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth import verify_web_token
from src.schemas.auth import CurrentUser

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return verify_web_token(credentials.credentials, request.app.state.settings)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid or expired token") from None
