"""Server configuration — loaded from environment variables with sensible defaults."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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
        default_factory=lambda: os.getenv("AGENTBENCH_SECRET_KEY", "dev-secret-change-me")
    )
    api_keys: list[str] = field(
        default_factory=lambda: _load_api_keys()
    )
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "AGENTBENCH_CORS_ORIGINS", "*"
        ).split(",")
    )
    debug: bool = field(
        default_factory=lambda: os.getenv("AGENTBENCH_DEBUG", "false").lower() == "true"
    )

    def __post_init__(self) -> None:
        if not self.debug:
            if self.secret_key == "dev-secret-change-me":
                logger.warning(
                    "ServerConfig: using default secret_key in production mode. "
                    "Set AGENTBENCH_SECRET_KEY to a secure value."
                )
            if self.api_keys == ["dev-key"]:
                logger.warning(
                    "ServerConfig: using default api_key in production mode. "
                    "Set AGENTBENCH_API_KEYS to a secure value."
                )


def _load_api_keys() -> list[str]:
    """Load API keys from the environment.

    Keys are comma-separated in ``AGENTBENCH_API_KEYS``.
    A default key ``dev-key`` is always present in debug mode.
    """
    raw = os.getenv("AGENTBENCH_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    # Always include a usable dev key when no keys are configured
    if not keys:
        keys = ["dev-key"]
    return keys


# Module-level singleton — imported by other server modules.
settings = ServerConfig()
