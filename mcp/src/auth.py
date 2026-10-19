"""Xác thực lời gọi API→MCP; kiểm tra độc lập với FastAPI phía trước."""

import jwt
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models import Principal


class BearerMiddleware:
    def __init__(self, app, secret):
        self.app, self.secret = app, secret

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        try:
            scheme, token = request.headers.get("authorization", "").split(" ", 1)
            if scheme.lower() != "bearer":
                raise ValueError("Bearer required")
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                audience="academic-mcp",
                issuer="assistant-api",
                options={"require": ["sub", "role", "permissions", "exp", "iat"]},
            )
            if not 0 < claims["exp"] - claims["iat"] <= 300:
                raise ValueError("Invalid lifetime")
            if claims["role"] != "student" or "academic:read" not in claims["permissions"]:
                return await JSONResponse({"error": "forbidden"}, status_code=403)(
                    scope, receive, send
                )
            scope["academic_principal"] = Principal(
                user_id=claims["sub"], role=claims["role"], permissions=claims["permissions"]
            )
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, ValidationError):
            return await JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )(scope, receive, send)
        await self.app(scope, receive, send)


def principal_from_context(ctx) -> Principal:
    request = ctx.request_context.request
    principal = request.scope.get("academic_principal") if request else None
    if principal is None or "academic:read" not in principal.permissions:
        raise PermissionError("Authenticated student context required")
    return principal
