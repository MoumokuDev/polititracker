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
    # master switch for the editing surface (promises, accountability records)
    enable_editing: bool = True
    # when set, editing requires login at /login with this password; when empty,
    # editing is open (local development mode) — set it on any shared instance
    editor_password: str = ""
    # cookie-signing key; falls back to editor_password if unset
    secret_key: str = ""
    # shown as the author of editorial assessments
    editor_name: str = "instance editor"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
