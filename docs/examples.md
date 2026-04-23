# Examples Gallery

Real-world test suites for common agent types. Each example is a complete, runnable test class.

---

## 1. E-Commerce Checkout Agent

Test an agent that handles product search, cart management, payment, and returns.

```python
"""E-commerce checkout agent — behavioral test suite."""

from agentbench import AgentTest, expect, parametrize
from agentbench.adapters import RawAPIAdapter


def checkout_agent(prompt: str, context: dict | None = None) -> dict:
    """Simulated e-commerce checkout agent."""
    steps = []
    prompt_lower = prompt.lower()

    if "return" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "returns_api",
            "tool_input": {"action": "initiate_return"},
            "tool_output": "Return label generated - RR-12345",
        })
        steps.append({
            "action": "llm_response",
            "response": "I've initiated your return. A shipping label has been sent.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "buy" in prompt_lower or "order" in prompt_lower:
        import re
        sanitized = re.sub(r"\d{12,19}", "[REDACTED]", prompt)

        steps.append({
            "action": "tool_call",
            "tool_name": "product_search",
            "tool_input": {"query": sanitized},
            "tool_output": "Blue shirt, Size M - $29.99",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "add_to_cart",
            "tool_input": {"product_id": "SHIRT-M-BLUE", "quantity": 1},
            "tool_output": "Added to cart",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "payment_api",
            "tool_input": {"amount": 29.99, "currency": "USD"},
            "tool_output": "Payment successful - Order #12345",
        })
        steps.append({
            "action": "llm_response",
            "response": "Your blue shirt (Size M) has been ordered! Order #12345. Total: $29.99",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append({
            "action": "llm_response",
            "response": "I can help you buy products or process returns. What would you like?",
        })
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=checkout_agent,
    tools=["product_search", "add_to_cart", "payment_api", "returns_api"],
)


class ECommerceCheckoutTest(AgentTest):
    agent = "checkout-agent"
    adapter = adapter

    def test_buy_completes_checkout(self):
        """Full checkout flow should complete successfully."""
        result = self.run("Buy me a blue shirt, size M")
        expect(result).to_complete()
        expect(result).to_complete_within(steps=10)

    def test_buy_uses_payment_once(self):
        """Checkout should call the payment API exactly once."""
        result = self.run("Order a blue shirt")
        expect(result).to_use_tool("payment_api", times=1)

    def test_buy_follows_correct_workflow(self):
        """Checkout should follow: search → add to cart → pay."""
        result = self.run("Buy me a shirt")
        expect(result).to_follow_workflow(
            ["product_search", "add_to_cart", "payment_api"]
        )

    def test_no_pii_exposure(self):
        """Agent should never expose credit card numbers."""
        result = self.run("Buy a shirt, card: 4111111111111111")
        expect(result).to_not_expose("4111111111111111")

    def test_return_uses_returns_api(self):
        """Return requests should use the returns API."""
        result = self.run("I want to return my order")
        expect(result).to_use_tool("returns_api")
        expect(result).to_respond_with("return")

    def test_return_no_payment(self):
        """Returns should NOT trigger the payment API."""
        result = self.run("Return my blue shirt")
        expect(result).to_not_use_tool("payment_api")

    def test_general_query_no_payment(self):
        """General questions should not trigger payment."""
        result = self.run("What can you help me with?")
        expect(result).to_not_use_tool("payment_api")
        expect(result).to_complete()

    @parametrize("product", ["shirt", "shoes", "hat"])
    def test_various_products(self, product):
        """Agent should handle different product types."""
        result = self.run(f"Buy me a {product}")
        expect(result).to_use_tool("product_search")
        expect(result).to_complete()

    def test_handles_payment_failure(self):
        """Agent should retry when payment fails."""
        result = self.run(
            "Buy a shirt",
            inject_tool_failure="payment_api",
            fail_times=2,
        )
        expect(result).to_retry(max_attempts=3)
```

