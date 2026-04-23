# Adapters Guide

Framework-specific guides for connecting AgentBench to your agent.

Every adapter implements the same `AgentAdapter` interface, so you can swap frameworks without changing your test code.

---

## RawAPIAdapter

Test any agent via an HTTP endpoint or a Python function. This is the most flexible adapter — works with any framework, custom code, or third-party service.

### Installation

```bash
pip install agentbench
```

### Function Mode

Wrap any Python callable as an agent:

```python
from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


def my_agent(prompt: str, context: dict | None = None) -> dict:
    """Your agent logic here. Return a dict with 'response' and optional 'steps'."""
    steps = []

    # Agent decides to use a tool
    steps.append({
        "action": "tool_call",
        "tool_name": "search",
        "tool_input": {"query": prompt},
        "tool_output": "Found 3 results",
    })

    # Agent generates a final response
    steps.append({
        "action": "llm_response",
        "response": "Here are the search results I found for you.",
    })

    return {"response": "Here are the search results I found for you.", "steps": steps}


adapter = RawAPIAdapter(
    func=my_agent,
    tools=["search", "calculator", "database"],
)


class MyAgentTest(AgentTest):
    agent = "my-agent"
    adapter = adapter

    def test_uses_search(self):
        result = self.run("Find Python tutorials")
        expect(result).to_use_tool("search")
        expect(result).to_complete()

    def test_handles_failure(self):
        result = self.run(
            "Search for something",
            inject_tool_failure="search",
            fail_times=1,
        )
        expect(result).to_retry(max_attempts=3)
```

**Return format for function mode:**

```python
{
    "response": "Final text response",    # required
    "steps": [                             # optional
        {
            "action": "tool_call",         # "tool_call" | "llm_response" | "error" | "retry"
            "tool_name": "search",         # for tool_call actions
            "tool_input": {"query": "..."}, # for tool_call actions
            "tool_output": "...",          # for tool_call actions
            "response": "...",             # for llm_response actions
            "reasoning": "...",            # optional reasoning text
            "error": "...",                # for error actions
        },
    ],
}
```

### HTTP Mode

Test agents running as web services:

```python
from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


adapter = RawAPIAdapter(
    endpoint="http://localhost:8000/chat",
    headers={
        "Authorization": "Bearer your-api-key",
        "Content-Type": "application/json",
    },
    tools=["search", "calculator"],
    timeout=30.0,
)


class HTTPAgentTest(AgentTest):
    agent = "http-agent"
    adapter = adapter

    def test_basic_query(self):
        result = self.run("What's the weather in NYC?")
        expect(result).to_complete_within(steps=5)

    def test_uses_tools(self):
        result = self.run("Calculate 2+2")
        expect(result).to_use_tool("calculator")
```

**HTTP payload sent by AgentBench:**

```json
{
    "prompt": "User's message",
    "max_steps": 50,
    "timeout": 120.0,
    "context": {},
    "inject_failures": [{"tool": "search", "times": 1, "error": "Tool unavailable"}],
    "inject_latency": [{"tool": "search", "delay_ms": 1000}]
}
```

**Expected HTTP response format:**

```json
{
    "response": "Final response text",
    "completed": true,
    "steps": [
        {"action": "tool_call", "tool_name": "search", ...},
        {"action": "llm_response", "response": "..."}
    ],
    "tokens": 150,
    "cost": 0.002
}
```

---

## LangChainAdapter

Test LangChain agents (AgentExecutor) with full step recording.

### Installation

```bash
pip install agentbench[langchain]
```

### Basic Usage

```python
from agentbench import AgentTest, expect
from agentbench.adapters import LangChainAdapter
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate


# Define tools
@tool
def search(query: str) -> str:
    """Search the web."""
    return f"Results for: {query}"

@tool
def calculator(expression: str) -> str:
    """Calculate a math expression."""
    return str(eval(expression))


tools = [search, calculator]
llm = ChatOpenAI(model="gpt-4o-mini")
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])

agent = create_openai_tools_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools)

adapter = LangChainAdapter(executor)


class LangChainAgentTest(AgentTest):
    agent = "langchain-agent"
    adapter = adapter

    def test_searches_web(self):
        result = self.run("What is the capital of France?")
        expect(result).to_complete_within(steps=5)
        expect(result).to_use_tool("search")

    def test_uses_calculator(self):
        result = self.run("What is 15% of 240?")
        expect(result).to_use_tool("calculator")
        expect(result).to_complete()

    def test_handles_tool_failure(self):
        result = self.run(
            "Search for recent news",
            inject_tool_failure="search",
            fail_times=2,
        )
        expect(result).to_retry(max_attempts=3)

    def test_safe_output(self):
        result = self.run("Search for my SSN: 123-45-6789")
        expect(result).to_not_expose("123-45-6789")
```

