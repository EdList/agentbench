"""Property-Based Testing module for AgentBench.

⚠ EXPERIMENTAL — This module is alpha-quality. APIs may change without notice.
For production agent scanning, use the core scan API (``agentbench.scanner``).

Provides generators, property definitions, and shrinking for
randomised, property-based testing of AI agents.

Usage::

    from agentbench.property import (
        AgentInput,
        ToolCallGen,
        ConversationGen,
        TrajectoryGen,
        property_test,
        Property,
        no_pii_leakage,
        bounded_steps,
        consistent_behavior,
        no_hallucinated_tools,
        graceful_degradation,
        shrink,
    )
"""

from agentbench.property.generators import (
    AgentInput,
    ConversationGen,
    ToolCallGen,
    TrajectoryGen,
)
from agentbench.property.properties import (
    Property,
    bounded_steps,
    consistent_behavior,
    graceful_degradation,
    no_hallucinated_tools,
    no_pii_leakage,
    property_test,
)
from agentbench.property.shrink import shrink

__all__ = [
    # Generators
    "AgentInput",
    "ToolCallGen",
    "ConversationGen",
    "TrajectoryGen",
    # Property infrastructure
    "Property",
    "property_test",
    # Built-in properties
    "no_pii_leakage",
    "bounded_steps",
    "consistent_behavior",
    "no_hallucinated_tools",
    "graceful_degradation",
    # Shrinking
    "shrink",
]
