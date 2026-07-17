"""Auth dependencies: bearer-token user resolution and role gates.

Role hierarchy: driver < validator < admin. A gate admits its role and
everything above it.
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.entities import User

bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"driver": 1, "validator": 2, "admin": 3}


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if cred is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(401, "User disabled or missing")
    return user


def require_role(min_role: str):
    def gate(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK[user.role.value] < ROLE_RANK[min_role]:
            raise HTTPException(403, f"Requires {min_role} role")
        return user
    return gate