### How It Works

The adapter uses LangChain's callback system (`BaseCallbackHandler`) to intercept:

- **`on_llm_start` / `on_llm_end`** — Records LLM reasoning steps
- **`on_tool_start` / `on_tool_end`** — Records tool calls and outputs
- **`on_agent_action`** — Records agent decisions (and applies failure injection)
- **`on_tool_error`** — Records tool errors

No changes to your LangChain code are required — the adapter attaches callbacks automatically during `run()`.

---

## OpenAIAdapter

Test OpenAI Assistants (the Assistants API with threads, runs, and tool calls).

### Installation

```bash
pip install agentbench[openai]
```

### Basic Usage

```python
from agentbench import AgentTest, expect
from agentbench.adapters import OpenAIAdapter
from openai import OpenAI


client = OpenAI()

# Create or retrieve an assistant
assistant = client.beta.assistants.create(
    name="Research Assistant",
    model="gpt-4o",
    instructions="You are a helpful research assistant.",
    tools=[
        {"type": "function", "function": {
            "name": "search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }},
        {"type": "function", "function": {
            "name": "calculator",
            "description": "Calculate a math expression",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        }},
    ],
)

adapter = OpenAIAdapter(
    client=client,
    assistant_id=assistant.id,
    tools=["search", "calculator"],
    poll_interval=0.5,
)


class OpenAIAssistantTest(AgentTest):
    agent = "openai-assistant"
    adapter = adapter

    def test_research_query(self):
        result = self.run("Research the history of Python")
        expect(result).to_complete_within(steps=10)

    def test_uses_search_tool(self):
        result = self.run("Find recent papers on transformers")
        expect(result).to_use_tool("search")

    def test_tool_failure_recovery(self):
        result = self.run(
            "Search for AI news",
            inject_tool_failure="search",
            fail_times=1,
        )
        expect(result).to_complete()

    def test_cleanup(self):
        # Clean up the assistant after tests
        client.beta.assistants.delete(assistant.id)
```

### How It Works

1. Creates a new **thread** for each test run
2. Adds the user message and creates a **run**
3. Polls the run status at `poll_interval` intervals
4. When `requires_action`, resolves tool calls (with failure/latency injection)
5. Records all steps from the run steps API
6. Extracts the final assistant message on completion

---

## CrewAIAdapter

Test CrewAI multi-agent crews.

### Installation

```bash
pip install agentbench[crewai]
```

### Basic Usage

```python
from agentbench import AgentTest, expect
from agentbench.adapters import CrewAIAdapter
from crewai import Crew, Agent, Task


researcher = Agent(
    role="Research Analyst",
    goal="Research topics thoroughly",
    backstory="You are an experienced research analyst.",
)

writer = Agent(
    role="Technical Writer",
    goal="Write clear summaries",
    backstory="You are a skilled technical writer.",
)

research_task = Task(
    description="Research {topic}",
    agent=researcher,
    expected_output="A research summary",
)

write_task = Task(
    description="Write a summary based on the research",
    agent=writer,
    expected_output="A clear summary document",
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
)

adapter = CrewAIAdapter(crew, tools=["search"])


class CrewAITest(AgentTest):
    agent = "research-crew"
    adapter = adapter

    def test_completes_research(self):
        result = self.run("Research the benefits of Python 3.12")
        expect(result).to_complete()
        expect(result).to_respond_with("Python")

    def test_within_step_limit(self):
        result = self.run("Summarize machine learning trends")
        expect(result).to_complete_within(steps=20)

    def test_handles_empty_input(self):
        result = self.run("")
        expect(result).to_complete()
```

### How It Works

- Calls `crew.kickoff(input=prompt)` to execute the crew
- Parses `CrewOutput.tasks_output` into trajectory steps
- Records tool usage from `task_result.tools_used` when available
- Each task result becomes an `llm_response` step

---

## AutoGenAdapter

Test AutoGen multi-agent conversations.

### Installation

```bash
pip install agentbench[autogen]
```

### Basic Usage (Two-Agent Chat)

