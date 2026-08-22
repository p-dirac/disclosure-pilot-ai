"""SQLAlchemy models for application user authentication."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, text
from sqlalchemy.orm import Session
from app.core.database import Base


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = {"schema": "applogins"}

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    password_date = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
