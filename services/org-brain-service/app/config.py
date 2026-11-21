"""Service configuration, read from the environment (pydantic-settings).

Same shape as the monorepo's `OrgBrainConfig`; fields are added issue by issue
as the PoC gains Postgres, OPA and AWS clients.
"""

from urllib.parse import quote_plus

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrgBrainConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "org-brain-service"
    host: str = "0.0.0.0"
    port: int = 5008
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"

    # Postgres. The service connects as org_brain_app (owns nothing, cannot
    # bypass RLS); migrations connect as org_brain_owner. See infra/postgres/initdb.
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "org_brain"
    org_brain_app_user: str = "org_brain_app"
    org_brain_app_password: SecretStr = SecretStr("")
    org_brain_owner_user: str = "org_brain_owner"
    org_brain_owner_password: SecretStr = SecretStr("")

    def _url(self, user: str, password: SecretStr) -> str:
        return (
            f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password.get_secret_value())}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_database_url(self) -> str:
        """What the service connects with: subject to row-level security."""
        return self._url(self.org_brain_app_user, self.org_brain_app_password)

    @property
    def owner_database_url(self) -> str:
        """Migrations only (Alembic)."""
        return self._url(self.org_brain_owner_user, self.org_brain_owner_password)
