"""Capability probes — tool use, reasoning, multi-step tasks, code."""

from agentbench.probes.base import Domain, Probe, Severity

CAPABILITY_PROBES: list[Probe] = [
    # ── Tool Use ──
    Probe(
        id="capability-tool-01",
        domain=Domain.CAPABILITY,
        category="tool_use",
        description="Appropriate tool selection",
        prompt="What's the current weather in Tokyo?",
        check="tool_selection",
        expected="Uses weather/search tool if available, or states limitation",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-tool-02",
        domain=Domain.CAPABILITY,
        category="tool_use",
        description="Sequential tool usage",
        prompt="Find a Mexican restaurant near me, then make a reservation for 2 "
               "at 7pm tonight.",
        check="tool_sequence",
        expected="Uses search then booking tool in correct order",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-tool-03",
        domain=Domain.CAPABILITY,
        category="tool_use",
        description="Tool parameter accuracy",
        prompt="Search for flights from JFK to LAX on December 25th, 2026.",
        check="tool_params",
        expected="Passes correct parameters if using a flight tool",
        severity=Severity.INFO,
    ),
    # ── Reasoning ──
    Probe(
        id="capability-reason-01",
        domain=Domain.CAPABILITY,
        category="reasoning",
        description="Multi-step arithmetic",
        prompt="A store has a 20% off sale. An item costs $85. Sales tax is 8.5%. "
               "What's the final price? Show your work.",
        check="correct_answer",
        expected="$73.48 (85 * 0.8 * 1.085 = 73.48)",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-reason-02",
        domain=Domain.CAPABILITY,
        category="reasoning",
        description="Logical deduction",
        prompt="All cats have tails. Fluffy is a cat. Whiskers has no tail. "
               "Can Whiskers be a cat? Explain.",
        check="correct_answer",
        expected="Correctly identifies contradiction with premises",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-reason-03",
        domain=Domain.CAPABILITY,
        category="reasoning",
        description="Constraint satisfaction",
        prompt="Schedule a 1-hour meeting for 4 people. Alice is free 9-12, "
               "Bob is free 10-3, Carol is free 1-5, Dave is free 11-2. "
               "Find a time that works for everyone.",
        check="correct_answer",
        expected="Identifies 11am-12pm or 1pm-2pm as valid slots",
        severity=Severity.INFO,
    ),
    # ── Multi-step Tasks ──
    Probe(
        id="capability-multistep-01",
        domain=Domain.CAPABILITY,
        category="multi_step",
        description="Multi-step research task",
        prompt="I need to plan a trip to Paris. Find flights, suggest 3 hotels "
               "in different price ranges, and list the top 5 attractions.",
        check="task_completion",
        expected="Addresses all three parts of the request",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-multistep-02",
        domain=Domain.CAPABILITY,
        category="multi_step",
        description="Iterative refinement",
        prompt="Write a short poem about autumn. Then improve it by making "
               "the imagery more vivid.",
        check="task_completion",
        expected="Provides both original and improved version",
        severity=Severity.INFO,
    ),
    # ── Code Generation ──
    Probe(
        id="capability-code-01",
        domain=Domain.CAPABILITY,
        category="code",
        description="Working code generation",
        prompt="Write a Python function that takes a list of integers and returns "
               "the two numbers that sum to a given target. Include tests.",
        check="code_correctness",
        expected="Produces working code with correct logic",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-code-02",
        domain=Domain.CAPABILITY,
        category="code",
        description="Code debugging",
        prompt="This Python code has a bug. Find it:\n"
               "def factorial(n):\n    if n == 0:\n        return 0\n"
               "    return n * factorial(n-1)",
        check="correct_answer",
        expected="Identifies base case should return 1, not 0",
        severity=Severity.INFO,
    ),
    # ── Instruction Following ──
    Probe(
        id="capability-instruct-01",
        domain=Domain.CAPABILITY,
        category="instruction",
        description="Exact format compliance",
        prompt="List exactly 5 European capitals, numbered 1-5, "
               "with no additional text.",
        check="format_compliance",
        expected="Exactly 5 numbered items, no preamble or postscript",
        severity=Severity.INFO,
    ),
    Probe(
        id="capability-instruct-02",
        domain=Domain.CAPABILITY,
        category="instruction",
        description="Negative instruction",
        prompt="Explain quantum computing WITHOUT using the words "
               "'quantum', 'physics', 'particle', or 'science'.",
        check="instruction_compliance",
        expected="Explains without using any of the forbidden words",
        severity=Severity.INFO,
    ),
]
