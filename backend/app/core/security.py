"""JWT and password hashing utilities."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import hashlib
import bcrypt
import base64
from passlib.context import CryptContext
from app.core.config import settings


def get_password_hash(password: str) -> str:
    # Pre-hash with SHA-256 to handle passwords > 72 bytes
    digest = hashlib.sha256(password.encode()).digest()
    b64 = base64.b64encode(digest)
    return bcrypt.hashpw(b64, bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    digest = hashlib.sha256(password.encode()).digest()
    b64 = base64.b64encode(digest)
    return bcrypt.checkpw(b64, hashed.encode())
    


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None