---

## 2. Customer Support Agent

Test an agent that handles support tickets, refunds, and escalations.

```python
"""Customer support agent — behavioral test suite."""

from agentbench import AgentTest, expect, parametrize
from agentbench.adapters import RawAPIAdapter


def support_agent(prompt: str, context: dict | None = None) -> dict:
    """Simulated customer support agent."""
    steps = []
    prompt_lower = prompt.lower()

    if "refund" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "lookup_order",
            "tool_input": {"query": prompt},
            "tool_output": "Order #ORD-789 found - $49.99 - Widget Pro",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "process_refund",
            "tool_input": {"order_id": "ORD-789", "amount": 49.99},
            "tool_output": "Refund initiated - REF-456",
        })
        steps.append({
            "action": "llm_response",
            "response": "I've processed your refund of $49.99. Refund ID: REF-456. "
                        "It will appear in 3-5 business days.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "speak" in prompt_lower or "manager" in prompt_lower or "escalate" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "escalate",
            "tool_input": {"reason": "Customer requested escalation"},
            "tool_output": "Ticket escalated - ESC-001",
        })
        steps.append({
            "action": "llm_response",
            "response": "I've escalated your case. A manager will contact you within 2 hours.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "order status" in prompt_lower or "where is" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "lookup_order",
            "tool_input": {"query": prompt},
            "tool_output": "Order #ORD-123 - Status: Shipped - ETA: Tomorrow",
        })
        steps.append({
            "action": "llm_response",
            "response": "Your order #ORD-123 has shipped and should arrive tomorrow!",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append({
            "action": "llm_response",
            "response": "I can help with orders, refunds, and account issues. "
                        "What do you need help with?",
        })
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=support_agent,
    tools=["lookup_order", "process_refund", "escalate"],
)


class CustomerSupportTest(AgentTest):
    agent = "support-agent"
    adapter = adapter

    def test_refund_flow(self):
        """Refund request should look up order and process refund."""
        result = self.run("I want a refund for my order ORD-789")
        expect(result).to_complete()
        expect(result).to_follow_workflow(["lookup_order", "process_refund"])
        expect(result).to_respond_with("refund")

    def test_refund_mentions_amount(self):
        """Refund response should include the amount."""
        result = self.run("Refund order ORD-789")
        expect(result).to_respond_with("$49.99")

    def test_escalation(self):
        """Escalation request should trigger escalate tool."""
        result = self.run("I want to speak to a manager")
        expect(result).to_use_tool("escalate")
        expect(result).to_respond_with("escalated")

    def test_order_status(self):
        """Order status query should look up the order."""
        result = self.run("Where is my order?")
        expect(result).to_use_tool("lookup_order")
        expect(result).to_respond_with("shipped")

    def test_no_unauthorized_refund(self):
        """General questions should not trigger refund processing."""
        result = self.run("What's your return policy?")
        expect(result).to_not_use_tool("process_refund")

    def test_no_pii_in_response(self):
        """Agent should not expose internal customer IDs."""
        result = self.run("Show me my account details for email john@example.com")
        expect(result).to_not_expose("john@example.com")

    def test_refund_with_lookup_failure(self):
        """Agent should handle order lookup failures gracefully."""
        result = self.run(
            "Refund my order",
            inject_tool_failure="lookup_order",
            fail_times=1,
        )
        expect(result).to_retry(max_attempts=2)

    @parametrize("complaint", [
        "I want a refund",
        "Give me my money back",
        "Return my purchase",
    ])
    def test_refund_intent_variations(self, complaint):
        """Agent should recognize different refund phrasings."""
        result = self.run(complaint)
        expect(result).to_use_tool("process_refund")

    def test_completes_quickly(self):
        """All interactions should complete within reasonable steps."""
        result = self.run("What can you help me with?")
        expect(result).to_complete_within(steps=5)
```

---

## 3. Data Analysis Agent

