"""SQLAlchemy database setup."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings
import os
import logging
import sys

logger = logging.getLogger(__name__)

db_url = os.getenv("DATABASE_URL", "localhost")
engine = create_engine(db_url, pool_pre_ping=True)
#engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
