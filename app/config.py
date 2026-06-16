from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Latin Analyzer Backend"
    cors_origins: list[str] = ["*"]

    latin_wordnet_base_url: str = "https://latinwordnet.exeter.ac.uk/api"
    udpipe_base_url: str = "https://lindat.mff.cuni.cz/services/udpipe/api"
    latin_is_simple_base_url: str = "https://www.latin-is-simple.com"

    downstream_timeout_seconds: float = 8.0
    downstream_connect_timeout_seconds: float = 4.0
    downstream_retries: int = 1
    downstream_concurrency: int = 10

    cache_ttl_seconds: int = 21600
    cache_max_items: int = 5000

    user_agent: str = "Mozilla/5.0"
    verify_tls: bool = False

    zenrows_api_key: str = ""

    model_config = SettingsConfigDict(env_prefix="LATIN_ANALYZER_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
