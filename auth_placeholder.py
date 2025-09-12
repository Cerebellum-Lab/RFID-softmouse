"""Authentication placeholder module.

Purpose:
  Provide a minimal, swappable auth layer without bringing in full security stack yet.

Design:
  - Single function `verify_token(token: str) -> bool`
  - Token(s) loaded from either environment variable AUTH_TOKEN (single) or a flat text file `auth_tokens.txt` (one token per line, comments with '#').
  - Intended to be imported by API services (e.g., fastapi_service.py / pg_api.py) to gate endpoints.

Usage (FastAPI example):

    from fastapi import Depends, HTTPException
    from auth_placeholder import token_dependency

    @app.get('/secure-endpoint')
    def secure(item: str, ok = Depends(token_dependency)):
        return {"item": item}

Clients include header:  Authorization: Bearer <token>

Security Notes:
  - This is NOT production-ready (no rotation, hashing, revocation, scopes, rate limiting).
  - Replace with proper auth (OIDC, JWT, or API gateway) before external exposure.
"""
from __future__ import annotations
import os, pathlib
from typing import List

TOKENS_FILE = pathlib.Path('auth_tokens.txt')
_cached_tokens: List[str] | None = None

def load_tokens() -> List[str]:
    global _cached_tokens
    if _cached_tokens is not None:
        return _cached_tokens
    tokens = set()
    env_token = os.getenv('AUTH_TOKEN')
    if env_token:
        tokens.add(env_token.strip())
    if TOKENS_FILE.exists():
        for line in TOKENS_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tokens.add(line)
    _cached_tokens = list(tokens)
    return _cached_tokens


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    return token in load_tokens()

# FastAPI dependency helper (kept optional to avoid hard dependency if not used)
try:
    from fastapi import Header, HTTPException, Depends

    def token_dependency(authorization: str | None = Header(default=None)):
        token = None
        if authorization and authorization.lower().startswith('bearer '):
            token = authorization.split(None, 1)[1].strip()
        if not verify_token(token):
            raise HTTPException(status_code=401, detail='unauthorized')
        return True
except Exception:  # fastapi not installed yet
    pass

if __name__ == '__main__':
    print('Loaded tokens:', load_tokens())
