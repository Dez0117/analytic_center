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

    parser_enabled: bool = True
    parser_seed_defaults: bool = True
    parser_tick_seconds: int = 60
    parser_default_interval_minutes: int = 30
    parser_timeout_seconds: float = 20.0
    parser_max_items_per_poll: int = 25
    parser_concurrency: int = 4
    parser_user_agent: str = "GosRadarBot/0.1 (+https://example.org/gosradar; analytics prototype)"

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    @property
    def use_mock(self) -> bool:
        return self.demo_mode or not self.openrouter_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
