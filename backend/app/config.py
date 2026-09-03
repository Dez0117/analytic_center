from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).parents[2]


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "google/gemini-3.7-flash"
    llm_provider: str = "openrouter"
    database_url: str = "sqlite:///./gs_radar.db"
    demo_mode: bool = True
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    @property
    def use_mock(self) -> bool:
        return self.demo_mode or not self.openrouter_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
