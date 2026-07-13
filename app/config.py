from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/nameplate_ocr"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    upload_dir: str = "/data/uploads"
    max_upload_size_mb: int = 20

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
