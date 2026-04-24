"""Adversarial Test Generation — mutation engine, strategies, and auto-discovery."""

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
