"""Configuration for AgentBench test runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SandboxConfig:
    """Docker sandbox configuration."""

    enabled: bool = True
    image: str = "agentbench-runner:latest"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = True
    timeout_seconds: int = 60
    max_containers: int = 10


@dataclass
class JudgeConfig:
    """LLM-as-Judge configuration."""

    enabled: bool = False
    provider: str = "openai"  # "openai", "anthropic", "custom"
    model: str = "gpt-4o-mini"
    api_key_env: str = ""  # Environment variable name for API key
    temperature: float = 0.0
    max_tokens: int = 500
    cost_limit_usd: float = 1.0  # Max judge cost per test run


@dataclass
class AgentBenchConfig:
    """Main configuration for AgentBench."""

    # Test execution
    max_steps: int = 50
    timeout_seconds: float = 120.0
    max_retries: int = 3
    parallel_workers: int = 1

    # Sandbox
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)

    # Judge
    judge: JudgeConfig = field(default_factory=JudgeConfig)

    # Storage
    trajectories_dir: Path = field(default_factory=lambda: Path(".agentbench/trajectories"))
    results_dir: Path = field(default_factory=lambda: Path(".agentbench/results"))

    # Agent defaults
    default_agent: str = ""
    default_adapter: str = "raw_api"  # "langchain", "openai", "raw_api"

    @classmethod
    def from_yaml(cls, path: Path | str) -> AgentBenchConfig:
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> AgentBenchConfig:
        """Create config from a dictionary."""
        config = cls()

        # Top-level fields
        expected_types: dict[str, type | tuple[type, ...]] = {
            "max_steps": int,
            "timeout_seconds": (int, float),
            "max_retries": int,
            "parallel_workers": int,
            "default_agent": str,
            "default_adapter": str,
        }
        for key in expected_types:
            if key in data:
                value = data[key]
                if not isinstance(value, expected_types[key]):
                    type_name = (
                        expected_types[key].__name__
                        if isinstance(expected_types[key], type)
                        else "number"
                    )
                    raise TypeError(
                        f"Config '{key}' expected {type_name}, got {type(value).__name__}"
                    )
                setattr(config, key, value)

        # Nested configs
        if "sandbox" in data:
            sandbox_data = data["sandbox"]
            config.sandbox = SandboxConfig(
                **{k: v for k, v in sandbox_data.items() if k in SandboxConfig.__dataclass_fields__}
            )

        if "judge" in data:
            judge_data = data["judge"]
            config.judge = JudgeConfig(
                **{k: v for k, v in judge_data.items() if k in JudgeConfig.__dataclass_fields__}
            )

        if "trajectories_dir" in data:
            config.trajectories_dir = Path(data["trajectories_dir"])
        if "results_dir" in data:
            config.results_dir = Path(data["results_dir"])

        return config

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "parallel_workers": self.parallel_workers,
            "sandbox": {
                "enabled": self.sandbox.enabled,
                "image": self.sandbox.image,
                "memory_limit": self.sandbox.memory_limit,
                "cpu_limit": self.sandbox.cpu_limit,
                "network_enabled": self.sandbox.network_enabled,
                "timeout_seconds": self.sandbox.timeout_seconds,
            },
            "judge": {
                "enabled": self.judge.enabled,
                "provider": self.judge.provider,
                "model": self.judge.model,
                "temperature": self.judge.temperature,
                "max_tokens": self.judge.max_tokens,
            },
            "trajectories_dir": str(self.trajectories_dir),
            "results_dir": str(self.results_dir),
            "default_adapter": self.default_adapter,
        }

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
