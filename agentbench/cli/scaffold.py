"""Project scaffolding for agentbench init."""

from __future__ import annotations

from pathlib import Path


TEMPLATES: dict[str, dict[str, str]] = {
    "raw_api": {
        "test_agent.py": '''"""Agent behavioral tests — edit this to test your agent."""

from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


# Option 1: Test an HTTP-based agent
# adapter = RawAPIAdapter(endpoint="http://localhost:8000/chat")

# Option 2: Test a Python function
def my_agent(prompt: str, context: dict | None = None) -> dict:
    """Replace this with your actual agent."""
    return {"response": f"Echo: {prompt}", "steps": []}

adapter = RawAPIAdapter(func=my_agent)


class MyAgentTest(AgentTest):
    agent = "my-agent"
    adapter = adapter

    def test_basic_response(self):
        """Agent should respond to basic prompts."""
        result = self.run("Hello, how are you?")
        expect(result).to_complete()
        expect(result).to_respond_with("Echo")

    def test_completes_quickly(self):
        """Agent should respond within 10 steps."""
        result = self.run("Tell me a joke")
        expect(result).to_complete_within(steps=10)

    def test_no_errors(self):
        """Agent should not produce errors on normal input."""
        result = self.run("What is 2 + 2?")
        expect(result).to_have_no_errors()
        expect(result).to_complete()
''',
    },
    "langchain": {
        "test_agent.py": '''"""Agent behavioral tests for a LangChain agent."""

from agentbench import AgentTest, expect
from agentbench.adapters.langchain import LangChainAdapter

# Replace with your actual LangChain agent setup
# from langchain_openai import ChatOpenAI
# from langchain.agents import AgentExecutor, create_openai_tools_agent
# llm = ChatOpenAI(model="gpt-4o-mini")
# agent_executor = AgentExecutor(agent=..., tools=[...])

# adapter = LangChainAdapter(agent_executor)

from agentbench.adapters import RawAPIAdapter

def placeholder_agent(prompt: str, context=None) -> dict:
    return {"response": f"LangChain response: {prompt}"}

adapter = RawAPIAdapter(func=placeholder_agent)


class LangChainAgentTest(AgentTest):
    agent = "langchain-agent"
    adapter = adapter

    def test_basic_query(self):
        """Agent should handle basic queries."""
        result = self.run("Search for Python tutorials")
        expect(result).to_complete_within(steps=10)

    def test_uses_tools(self):
        """Agent should use appropriate tools."""
        result = self.run("Search for Python tutorials")
        # expect(result).to_use_tool("search")
        expect(result).to_complete()

    def test_handles_errors(self):
        """Agent should handle errors gracefully."""
        result = self.run(
            "Search for something",
            inject_tool_failure="search",
            fail_times=2,
        )
        expect(result).to_retry(max_attempts=3)
''',
    },
}


def scaffold_project(output_path: Path, name: str, framework: str) -> None:
    """Create a new AgentBench test project with boilerplate files."""
    output_path.mkdir(parents=True, exist_ok=True)

    # Get template for framework (fallback to raw_api)
    template = TEMPLATES.get(framework, TEMPLATES["raw_api"])

    # Write template files
    for filename, content in template.items():
        (output_path / filename).write_text(content)

    # Write config
    config_content = f"""# AgentBench configuration
# Docs: https://github.com/agentbench/agentbench

max_steps: 50
timeout_seconds: 120
default_adapter: {framework}

sandbox:
  enabled: false  # Enable for Docker-based isolation

judge:
  enabled: false  # Enable for LLM-as-Judge evaluation
  provider: openai
  model: gpt-4o-mini
"""
    (output_path / "agentbench.yaml").write_text(config_content)

    # Write requirements
    (output_path / "requirements.txt").write_text("agentbench\n")

    # Create trajectories dir
    (output_path / ".agentbench" / "trajectories").mkdir(parents=True, exist_ok=True)