Test an agent that analyzes datasets, generates charts, and answers data questions.

```python
"""Data analysis agent — behavioral test suite."""

from agentbench import AgentTest, expect, parametrize
from agentbench.adapters import RawAPIAdapter


def data_agent(prompt: str, context: dict | None = None) -> dict:
    """Simulated data analysis agent."""
    steps = []
    prompt_lower = prompt.lower()

    if "chart" in prompt_lower or "plot" in prompt_lower or "visual" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "load_dataset",
            "tool_input": {"source": "default"},
            "tool_output": "Loaded 1000 rows, 12 columns",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "generate_chart",
            "tool_input": {"chart_type": "bar", "x": "category", "y": "value"},
            "tool_output": "chart_bar_001.png",
        })
        steps.append({
            "action": "llm_response",
            "response": "I've generated a bar chart showing values by category. "
                        "The chart is saved as chart_bar_001.png",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "summary" in prompt_lower or "describe" in prompt_lower or "statistics" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "load_dataset",
            "tool_input": {"source": "default"},
            "tool_output": "Loaded 1000 rows, 12 columns",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "run_analysis",
            "tool_input": {"analysis_type": "descriptive_statistics"},
            "tool_output": "Mean: 45.2, Median: 42.0, Std: 12.3",
        })
        steps.append({
            "action": "llm_response",
            "response": "Here's the statistical summary: Mean=45.2, Median=42.0, StdDev=12.3. "
                        "The data shows a roughly normal distribution.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "query" in prompt_lower or "filter" in prompt_lower or "find" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "load_dataset",
            "tool_input": {"source": "default"},
            "tool_output": "Loaded 1000 rows, 12 columns",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "run_query",
            "tool_input": {"query": prompt},
            "tool_output": "42 rows matched",
        })
        steps.append({
            "action": "llm_response",
            "response": "I found 42 rows matching your criteria. The results show "
                        "concentrated activity in the Q4 period.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append({
            "action": "llm_response",
            "response": "I can help you analyze data, generate charts, run queries, "
                        "and compute statistics. What would you like to do?",
        })
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=data_agent,
    tools=["load_dataset", "run_analysis", "run_query", "generate_chart"],
)


class DataAnalysisTest(AgentTest):
    agent = "data-analysis-agent"
    adapter = adapter

    def test_chart_generation(self):
        """Chart requests should load data and generate a chart."""
        result = self.run("Create a bar chart of sales by category")
        expect(result).to_complete()
        expect(result).to_follow_workflow(["load_dataset", "generate_chart"])
        expect(result).to_respond_with("chart")

    def test_statistical_summary(self):
        """Summary requests should compute statistics."""
        result = self.run("Give me a statistical summary of the data")
        expect(result).to_use_tool("run_analysis")
        expect(result).to_respond_with("Mean")

    def test_data_query(self):
        """Query requests should run a data query."""
        result = self.run("Find all records where revenue > 10000")
        expect(result).to_use_tool("run_query")
        expect(result).to_respond_with("42 rows")

    def test_always_loads_data(self):
        """Data-dependent requests should load the dataset first."""
        result = self.run("Describe the dataset")
        expect(result).to_follow_workflow(["load_dataset"])

    def test_no_chart_for_summary(self):
        """Summary requests should not generate charts."""
        result = self.run("Show me statistics")
        expect(result).to_not_use_tool("generate_chart")

    def test_handles_load_failure(self):
        """Agent should handle dataset loading failures."""
        result = self.run(
            "Analyze the data",
            inject_tool_failure="load_dataset",
            fail_times=1,
        )
        expect(result).to_retry(max_attempts=3)

    @parametrize("chart_type", ["bar chart", "line plot", "scatter plot", "pie chart"])
    def test_various_chart_types(self, chart_type):
        """Agent should handle different chart type requests."""
        result = self.run(f"Create a {chart_type} of sales")
        expect(result).to_use_tool("generate_chart")
        expect(result).to_complete()

    def test_completes_within_limits(self):
        """Analysis tasks should complete efficiently."""
        result = self.run("Summarize the quarterly revenue data")
        expect(result).to_complete_within(steps=10)
```

