"""
Application configuration — loaded from environment variables / .env file.
All DATA_* paths are Windows host paths.
All WIN_*  paths are Windows host paths.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # silently ignore any .env keys not listed here
    )

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://postgres:password@127.0.0.1:5432/postgres"

    # ── Ollama ────────────────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str 
    
    # CORS
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173","http://localhost:3000"]
    

    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str = "changeme"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480       # 8 hours

    WIN_APPIO_DIR: str 
    # User input directory on Windows host 
    # User input directory — settings.DATA_USER_INPUT_DIR is read directly by
    # docx_service.py, so this must be the real (Windows) path 
    DATA_USER_INPUT_DIR: str 
    #
    # Data SEC 10-K directories on Windows host 
    # Data SEC 10-Q directories on Windows host 
    #
    # Data SEC 10-K directories — settings.DATA_10K_* is what docx_service.py
    # actually reads. 
    DATA_10K_DIR: str 
    DATA_10K_INTRO: str 
    DATA_10K_PART1: str 
    DATA_10K_PART2: str
    DATA_10K_PART3: str 
    DATA_10K_PART4: str 
    DATA_10K_SIGS: str 
    #
    # Data SEC 10-Q directories — to allow for q1,q2,q3
    # actually reads. 
    BASE_10Q : str 
    DATA_10Q_INTRO: str 
    DATA_10Q_PART1: str 
    DATA_10Q_PART2: str 
    DATA_10Q_SIGS: str 
    #
    # Reports output directory on Windows host 
    # Reports output directory — settings.REPORTS_DIR
    REPORTS_DIR: str 

settings = Settings()
