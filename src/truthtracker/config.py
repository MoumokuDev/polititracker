from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://truthtracker:truthtracker@127.0.0.1:5432/truthtracker"
    )
    data_gov_api_key: str = ""
    courtlistener_api_token: str = ""
    # bumped manually at the start of each Congress/session
    current_congress: int = 119
    current_session: int = 2
    # cosine similarity below this is reported as "no strong match"
    search_match_threshold: float = 0.55
    # minimum similarity for a machine-assigned statement topic tag
    topic_tag_threshold: float = 0.60
    # promise/evidence editing forms — DISABLE before any public deployment
    # (the editor endpoints have no authentication yet)
    enable_editing: bool = True
    # shown as the author of editorial assessments
    editor_name: str = "instance editor"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
