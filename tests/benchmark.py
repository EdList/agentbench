#!/usr/bin/env python
"""Performance benchmark — verifies 1000 tests run in under 5 minutes (mocked agents).

This is a one-off benchmark script, not a unit test. Run with:
    python tests/benchmark.py
"""

import os
import sys
import time

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentbench.adapters.raw_api import RawAPIAdapter
from agentbench.core.runner import TestRunner
from agentbench.core.test import AgentTest


def _fast_adapter():
    """Minimal overhead adapter that doesn't do any real work."""
    call_count = 0

    def fast_agent(prompt: str, context=None):
        nonlocal call_count
        call_count += 1
        return {
            "response": f"ok:{call_count}",
            "steps": [{"action": "llm_response", "response": f"ok:{call_count}"}],
        }

    return RawAPIAdapter(func=fast_agent)


def main():
    print("=" * 60)
    print("  AgentBench Performance Benchmark")
    print("  Target: 1000 tests in < 300 seconds")
    print("=" * 60)

    # Generate a suite with many test methods dynamically
    class BenchmarkSuite(AgentTest):
        agent = "benchmark"
        adapter = _fast_adapter()

    # Dynamically add 100 test methods (each run 10x via parametrize = 1000 tests)
    from agentbench.core.parametrize import parametrize

    for i in range(100):
        def make_test(idx):
            @parametrize("q", [f"q{j}" for j in range(10)])
            def test_n(self, q):
                result = self.run(q)
                # Minimal assertions
                assert result is not None
            test_n.__name__ = f"test_{idx:03d}"
            return test_n
        setattr(BenchmarkSuite, f"test_{i:03d}", make_test(i))

    # Run the benchmark
    runner = TestRunner(config={"verbose": False})
    print("\nRunning 1000 tests (100 methods × 10 params)...")
    start = time.time()
    result = runner.run_suite(BenchmarkSuite)
    elapsed = time.time() - start

    print("\nResults:")
    print(f"  Total tests:  {result.total}")
    print(f"  Passed:       {result.passed}")
    print(f"  Failed:       {result.failed}")
    print(f"  Duration:     {elapsed:.2f}s")
    print(f"  Tests/sec:    {result.total / elapsed:.1f}")
    print(f"  Avg per test: {elapsed / result.total * 1000:.1f}ms")

    if result.total != 1000:
        print(f"\n⚠ WARNING: Expected 1000 tests, ran {result.total}")

    if elapsed > 300:
        print(f"\n✗ FAIL: {elapsed:.1f}s exceeds 300s limit")
        return 1
    else:
        print(f"\n✓ PASS: {elapsed:.1f}s is within 300s limit")
        return 0


if __name__ == "__main__":
    sys.exit(main())
