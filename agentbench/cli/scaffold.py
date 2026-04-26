"""Project scaffolding for agentbench init."""

from __future__ import annotations

from pathlib import Path

RAW_API_TEMPLATE = '''"""Agent behavioral tests — edit this to test your agent."""

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
'''

LANGCHAIN_TEMPLATE = '''"""Agent behavioral tests for a LangChain agent."""

from agentbench import AgentTest, expect
from agentbench.adapters.langchain import LangChainAdapter


class _PlaceholderTool:
    def __init__(self, name: str):
        self.name = name


class _PlaceholderExecutor:
    """Replace this with your real AgentExecutor or Runnable."""

    tools = [_PlaceholderTool("search")]

    def invoke(self, inputs, config=None):
        prompt = inputs.get("input", "")
        callbacks = (config or {}).get("callbacks", [])
        for callback in callbacks:
            on_tool_start = getattr(callback, "on_tool_start", None)
            if callable(on_tool_start):
                on_tool_start({"name": "search"}, prompt, tool_input={"input": prompt})
            on_tool_end = getattr(callback, "on_tool_end", None)
            if callable(on_tool_end):
                on_tool_end(f"search result for {prompt}")
        return {"output": f"LangChain placeholder response: {prompt}"}


adapter = LangChainAdapter(_PlaceholderExecutor())


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
        expect(result).to_use_tool("search")
        expect(result).to_complete()
'''

TEMPLATES: dict[str, dict[str, str]] = {
    "raw_api": {"test_agent.py": RAW_API_TEMPLATE},
    "langchain": {"test_agent.py": LANGCHAIN_TEMPLATE},
}
SUPPORTED_SCAFFOLD_FRAMEWORKS = tuple(TEMPLATES.keys())


def scaffold_project(output_path: Path, name: str, framework: str) -> None:
    """Create a new AgentBench test project with boilerplate files."""
    output_path.mkdir(parents=True, exist_ok=True)

    if framework not in TEMPLATES:
        supported = ", ".join(SUPPORTED_SCAFFOLD_FRAMEWORKS)
        raise ValueError(
            f"Unsupported scaffold framework '{framework}'. Supported scaffold frameworks: {supported}."
        )

    template = TEMPLATES[framework]

    for filename, content in template.items():
        (output_path / filename).write_text(content)

    config_content = f"""# AgentBench configuration
# Docs: https://github.com/agentbench/agentbench

max_steps: 50
timeout_seconds: 120
parallel_workers: 1
default_adapter: {framework}

sandbox:
  enabled: false  # Enable for Docker-based isolation

judge:
  enabled: false  # Enable for LLM-as-Judge evaluation
  provider: openai
  model: gpt-4o-mini
"""
    (output_path / "agentbench.yaml").write_text(config_content)

    (output_path / "requirements.txt").write_text("agentbench\n")
    (output_path / ".agentbench" / "trajectories").mkdir(parents=True, exist_ok=True)
