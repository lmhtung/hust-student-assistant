"""JWT của hai chặng web→assistant và assistant→MCP.
<base64-encoded header>.<base64-encoded payload>.<base64-encoded signature>
Module này không phụ thuộc FastAPI; script, agent client và API dùng chung.
"""

import time

import jwt

from src.config import Settings
from src.schemas.auth import CurrentUser


def verify_web_token(token: str, settings: Settings) -> CurrentUser:
    """Kiểm tra JWT và chỉ chấp nhận role student/admin qua schema CurrentUser."""
    claims = jwt.decode(
        token,
        settings.auth_secret.get_secret_value(),
        algorithms=["HS256"],  # HMAC SHA256 - HS256
        audience="assistant-api",
        issuer="web-backend",
        options={"require": ["sub", "role", "permissions", "exp", "iat", "iss", "aud"]},
    )
    if not 0 < claims["exp"] - claims["iat"] <= 900:
        raise ValueError("Token phải có thời hạn tối đa 15 phút")
    return CurrentUser(
        user_id=claims["sub"], role=claims["role"], permissions=claims["permissions"]
    )


def issue_mcp_token(user: CurrentUser, settings: Settings) -> str:
    if user.role != "student" or "academic:read" not in user.permissions:
        raise PermissionError("Student read permission required")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user.user_id,
            "role": "student",
            "permissions": ["academic:read"],
            "iss": "assistant-api",
            "aud": "academic-mcp",
            "iat": now,
            "exp": now + 300,
        },
        settings.mcp_secret.get_secret_value(),
        algorithm="HS256",
    )
