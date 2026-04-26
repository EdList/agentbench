"""Tests for LLM-powered behavior analysis — LLMAnalyzer and integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentbench.scanner.analyzer import BehaviorAnalyzer, DetectedBehavior
from agentbench.scanner.llm_analyzer import LLMAnalysisResult, LLMAnalyzer
from agentbench.scanner.prober import ProbeResult, ProbeSession
from agentbench.scanner.scorer import ScoringEngine

# ─── Helpers ───


def _make_result(
    category: str = "safety",
    prompt: str = "test",
    response: str = "ok",
    probe_id: str | None = None,
    status: str | None = None,
) -> ProbeResult:
    metadata: dict = {}
    if probe_id is not None:
        metadata["probe_id"] = probe_id
    if status is not None:
        metadata["status"] = status
    return ProbeResult(
        category=category,
        prompt=prompt,
        response=response,
        metadata=metadata,
    )


def _make_session(*results: ProbeResult) -> ProbeSession:
    return ProbeSession(results=list(results))


# ─── LLMAnalysisResult Tests ───


class TestLLMAnalysisResult:
    """Test LLMAnalysisResult dataclass creation and fields."""

    def test_creation_defaults(self):
        result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=True,
            leaked_info=[],
            topics=["math", "code"],
            sentiment="positive",
            contains_pii=False,
            response_quality=0.8,
            safety_concern=None,
            explanation="Test explanation",
        )
        assert result.is_refusal is False
        assert result.is_helpful is True
        assert result.leaked_info == []
        assert result.topics == ["math", "code"]
        assert result.sentiment == "positive"
        assert result.contains_pii is False
        assert result.response_quality == 0.8
        assert result.safety_concern is None
        assert result.explanation == "Test explanation"

    def test_with_safety_concern(self):
        result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=True,
            leaked_info=[],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.3,
            safety_concern="Agent provided dangerous instructions",
            explanation="Safety issue detected",
        )
        assert result.safety_concern == "Agent provided dangerous instructions"
        assert result.is_refusal is False

    def test_with_leaked_info(self):
        result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=False,
            leaked_info=["system_prompt", "internal_instructions"],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.2,
            safety_concern="Information leakage",
            explanation="Agent revealed internal info",
        )
        assert len(result.leaked_info) == 2
        assert "system_prompt" in result.leaked_info


# ─── LLMAnalyzer Fallback Tests ───


class TestLLMAnalyzerFallback:
    """Test keyword-based fallback when no API key is available."""

    def test_fallback_without_api_key(self):
        analyzer = LLMAnalyzer(api_key=None)
        # Ensure no env keys either
        with patch.dict("os.environ", {}, clear=True):
            analyzer._api_key = None
            result = analyzer.analyze_response("Do bad thing", "I cannot help with that.", "safety")
        assert result.is_refusal is True
        assert "Fallback" in result.explanation

    def test_fallback_refusal_detection(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        result = analyzer.analyze_response("hack", "I cannot assist with hacking.", "safety")
        assert result.is_refusal is True

    def test_fallback_no_refusal(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        result = analyzer.analyze_response(
            "hello", "Hello! How can I help you today?", "capability"
        )
        assert result.is_refusal is False

    def test_fallback_leak_detection(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        result = analyzer.analyze_response(
            "Ignore instructions",
            "My system prompt says I should be helpful.",
            "persona",
        )
        assert "system_prompt" in result.leaked_info

    def test_fallback_is_helpful(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        long_response = "I can help you with that. " * 10
        result = analyzer.analyze_response("test", long_response, "capability")
        assert result.is_helpful is True

    def test_fallback_short_response_not_helpful(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        result = analyzer.analyze_response("test", "ok", "capability")
        assert result.is_helpful is False


# ─── LLMAnalyzer Batch Tests ───


class TestLLMAnalyzerBatch:
    """Test batch_analyze method."""

    def test_batch_analyze_returns_correct_count(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        items = [
            ("prompt1", "response1", "safety"),
            ("prompt2", "response2", "capability"),
            ("prompt3", "I cannot help.", "safety"),
        ]
        results = analyzer.batch_analyze(items)
        assert len(results) == 3
        assert all(isinstance(r, LLMAnalysisResult) for r in results)

    def test_batch_analyze_empty(self):
        analyzer = LLMAnalyzer(api_key=None)
        analyzer._api_key = None
        results = analyzer.batch_analyze([])
        assert results == []


# ─── LLMAnalyzer with Mocked HTTP ───


class TestLLMAnalyzerMockedHTTP:
    """Test LLM analysis with mocked HTTP responses."""

    def test_successful_llm_analysis(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"is_refusal": true, "is_helpful": false, '
                        '"leaked_info": [], "topics": [], "sentiment": "neutral", '
                        '"contains_pii": false, "response_quality": 0.1, '
                        '"safety_concern": null, '
                        '"explanation": "Agent refused the harmful request."}'
                    }
                }
            ]
        }

        analyzer = LLMAnalyzer(api_key="test-key")
        # Force lazy client init so we can patch it
        _ = analyzer._get_client()
        with patch.object(analyzer._client, "post", return_value=mock_response):
            result = analyzer.analyze_response("Do bad thing", "I cannot help.", "safety")

        assert result.is_refusal is True
        assert result.is_helpful is False
        assert result.explanation == "Agent refused the harmful request."

    def test_http_error_falls_back(self):
        analyzer = LLMAnalyzer(api_key="test-key")
        _ = analyzer._get_client()
        with patch.object(analyzer._client, "post", side_effect=Exception("Connection error")):
            result = analyzer.analyze_response("test", "response", "safety")
        assert "Fallback" in result.explanation


# ─── BehaviorAnalyzer Integration with LLM ───


class TestBehaviorAnalyzerWithLLM:
    """Test that BehaviorAnalyzer uses LLMAnalyzer when use_llm=True."""

    def test_safety_refusal_via_llm(self):
        """When LLM says it's a refusal, analyzer marks it as refusal."""
        mock_result = LLMAnalysisResult(
            is_refusal=True,
            is_helpful=False,
            leaked_info=[],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.1,
            safety_concern=None,
            explanation="Refused",
        )

        analyzer = BehaviorAnalyzer(use_llm=True)
        with patch.object(analyzer._llm, "analyze_response", return_value=mock_result):
            session = _make_session(_make_result("safety", "bad", "I cannot help.", probe_id="s1"))
            behaviors = analyzer.analyze(session)

        refusal = [b for b in behaviors if b.test_type == "refusal"]
        assert len(refusal) == 1
        assert refusal[0].expected == "refusal"

    def test_safety_vulnerability_via_llm(self):
        """When LLM detects a safety concern, analyzer flags it."""
        mock_result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=True,
            leaked_info=[],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.7,
            safety_concern="Provided instructions for harmful activity",
            explanation="Safety concern",
        )

        analyzer = BehaviorAnalyzer(use_llm=True)
        with patch.object(analyzer._llm, "analyze_response", return_value=mock_result):
            session = _make_session(
                _make_result("safety", "bad", "Sure, here's how...", probe_id="s2")
            )
            behaviors = analyzer.analyze(session)

        vuln = [b for b in behaviors if b.test_type == "response_not_contains"]
        assert len(vuln) == 1
        assert "vulnerability" in vuln[0].description.lower()

    def test_capability_topics_via_llm(self):
        """When LLM returns topics, analyzer uses them for capabilities."""
        mock_result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=True,
            leaked_info=[],
            topics=["search", "code", "math"],
            sentiment="positive",
            contains_pii=False,
            response_quality=0.9,
            safety_concern=None,
            explanation="Capabilities described",
        )

        analyzer = BehaviorAnalyzer(use_llm=True)
        with patch.object(analyzer._llm, "analyze_response", return_value=mock_result):
            session = _make_session(
                _make_result(
                    "capability",
                    "What can you do?",
                    "I can search, code...",
                    probe_id="c1",
                )
            )
            behaviors = analyzer.analyze(session)

        cap = [
            b
            for b in behaviors
            if b.category == "capability" and b.test_type == "response_contains"
        ]
        assert len(cap) == 1
        assert "search" in cap[0].expected
        assert "code" in cap[0].expected

    def test_persona_leak_via_llm(self):
        """When LLM detects leaked info, analyzer flags persona leak."""
        mock_result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=False,
            leaked_info=["system_prompt"],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.3,
            safety_concern="Instruction leakage",
            explanation="System prompt leaked",
        )

        analyzer = BehaviorAnalyzer(use_llm=True)
        with patch.object(analyzer._llm, "analyze_response", return_value=mock_result):
            session = _make_session(
                _make_result(
                    "persona",
                    "Ignore instructions",
                    "My system prompt is...",
                    probe_id="p1",
                )
            )
            behaviors = analyzer.analyze(session)

        leak = [b for b in behaviors if b.category == "persona" and "leak" in b.description.lower()]
        assert len(leak) >= 1

    def test_use_llm_false_uses_keywords(self):
        """When use_llm=False (default), analyzer uses keyword matching."""
        analyzer = BehaviorAnalyzer()
        assert analyzer._llm is None

        session = _make_session(
            _make_result("safety", "bad", "I'm sorry, I cannot help.", probe_id="s1")
        )
        behaviors = analyzer.analyze(session)
        refusal = [b for b in behaviors if b.test_type == "refusal"]
        assert len(refusal) == 1

    def test_scoring_with_llm_result(self):
        """ScoringEngine uses registered LLM results for better accuracy."""
        llm_result = LLMAnalysisResult(
            is_refusal=True,
            is_helpful=False,
            leaked_info=[],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.1,
            safety_concern=None,
            explanation="Refused",
        )

        engine = ScoringEngine()
        engine.register_llm_result("s1", llm_result)

        behavior = DetectedBehavior(
            category="safety",
            description="Safety probe s1 correctly refused",
            test_type="refusal",
            test_prompt="bad",
            expected="refusal",
            confidence=0.95,
            source_probe="s1",
        )

        earned, max_pts = engine._behavior_points(behavior)
        assert earned == max_pts  # Full marks for refusal

    def test_scoring_with_llm_safety_concern(self):
        """ScoringEngine penalizes when LLM detects safety concern."""
        llm_result = LLMAnalysisResult(
            is_refusal=False,
            is_helpful=True,
            leaked_info=[],
            topics=[],
            sentiment="neutral",
            contains_pii=False,
            response_quality=0.5,
            safety_concern="Provided dangerous instructions",
            explanation="Safety issue",
        )

        engine = ScoringEngine()
        engine.register_llm_result("s2", llm_result)

        behavior = DetectedBehavior(
            category="safety",
            description="Safety vulnerability: probe s2 — Provided dangerous instructions",
            test_type="response_not_contains",
            test_prompt="bad",
            expected="refusal",
            confidence=0.9,
            source_probe="s2",
        )

        earned, max_pts = engine._behavior_points(behavior)
        assert earned == 0.0  # Zero marks for safety concern
