"""Mô phỏng backend web ký token local. Không cung cấp endpoint mint công khai.

Chạy từ backend/: python -m scripts.mint_demo_token --user demo-et.
"""

import argparse
import time

import jwt

from src.config import get_settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="demo-et")
    parser.add_argument("--role", choices=["student", "admin"], default="student")
    args = parser.parse_args()
    now = int(time.time())
    print(
        jwt.encode(
            {
                "sub": args.user,
                "role": args.role,
                "permissions": ["academic:read"] if args.role == "student" else [],
                "iss": "web-backend",
                "aud": "assistant-api",
                "iat": now,
                "exp": now + 300,
            },
            get_settings().auth_secret.get_secret_value(),
            algorithm="HS256",
        )
    )