---

## 4. RAG/QA Agent

Test a retrieval-augmented generation agent that answers questions from a knowledge base.

```python
"""RAG/QA agent — behavioral test suite."""

from agentbench import AgentTest, expect, parametrize
from agentbench.adapters import RawAPIAdapter


def rag_agent(prompt: str, context: dict | None = None) -> dict:
    """Simulated RAG/QA agent."""
    steps = []
    prompt_lower = prompt.lower()

    steps.append({
        "action": "tool_call",
        "tool_name": "retrieve",
        "tool_input": {"query": prompt, "top_k": 5},
        "tool_output": "Retrieved 5 relevant documents (relevance: 0.92)",
    })

    if "pricing" in prompt_lower or "price" in prompt_lower or "cost" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "generate_answer",
            "tool_input": {"context": "pricing_docs", "query": prompt},
            "tool_output": "Generated answer from pricing documentation",
        })
        steps.append({
            "action": "llm_response",
            "response": "Based on our pricing docs, the Pro plan costs $29/month "
                        "and includes 10,000 API calls. Enterprise pricing is custom.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "api" in prompt_lower or "endpoint" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "generate_answer",
            "tool_input": {"context": "api_docs", "query": prompt},
            "tool_output": "Generated answer from API documentation",
        })
        steps.append({
            "action": "llm_response",
            "response": "The API endpoint is POST /api/v1/predict. "
                        "It accepts JSON with 'input' and 'model' fields.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "how do" in prompt_lower or "how to" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "generate_answer",
            "tool_input": {"context": "how_to_docs", "query": prompt},
            "tool_output": "Generated step-by-step answer",
        })
        steps.append({
            "action": "llm_response",
            "response": "Here's how to do it: First, navigate to Settings. "
                        "Then click on Integrations. Finally, add your API key.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append({
            "action": "tool_call",
            "tool_name": "generate_answer",
            "tool_input": {"query": prompt},
            "tool_output": "General answer generated",
        })
        steps.append({
            "action": "llm_response",
            "response": "I found some relevant information, but could you be more specific "
                        "about what you'd like to know?",
        })
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=rag_agent,
    tools=["retrieve", "generate_answer"],
)


class RAGQATest(AgentTest):
    agent = "rag-qa-agent"
    adapter = adapter

    def test_always_retrieves(self):
        """Every question should trigger retrieval first."""
        result = self.run("What is the pricing for the Pro plan?")
        expect(result).to_use_tool("retrieve")
        expect(result).to_follow_workflow(["retrieve"])

    def test_generates_answer(self):
        """Agent should generate an answer from retrieved context."""
        result = self.run("What is the pricing for the Pro plan?")
        expect(result).to_use_tool("generate_answer")
        expect(result).to_respond_with("$29")

    def test_api_question(self):
        """API questions should return technical details."""
        result = self.run("What is the API endpoint for predictions?")
        expect(result).to_respond_with("POST /api/v1/predict")

    def test_how_to_question(self):
        """How-to questions should provide step-by-step answers."""
        result = self.run("How do I set up the integration?")
        expect(result).to_respond_with("Settings")

    def test_retrieval_failure_handled(self):
        """Agent should handle retrieval service failures."""
        result = self.run(
            "What is the pricing?",
            inject_tool_failure="retrieve",
            fail_times=1,
        )
        expect(result).to_retry(max_attempts=3)

    def test_no_hallucinated_pii(self):
        """Agent should not invent personal information."""
        result = self.run("What is the CEO's personal phone number?")
        expect(result).to_not_expose("phone")
        expect(result).to_not_expose("email")

    @parametrize("question", [
        "What does the Pro plan cost?",
        "How much is the Enterprise plan?",
        "Is there a free tier?",
    ])
    def test_pricing_questions(self, question):
        """All pricing questions should retrieve and answer."""
        result = self.run(question)
        expect(result).to_use_tool("retrieve")
        expect(result).to_use_tool("generate_answer")
        expect(result).to_complete()

    def test_workflow_order(self):
        """Retrieval should always happen before answer generation."""
        result = self.run("Explain the API rate limits")
        expect(result).to_follow_workflow(["retrieve", "generate_answer"])

    def test_completes_quickly(self):
        """QA should complete within a few steps."""
        result = self.run("What is your refund policy?")
        expect(result).to_complete_within(steps=5)
```

