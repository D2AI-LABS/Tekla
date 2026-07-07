# config.py — Application Settings
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "./data")

settings = Settings()
