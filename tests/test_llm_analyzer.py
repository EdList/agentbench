from agentbench.scanner.llm_analyzer import redact_sensitive_evidence


def test_redacts_secrets_credentials_and_pii_before_external_analysis():
    source = (
        "OPENAI_API_KEY=sk-live-super-secret-token-123456789 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature "
        "password=hunter2 email=alice@example.com ssn=123-45-6789 "
        "card=4111 1111 1111 1111"
    )

    redacted = redact_sensitive_evidence(source)

    for secret in (
        "sk-live-super-secret-token-123456789",
        "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "hunter2",
        "alice@example.com",
        "123-45-6789",
        "4111 1111 1111 1111",
    ):
        assert secret not in redacted
    assert "[REDACTED" in redacted
