import os
from dotenv import load_dotenv # <--- ADD THIS
from pydantic_settings import BaseSettings
from pathlib import Path

load_dotenv()

class Settings(BaseSettings):
    # Database (Defaults to local usage)
    DATABASE_URL: str = "postgresql://postgres:Kaushal123@localhost:5432/coding_agent"
    
    # Google Gemini API
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-1.5-flash" # or "gemini-1.5-pro"
    
    # File Storage
    UPLOAD_DIR: Path = Path(__file__).resolve().parent.parent / "uploads"
    
    # CocoIndex
    COCOINDEX_DATABASE_URL: str = "postgresql://postgres:Kaushal123@localhost:5432/coding_agent"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Ensure upload directory exists
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# Trigger uvicorn reload