---

## 5. Multi-Step Workflow Agent

Test an agent that orchestrates complex multi-step workflows (e.g., travel booking).

```python
"""Multi-step workflow agent — behavioral test suite."""

from agentbench import AgentTest, expect, parametrize
from agentbench.adapters import RawAPIAdapter


def travel_agent(prompt: str, context: dict | None = None) -> dict:
    """Simulated travel booking agent with complex workflows."""
    steps = []
    prompt_lower = prompt.lower()

    if "cancel" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "lookup_booking",
            "tool_input": {"query": prompt},
            "tool_output": "Booking #BK-789: NYC→Tokyo, Dec 15-22, $2,400",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "check_cancellation_policy",
            "tool_input": {"booking_id": "BK-789"},
            "tool_output": "Free cancellation until Dec 10",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "process_cancellation",
            "tool_input": {"booking_id": "BK-789", "reason": "customer_request"},
            "tool_output": "Cancelled. Refund of $2,400 in 5-7 days.",
        })
        steps.append({
            "action": "llm_response",
            "response": "Your booking #BK-789 has been cancelled. A refund of $2,400 "
                        "will be processed within 5-7 business days.",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "book" in prompt_lower or "reserve" in prompt_lower or "flight" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "search_flights",
            "tool_input": {"query": prompt},
            "tool_output": "Found 5 flights: JAL123 ($800), AA456 ($750), DL789 ($820)",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "search_hotels",
            "tool_input": {"destination": "Tokyo", "dates": "Dec 15-22"},
            "tool_output": "Found 3 hotels: Grand ($200/n), Palace ($150/n), Inn ($80/n)",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "verify_passport",
            "tool_input": {"action": "check_requirements"},
            "tool_output": "Passport valid. Visa-free entry for 90 days.",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "book_flight",
            "tool_input": {"flight": "AA456", "amount": 750},
            "tool_output": "Flight booked - Confirmation #FL-001",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "book_hotel",
            "tool_input": {"hotel": "Palace", "nights": 7, "amount": 1050},
            "tool_output": "Hotel booked - Confirmation #HT-001",
        })
        steps.append({
            "action": "tool_call",
            "tool_name": "send_confirmation",
            "tool_input": {"email": "customer@example.com"},
            "tool_output": "Confirmation email sent",
        })
        steps.append({
            "action": "llm_response",
            "response": "Your trip is booked! Flight AA456 (Confirmation #FL-001) and "
                        "Hotel Palace (Confirmation #HT-001). Total: $1,800. "
                        "Confirmation email sent!",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    elif "weather" in prompt_lower:
        steps.append({
            "action": "tool_call",
            "tool_name": "check_weather",
            "tool_input": {"destination": "Tokyo"},
            "tool_output": "Tokyo: 15°C, partly cloudy, 20% rain",
        })
        steps.append({
            "action": "llm_response",
            "response": "The weather in Tokyo is currently 15°C and partly cloudy "
                        "with a 20% chance of rain. I'd recommend bringing a light jacket!",
        })
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append({
            "action": "llm_response",
            "response": "I can help you book flights and hotels, check weather, "
                        "or manage existing bookings. What would you like?",
        })
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=travel_agent,
    tools=[
        "search_flights", "search_hotels", "verify_passport",
        "book_flight", "book_hotel", "send_confirmation",
        "lookup_booking", "check_cancellation_policy", "process_cancellation",
        "check_weather",
    ],
)


class TravelWorkflowTest(AgentTest):
    agent = "travel-agent"
    adapter = adapter

    def test_booking_workflow(self):
        """Full booking should follow the complete workflow."""
        result = self.run("Book me a flight and hotel to Tokyo for Dec 15-22")
        expect(result).to_complete()
        expect(result).to_follow_workflow([
            "search_flights",
            "search_hotels",
            "verify_passport",
            "book_flight",
            "book_hotel",
            "send_confirmation",
        ])

    def test_books_both_flight_and_hotel(self):
        """Booking should include both flight and hotel."""
        result = self.run("Book a trip to Tokyo")
        expect(result).to_use_tool("book_flight", times=1)
        expect(result).to_use_tool("book_hotel", times=1)

    def test_sends_confirmation(self):
        """Booking should end with a confirmation email."""
        result = self.run("Reserve a flight and hotel to Tokyo")
        expect(result).to_use_tool("send_confirmation")

    def test_verifies_passport(self):
        """International bookings should verify passport requirements."""
        result = self.run("Book a flight to Tokyo")
        expect(result).to_use_tool("verify_passport")

    def test_cancellation_workflow(self):
        """Cancellation should follow: lookup → check policy → cancel."""
        result = self.run("Cancel my booking BK-789")
        expect(result).to_follow_workflow([
            "lookup_booking",
            "check_cancellation_policy",
            "process_cancellation",
        ])
        expect(result).to_respond_with("refund")

    def test_no_booking_for_weather(self):
        """Weather queries should not trigger bookings."""
        result = self.run("What's the weather in Tokyo?")
        expect(result).to_not_use_tool("book_flight")
        expect(result).to_not_use_tool("book_hotel")
        expect(result).to_use_tool("check_weather")

    def test_handles_search_failure(self):
        """Agent should handle flight search failures."""
        result = self.run(
            "Book a flight to Tokyo",
            inject_tool_failure="search_flights",
            fail_times=2,
        )
        expect(result).to_retry(max_attempts=3)

    def test_handles_booking_failure(self):
        """Agent should handle booking failures gracefully."""
        result = self.run(
            "Book a trip to Tokyo",
            inject_tool_failure="book_flight",
            fail_times=1,
        )
        expect(result).to_have_no_errors()

    def test_no_pii_exposure(self):
        """Agent should not expose passport numbers or personal details."""
        result = self.run(
            "Book a trip, my passport is X12345678 and SSN is 123-45-6789"
        )
        expect(result).to_not_expose("123-45-6789")

    @parametrize("destination", ["Tokyo", "Paris", "London"])
    def test_various_destinations(self, destination):
        """Agent should handle different destinations."""
        result = self.run(f"Book a flight to {destination}")
        expect(result).to_use_tool("search_flights")
        expect(result).to_complete()

    def test_completes_in_reasonable_steps(self):
        """Complex workflows should still complete efficiently."""
        result = self.run("Book a complete trip to Tokyo")
        expect(result).to_complete_within(steps=15)

    def test_general_query_no_tools(self):
        """General questions should not call booking tools."""
        result = self.run("What can you help me with?")
        expect(result).to_not_use_tool("book_flight")
        expect(result).to_not_use_tool("book_hotel")
        expect(result).to_not_use_tool("process_cancellation")
```

---

## Running the Examples

Save any test file as `test_*.py` and run:

```bash
# Run all examples
agentbench run ./examples

# Run a single test file
agentbench run examples/test_checkout_agent.py

# Run with verbose output
agentbench run ./examples -v

# Filter specific tests
agentbench run ./examples -f "workflow"

# Generate a report
agentbench run ./examples -r report.json
agentbench report report.json -o report.html
```
