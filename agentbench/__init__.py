"""AgentBench — Behavioral testing framework for AI agents."""

__version__ = "0.3.0"

# Lazy imports to avoid circular dependencies
# Users should import directly: from agentbench.core.test import AgentTest
# Or use the convenience: from agentbench import AgentTest, expect


def __getattr__(name: str):
    if name == "AgentTest":
        from agentbench.core.test import AgentTest

        return AgentTest
    elif name == "expect":
        from agentbench.core.assertions import expect

        return expect
    elif name == "AgentBenchConfig":
        from agentbench.core.config import AgentBenchConfig

        return AgentBenchConfig
    elif name == "parametrize":
        from agentbench.core.parametrize import parametrize

        return parametrize
    elif name == "fixture":
        from agentbench.core.fixtures import fixture

        return fixture
    elif name == "Fixture":
        from agentbench.core.fixtures import Fixture

        return Fixture
    raise AttributeError(f"module 'agentbench' has no attribute {name!r}")
