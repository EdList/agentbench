"""Server configuration — loaded from environment variables."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _split_csv_env(name: str, default: str = "") -> list[str]:
    """Return a stripped list parsed from a comma-separated environment variable."""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_api_keys() -> list[str]:
    """Load API keys from ``AGENTBENCH_API_KEYS`` without injecting prod defaults."""
    return _split_csv_env("AGENTBENCH_API_KEYS")


def _generate_dev_secret() -> str:
    """Generate an ephemeral debug-only secret key."""
    return f"dev-secret-{secrets.token_urlsafe(24)}"


def _generate_dev_api_key() -> str:
    """Generate an ephemeral debug-only API key."""
    return f"dev-key-{secrets.token_urlsafe(18)}"


@dataclass
class ServerConfig:
    """Runtime configuration for the AgentBench API server."""

    host: str = field(default_factory=lambda: os.getenv("AGENTBENCH_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("AGENTBENCH_PORT", "8000")))
    database_url: str = field(
        default_factory=lambda: os.getenv("AGENTBENCH_DATABASE_URL", "sqlite:///./agentbench.db")
    )
    secret_key: str = field(default_factory=lambda: os.getenv("AGENTBENCH_SECRET_KEY", "").strip())
    api_keys: list[str] = field(default_factory=_load_api_keys)
    cors_origins: list[str] = field(
        default_factory=lambda: _split_csv_env("AGENTBENCH_CORS_ORIGINS", "*")
    )
    scanner_use_llm: bool = field(
        default_factory=lambda: os.getenv("AGENTBENCH_SCANNER_USE_LLM", "false").lower() == "true"
    )
    debug: bool = field(
        default_factory=lambda: os.getenv("AGENTBENCH_DEBUG", "false").lower() == "true"
    )
    scan_store_mode: str = field(
        default_factory=lambda: os.getenv("AGENTBENCH_SCAN_STORE_MODE", "local")
    )
    scan_max_workers: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_MAX_WORKERS", "4"))
    )
    scan_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_TIMEOUT_SECONDS", "300"))
    )
    scan_memory_cap: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_MEMORY_CAP", "1000"))
    )
    allowed_private_cidrs: list[str] = field(
        default_factory=lambda: _split_csv_env("AGENTBENCH_ALLOWED_PRIVATE_CIDRS")
    )
    retention_days: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_RETENTION_DAYS", "90"))
    )
    cors_allow_credentials: bool = field(
        default_factory=lambda: os.getenv("AGENTBENCH_CORS_ALLOW_CREDENTIALS", "false").lower()
        == "true"
    )
    scan_rate_limit_window_seconds: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_RATE_LIMIT_WINDOW_SECONDS", "60"))
    )
    scan_rate_limit_max_requests: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_RATE_LIMIT_MAX_REQUESTS", "10"))
    )
    scan_max_queued_per_principal: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_MAX_QUEUED_PER_PRINCIPAL", "2"))
    )
    scan_max_queued_total: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SCAN_MAX_QUEUED_TOTAL", "10"))
    )
    sync_scan_wait_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("AGENTBENCH_SYNC_SCAN_WAIT_TIMEOUT_SECONDS", "60"))
    )
    base_url: str = field(
        default_factory=lambda: os.getenv("AGENTBENCH_BASE_URL", "").rstrip("/")
    )

    def __post_init__(self) -> None:
        if "*" in self.cors_origins and self.cors_allow_credentials:
            if self.debug:
                logger.warning(
                    "ServerConfig: wildcard CORS cannot be combined with credentials;"
                    " disabling credentialed CORS in debug mode."
                )
                self.cors_allow_credentials = False
            else:
                raise ValueError(
                    "AGENTBENCH_CORS_ALLOW_CREDENTIALS cannot be true when "
                    "AGENTBENCH_CORS_ORIGINS contains '*'."
                )

        if self.scan_rate_limit_window_seconds <= 0:
            raise ValueError("AGENTBENCH_SCAN_RATE_LIMIT_WINDOW_SECONDS must be greater than 0.")
        if self.scan_rate_limit_max_requests <= 0:
            raise ValueError("AGENTBENCH_SCAN_RATE_LIMIT_MAX_REQUESTS must be greater than 0.")
        if self.scan_max_queued_per_principal <= 0:
            raise ValueError("AGENTBENCH_SCAN_MAX_QUEUED_PER_PRINCIPAL must be greater than 0.")
        if self.scan_max_queued_total <= 0:
            raise ValueError("AGENTBENCH_SCAN_MAX_QUEUED_TOTAL must be greater than 0.")
        if self.sync_scan_wait_timeout_seconds <= 0:
            raise ValueError("AGENTBENCH_SYNC_SCAN_WAIT_TIMEOUT_SECONDS must be greater than 0.")

        if self.debug:
            if not self.secret_key:
                logger.warning(
                    "ServerConfig: AGENTBENCH_SECRET_KEY not set in debug mode;"
                    " using ephemeral dev secret."
                )
                self.secret_key = _generate_dev_secret()
            if not self.api_keys:
                logger.warning(
                    "ServerConfig: AGENTBENCH_API_KEYS not set in debug mode;"
                    " using ephemeral dev API key."
                )
                self.api_keys = [_generate_dev_api_key()]
            return

        if not self.secret_key:
            raise ValueError("AGENTBENCH_SECRET_KEY must be set when AGENTBENCH_DEBUG is false.")
        if not self.api_keys:
            raise ValueError("AGENTBENCH_API_KEYS must be set when AGENTBENCH_DEBUG is false.")


# Module-level singleton — imported by other server modules.
settings = ServerConfig()
