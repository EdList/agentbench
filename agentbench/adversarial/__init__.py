"""Adversarial Test Generation — mutation engine, strategies, and auto-discovery.

⚠ EXPERIMENTAL — This module is alpha-quality. APIs may change without notice.
For production agent scanning, use the core scan API (``agentbench.scanner``).
"""

from agentbench.adversarial.discovery import (
    AdversarialTestGenerator,
    adversarial_suite,
)
from agentbench.adversarial.mutator import (
    MutatorChain,
    PromptMutator,
    TrajectoryMutator,
    adversarial,
)
from agentbench.adversarial.strategies import (
    ContextOverflowStrategy,
    JailbreakStrategy,
    PIILeakStrategy,
    ToolConfusionStrategy,
)

__all__ = [
    # Mutators
    "PromptMutator",
    "TrajectoryMutator",
    "MutatorChain",
    "adversarial",
    # Strategies
    "JailbreakStrategy",
    "PIILeakStrategy",
    "ToolConfusionStrategy",
    "ContextOverflowStrategy",
    # Discovery
    "AdversarialTestGenerator",
    "adversarial_suite",
]
