"""Server configuration — loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEV_SECRET = "dev-secret-change-me"
_DEV_API_KEY = "dev-key"


def _load_api_keys() -> list[str]:
    """Load API keys from ``AGENTBENCH_API_KEYS`` without injecting prod defaults."""
    raw = os.getenv("AGENTBENCH_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


@dataclass
class ServerConfig:
    """Runtime configuration for the AgentBench API server."""

    host: str = field(default_factory=lambda: os.getenv("AGENTBENCH_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("AGENTBENCH_PORT", "8000")))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "AGENTBENCH_DATABASE_URL", "sqlite:///./agentbench.db"
        )
    )
    secret_key: str = field(
        default_factory=lambda: os.getenv("AGENTBENCH_SECRET_KEY", "").strip()
    )
    api_keys: list[str] = field(default_factory=_load_api_keys)
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "AGENTBENCH_CORS_ORIGINS", "*"
        ).split(",")
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

    def __post_init__(self) -> None:
        if self.debug:
            if not self.secret_key:
                logger.warning(
                    "ServerConfig: AGENTBENCH_SECRET_KEY not set in debug mode; using dev secret."
                )
                self.secret_key = _DEV_SECRET
            if not self.api_keys:
                logger.warning(
                    "ServerConfig: AGENTBENCH_API_KEYS not set in debug mode; using dev API key."
                )
                self.api_keys = [_DEV_API_KEY]
            return

        if not self.secret_key:
            raise ValueError(
                "AGENTBENCH_SECRET_KEY must be set when AGENTBENCH_DEBUG is false."
            )
        if not self.api_keys:
            raise ValueError(
                "AGENTBENCH_API_KEYS must be set when AGENTBENCH_DEBUG is false."
            )


# Module-level singleton — imported by other server modules.
settings = ServerConfig()