```python
from agentbench import AgentTest, expect
from agentbench.adapters import AutoGenAdapter
import autogen


assistant = autogen.AssistantAgent(
    name="assistant",
    llm_config={"model": "gpt-4o"},
)

user_proxy = autogen.UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=5,
)

adapter = AutoGenAdapter(
    assistant=assistant,
    user_proxy=user_proxy,
    tools=["search", "calculator"],
)


class AutoGenTest(AgentTest):
    agent = "autogen-assistant"
    adapter = adapter

    def test_basic_conversation(self):
        result = self.run("What is 2+2?")
        expect(result).to_complete()
        expect(result).to_respond_with("4")

    def test_tool_usage(self):
        result = self.run("Search for recent AI papers")
        expect(result).to_use_tool("search")

    def test_within_limits(self):
        result = self.run("Explain quantum computing")
        expect(result).to_complete_within(steps=10)
```

### Group Chat Mode

```python
agent1 = autogen.AssistantAgent(name="researcher", llm_config={...})
agent2 = autogen.AssistantAgent(name="writer", llm_config={...})

groupchat = autogen.GroupChat(agents=[agent1, agent2], messages=[])
manager = autogen.GroupChatManager(groupchat=groupchat)

adapter = AutoGenAdapter(
    assistant=agent1,
    user_proxy=user_proxy,
    group_chat_manager=manager,
    tools=["search"],
)


class GroupChatTest(AgentTest):
    agent = "group-chat"
    adapter = adapter

    def test_multi_agent_collaboration(self):
        result = self.run("Research and summarize the latest in AI safety")
        expect(result).to_complete_within(steps=15)
```

### How It Works

- Initiates a chat via `user_proxy.initiate_chat(assistant, message=prompt)`
- Collects messages from agent chat histories post-execution
- Parses `function_call` and `tool_calls` from message dicts
- Records each message as either a `tool_call` or `llm_response` step

---

## LangGraphAdapter

Test LangGraph compiled graphs.

### Installation

```bash
pip install agentbench[langgraph]
```

### Basic Usage (Prebuilt ReAct Agent)

```python
from agentbench import AgentTest, expect
from agentbench.adapters import LangGraphAdapter
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain.tools import tool


@tool
def search(query: str) -> str:
    """Search the web."""
    return f"Results for: {query}"

@tool
def calculator(expression: str) -> str:
    """Calculate."""
    return str(eval(expression))


model = ChatOpenAI(model="gpt-4o-mini")
graph = create_react_agent(model, [search, calculator])

adapter = LangGraphAdapter(graph, tools=["search", "calculator"])


class LangGraphReActTest(AgentTest):
    agent = "langgraph-react"
    adapter = adapter

    def test_searches(self):
        result = self.run("What is LangGraph?")
        expect(result).to_use_tool("search")
        expect(result).to_complete()

    def test_calculates(self):
        result = self.run("What is 17 * 23?")
        expect(result).to_use_tool("calculator")
        expect(result).to_complete()
```

### Custom Graph

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict


class State(TypedDict):
    messages: list
    next_action: str


def agent_node(state: State) -> State:
    return {"messages": state["messages"] + ["Agent thinking..."]}

def tools_node(state: State) -> State:
    return {"messages": state["messages"] + ["Tool result"]}

builder = StateGraph(State)
builder.add_node("agent", agent_node)
builder.add_node("tools", tools_node)
builder.add_edge("agent", "tools")
builder.add_edge("tools", "agent")
builder.set_entry_point("agent")

graph = builder.compile()

adapter = LangGraphAdapter(
    graph,
    tools=["search"],
    node_name_map={"tools": "search"},  # map node → tool name
)


class CustomGraphTest(AgentTest):
    agent = "custom-graph"
    adapter = adapter

    def test_executes_graph(self):
        result = self.run("Hello")
        expect(result).to_complete()
```

### How It Works

- Attempts streaming mode (`graph.stream()`) for step-by-step capture
- Falls back to `graph.invoke()` if streaming is unavailable
- Each node execution becomes a trajectory step
- Tool nodes become `tool_call` steps; agent nodes become `llm_response` steps
- `node_name_map` lets you remap node names to semantic tool names

---

## Choosing the Right Adapter

| Your Agent | Adapter | Key Benefit |
|---|---|---|
| HTTP API / microservice | `RawAPIAdapter(endpoint=...)` | No code changes needed |
| Python function / custom | `RawAPIAdapter(func=...)` | Easiest to start with |
| LangChain AgentExecutor | `LangChainAdapter` | Callback-based step recording |
| OpenAI Assistants | `OpenAIAdapter` | Thread/run management built-in |
| CrewAI Crew | `CrewAIAdapter` | Multi-agent task tracking |
| AutoGen conversation | `AutoGenAdapter` | Message history parsing |
| LangGraph graph | `LangGraphAdapter` | Streaming node capture |
