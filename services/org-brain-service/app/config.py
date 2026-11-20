"""Service configuration, read from the environment (pydantic-settings).

Same shape as the monorepo's `OrgBrainConfig`; fields are added issue by issue
as the PoC gains Postgres, OPA and AWS clients.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class OrgBrainConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "org-brain-service"
    host: str = "0.0.0.0"
    port: int = 5008
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"
