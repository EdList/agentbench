"""Role simulation — define agent roles with personality traits and prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Role:
    """Definition of an agent's role in a conversation.

    Roles encapsulate the system prompt, personality traits, and
    behavioral parameters that define how an agent should act.

    Usage::

        role = Role(
            name="Customer",
            system_prompt="You are an angry customer...",
            personality_traits=["impatient", "demanding"],
        )
        config = RolePlay.agent_config(role)
    """

    name: str
    system_prompt: str
    personality_traits: list[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 500
    tools: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the role to a dictionary."""
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "personality_traits": self.personality_traits,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": self.tools,
            "description": self.description,
        }

    def with_traits(self, *traits: str) -> Role:
        """Return a new Role with additional personality traits."""
        return Role(
            name=self.name,
            system_prompt=self.system_prompt,
            personality_traits=list(set(self.personality_traits + list(traits))),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=self.tools,
            description=self.description,
        )

    def with_prompt(self, system_prompt: str) -> Role:
        """Return a new Role with a different system prompt."""
        return Role(
            name=self.name,
            system_prompt=system_prompt,
            personality_traits=list(self.personality_traits),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=list(self.tools),
            description=self.description,
        )

    def with_tools(self, *tools: str) -> Role:
        """Return a new Role with additional tools."""
        return Role(
            name=self.name,
            system_prompt=self.system_prompt,
            personality_traits=list(self.personality_traits),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=list(set(self.tools + list(tools))),
            description=self.description,
        )


# ------------------------------------------------------------------
# Pre-built roles
# ------------------------------------------------------------------

Customer = Role(
    name="Customer",
    system_prompt=(
        "You are a customer seeking help with a product or service. "
        "You have a specific problem that needs resolving. "
        "Be polite but firm about your needs. "
        "Ask questions when you don't understand something."
    ),
    personality_traits=["curious", "practical"],
    description="A customer seeking help with a product or service.",
)

SupportAgent = Role(
    name="SupportAgent",
    system_prompt=(
        "You are a helpful customer support agent. "
        "Your goal is to resolve the customer's issue efficiently. "
        "Be empathetic, ask clarifying questions, and provide clear solutions. "
        "Always confirm the customer is satisfied before closing."
    ),
    personality_traits=["empathetic", "patient", "knowledgeable"],
    tools=["search_knowledge_base", "lookup_order", "create_ticket"],
    description="A customer support agent helping resolve issues.",
)

Manager = Role(
    name="Manager",
    system_prompt=(
        "You are a senior manager overseeing a team. "
        "You make final decisions and resolve disputes. "
        "Consider all perspectives before making a judgment. "
        "Be decisive but fair."
    ),
    personality_traits=["decisive", "fair", "strategic"],
    description="A senior manager who makes decisions and resolves disputes.",
)

Expert = Role(
    name="Expert",
    system_prompt=(
        "You are a domain expert with deep technical knowledge. "
        "Provide detailed, accurate information. "
        "Cite sources when possible. "
        "Acknowledge uncertainty when you're not sure."
    ),
    personality_traits=["analytical", "precise", "thorough"],
    tools=["search", "calculate", "verify"],
    description="A domain expert providing technical knowledge.",
)

Skeptic = Role(
    name="Skeptic",
    system_prompt=(
        "You are a critical thinker who questions assumptions. "
        "Challenge claims that lack evidence. "
        "Propose alternative viewpoints. "
        "Be constructive in your criticism."
    ),
    personality_traits=["critical", "analytical", "contrarian"],
    description="A critical thinker who challenges assumptions.",
)


class RolePlay:
    """Helper that creates agent configurations from roles.

    Usage::

        config = RolePlay.agent_config(Customer)
        # config = {"name": "Customer", "system_prompt": "...", ...}

        configs = RolePlay.create_configs([Customer, SupportAgent])
        # List of config dicts for each role

        fn = RolePlay.create_function(Customer)
        # A callable that simulates the role's behavior
    """

    @staticmethod
    def agent_config(role: Role) -> dict[str, Any]:
        """Create an agent configuration dict from a Role.

        Returns:
            Dict with name, system_prompt, personality_traits,
            temperature, max_tokens, tools, and description.
        """
        return role.to_dict()

    @staticmethod
    def create_configs(roles: list[Role]) -> list[dict[str, Any]]:
        """Create agent configuration dicts for multiple roles.

        Args:
            roles: List of Role objects.

        Returns:
            List of config dicts.
        """
        return [RolePlay.agent_config(role) for role in roles]

    @staticmethod
    def create_function(role: Role) -> Any:
        """Create a callable that simulates a role's behavior.

        The returned function accepts (message, history) and returns
        a response string that incorporates the role's personality.

        This is useful for testing without a real LLM backend.

        Args:
            role: The Role to simulate.

        Returns:
            A callable (message, history) -> str.
        """

        def _role_fn(message: str, history: list | None = None) -> str:
            # Simulate role behavior based on personality traits
            trait_prefix = ""
            if role.personality_traits:
                traits = ", ".join(role.personality_traits)
                trait_prefix = f"[{role.name} — traits: {traits}] "

            # Simple response generation based on role
            response = f"{trait_prefix}Responding to: {message}"

            # Add personality-specific touches
            if "impatient" in role.personality_traits:
                response += " (I need this resolved quickly.)"
            if "empathetic" in role.personality_traits:
                response += " (I understand your concern.)"
            if "critical" in role.personality_traits:
                response += " (But have you considered the alternatives?)"
            if "analytical" in role.personality_traits:
                response += " (Let me analyze this carefully.)"
            if "decisive" in role.personality_traits:
                response += " (Here's my decision.)"

            return response

        return _role_fn

    @staticmethod
    def create_agents(
        roles: list[Role],
    ) -> list[dict[str, Any]]:
        """Create full agent entries for use with MultiAgentTest.

        Returns a list of dicts, each with 'name', 'fn', and 'config' keys.
        """
        results: list[dict[str, Any]] = []
        for role in roles:
            results.append(
                {
                    "name": role.name,
                    "fn": RolePlay.create_function(role),
                    "config": RolePlay.agent_config(role),
                }
            )
        return results
