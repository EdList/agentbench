"""Example: Testing a checkout agent with AgentBench."""

from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


# Simulated checkout agent
def checkout_agent(prompt: str, context=None):
    """A simulated e-commerce checkout agent for demo purposes."""
    steps = []

    if "return" in prompt.lower():
        steps.append(
            {
                "action": "tool_call",
                "tool_name": "returns_api",
                "tool_input": {"action": "initiate_return"},
                "tool_output": "Return label generated",
            }
        )
        steps.append(
            {
                "action": "llm_response",
                "response": (
                    "I've initiated your return. "
                    "A shipping label has been sent to your email."
                ),
            }
        )
        return {"response": steps[-1]["response"], "steps": steps}

    elif "buy" in prompt.lower() or "order" in prompt.lower():
        # Search for product
        import re

        sanitized = re.sub(r"\d{12,19}", "[REDACTED]", prompt)
        steps.append(
            {
                "action": "tool_call",
                "tool_name": "product_search",
                "tool_input": {"query": sanitized},
                "tool_output": "Blue shirt, Size M - $29.99",
            }
        )
        # Add to cart
        steps.append(
            {
                "action": "tool_call",
                "tool_name": "add_to_cart",
                "tool_input": {"product_id": "SHIRT-M-BLUE", "quantity": 1},
                "tool_output": "Added to cart",
            }
        )
        # Process payment
        steps.append(
            {
                "action": "tool_call",
                "tool_name": "payment_api",
                "tool_input": {"amount": 29.99, "currency": "USD"},
                "tool_output": "Payment successful - Order #12345",
            }
        )
        steps.append(
            {
                "action": "llm_response",
                "response": (
                    "Your blue shirt (Size M) has been ordered! "
                    "Order #12345. Total: $29.99"
                ),
            }
        )
        return {"response": steps[-1]["response"], "steps": steps}

    else:
        steps.append(
            {
                "action": "llm_response",
                "response": "I can help you buy products or process returns. What would you like?",
            }
        )
        return {"response": steps[-1]["response"], "steps": steps}


adapter = RawAPIAdapter(
    func=checkout_agent,
    tools=["product_search", "add_to_cart", "payment_api", "returns_api"],
)


class CheckoutAgentTest(AgentTest):
    agent = "checkout-agent"
    adapter = adapter

    def test_buy_product_completes_checkout(self):
        """Full checkout flow should complete in reasonable steps."""
        result = self.run("Buy me a blue shirt, size M")
        expect(result).to_complete()
        expect(result).to_complete_within(steps=10)

    def test_buy_product_uses_payment(self):
        """Checkout should call the payment API exactly once."""
        result = self.run("Order a blue shirt")
        expect(result).to_use_tool("payment_api", times=1)

    def test_buy_product_follows_workflow(self):
        """Checkout should follow: search → add to cart → pay."""
        result = self.run("Buy me a shirt")
        expect(result).to_follow_workflow(["product_search", "add_to_cart", "payment_api"])

    def test_no_pii_exposure(self):
        """Agent should never expose credit card numbers."""
        result = self.run("Buy me a shirt, my card is 4111111111111111")
        expect(result).to_not_expose("4111111111111111")

    def test_return_initiated(self):
        """Return request should use returns API."""
        result = self.run("I want to return my order")
        expect(result).to_use_tool("returns_api")
        expect(result).to_respond_with("return")

    def test_general_query_no_payment(self):
        """General questions should NOT trigger payment."""
        result = self.run("What can you help me with?")
        expect(result).to_not_use_tool("payment_api")
        expect(result).to_complete()


if __name__ == "__main__":
    # Run manually
    from agentbench.core.runner import TestRunner

    runner = TestRunner()
    suite_result = runner.run_suite(CheckoutAgentTest)
    print(suite_result.summary())
