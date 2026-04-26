"""Multi-agent test harness — test conversations between multiple AI agents.

⚠ EXPERIMENTAL — This module is alpha-quality. APIs may change without notice.
For production agent scanning, use the core scan API (``agentbench.scanner``).
"""

from agentbench.multiagent.assertions import expect_conversation
from agentbench.multiagent.patterns import (
    Debate,
    Moderated,
    Pipeline,
    RoundRobin,
)
from agentbench.multiagent.roles import (
    Customer,
    Expert,
    Manager,
    Role,
    RolePlay,
    Skeptic,
    SupportAgent,
)
from agentbench.multiagent.test import (
    ConversationResult,
    ConversationTurn,
    MultiAgentTest,
    Topology,
)

__all__ = [
    # Core
    "MultiAgentTest",
    "ConversationTurn",
    "ConversationResult",
    "Topology",
    # Patterns
    "RoundRobin",
    "Moderated",
    "Debate",
    "Pipeline",
    # Assertions
    "expect_conversation",
    # Roles
    "Role",
    "RolePlay",
    "Customer",
    "SupportAgent",
    "Manager",
    "Expert",
    "Skeptic",
]
