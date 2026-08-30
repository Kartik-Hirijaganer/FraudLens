"""Unit tests for LLM tool boundaries and fail-closed argument validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fraudlens_llm import GuardrailError, LlmMessage, Role, ToolCall, ToolDefinition
from fraudlens_llm.security.tools import (
    validate_response_schema,
    validate_tool_calls,
    validate_tool_definitions,
)


def _tool(
    *,
    name: str = "lookup",
    parameters: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Read a governed record by identifier.",
        parameters=parameters
        or {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "links": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
    )


def test_message_roles_enforce_tool_payload_contract() -> None:
    tool_call = ToolCall(id="call-1", name="lookup", arguments={"record_id": "rec-1"})

    assert LlmMessage(role=Role.ASSISTANT, tool_calls=(tool_call,)).content is None
    assert LlmMessage(role=Role.TOOL, tool_call_id="call-1", content="result").role == "tool"
    with pytest.raises(ValidationError):
        LlmMessage(role=Role.USER, tool_calls=(tool_call,))
    with pytest.raises(ValidationError):
        LlmMessage(role=Role.USER, content="hello", tool_call_id="call-1")
    with pytest.raises(ValidationError):
        LlmMessage(role=Role.TOOL, content="result")
    with pytest.raises(ValidationError):
        LlmMessage(role=Role.ASSISTANT)


def test_tool_definition_preflight_rejects_duplicates_schema_and_choice() -> None:
    tool = _tool()

    validate_tool_definitions([tool], tool_choice="lookup")
    validate_response_schema({"type": "object"})
    with pytest.raises(GuardrailError, match="unique"):
        validate_tool_definitions([tool, tool], tool_choice=None)
    with pytest.raises(GuardrailError, match="invalid JSON Schema"):
        validate_tool_definitions(
            [_tool(parameters={"type": "not-a-json-schema-type"})],
            tool_choice=None,
        )
    with pytest.raises(GuardrailError, match="tool_choice"):
        validate_tool_definitions([tool], tool_choice="missing")
    with pytest.raises(GuardrailError, match="requires"):
        validate_tool_definitions([], tool_choice="required")
    with pytest.raises(GuardrailError, match="response_schema"):
        validate_response_schema({"type": "not-a-json-schema-type"})


def test_tool_call_validation_rejects_undeclared_and_bad_arguments() -> None:
    tool = _tool()

    with pytest.raises(GuardrailError, match="undeclared"):
        validate_tool_calls(
            [ToolCall(id="call-1", name="other", arguments={})],
            [tool],
        )
    with pytest.raises(GuardrailError, match="schema validation"):
        validate_tool_calls(
            [ToolCall(id="call-1", name="lookup", arguments={"record_id": 3})],
            [tool],
        )


def test_tool_url_policy_allows_only_explicit_public_hosts() -> None:
    tool = _tool()
    allowed = ToolCall(
        id="call-1",
        name="lookup",
        arguments={
            "record_id": "rec-1",
            "links": ["https://api.example.com/records/rec-1"],
        },
    )

    validate_tool_calls([allowed], [tool], allowed_url_hosts={"api.example.com"})
    for blocked in (
        "https://127.0.0.1/private",
        "https://[::1]/private",
        "file:///etc/passwd",
        "ftp://api.example.com/file",
        "https:///missing-host",
        "https://other.example.com/records/rec-1",
    ):
        with pytest.raises(GuardrailError):
            validate_tool_calls(
                [
                    ToolCall(
                        id="call-1",
                        name="lookup",
                        arguments={"record_id": blocked},
                    )
                ],
                [tool],
                allowed_url_hosts={"api.example.com"},
            )
