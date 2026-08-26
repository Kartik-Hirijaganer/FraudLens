"""Summary: Fail-closed validation for model-requested tool arguments.
Tool definitions are checked as JSON Schema before any provider call, and tool
arguments are constrained to declared schemas without granting arbitrary egress.

Key classes:
- (none)

Key functions:
- validate_tool_definitions: Validate tool schemas, uniqueness, and tool choice.
- validate_tool_calls: Validate calls against declared schemas and URL policy.
- validate_response_schema: Validate a requested structured-output schema.

Notes:
- URL-shaped arguments are denied unless their hostname is explicitly allowlisted.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Mapping, Sequence
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from fraudlens_llm.exceptions import GuardrailError
from fraudlens_llm.models import ToolCall, ToolDefinition

_BUILTIN_TOOL_CHOICES = frozenset({"auto", "none", "required"})
_NETWORK_SCHEMES = frozenset({"http", "https"})


def validate_tool_definitions(
    tools: Sequence[ToolDefinition],
    *,
    tool_choice: str | None,
) -> None:
    """Validate unique tool names, their schemas, and an optional selection."""
    names: set[str] = set()
    for tool in tools:
        if tool.name in names:
            raise GuardrailError("Tool definitions must use unique names")
        names.add(tool.name)
        try:
            Draft202012Validator.check_schema(tool.parameters)
        except SchemaError as exc:
            raise GuardrailError(f"Tool '{tool.name}' declares an invalid JSON Schema") from exc
    if tool_choice is not None and tool_choice not in _BUILTIN_TOOL_CHOICES | names:
        raise GuardrailError("tool_choice must name a declared tool or a supported policy")
    if tool_choice not in {None, "none"} and not tools:
        raise GuardrailError("tool_choice requires at least one tool definition")


def validate_tool_calls(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[ToolDefinition],
    *,
    allowed_url_hosts: Collection[str] = (),
) -> None:
    """Validate tool calls against declarations and the fail-closed URL policy."""
    definitions = {tool.name: tool for tool in tools}
    normalized_hosts = {host.rstrip(".").lower() for host in allowed_url_hosts}
    for tool_call in tool_calls:
        definition = definitions.get(tool_call.name)
        if definition is None:
            raise GuardrailError("Model requested an undeclared tool")
        try:
            Draft202012Validator(definition.parameters).validate(tool_call.arguments)
        except ValidationError as exc:
            raise GuardrailError(
                f"Tool '{tool_call.name}' arguments failed schema validation"
            ) from exc
        _validate_argument_urls(tool_call.arguments, allowed_url_hosts=normalized_hosts)


def validate_response_schema(response_schema: Mapping[str, object] | None) -> None:
    """Validate a structured-output JSON Schema before any provider call."""
    if response_schema is None:
        return
    try:
        Draft202012Validator.check_schema(response_schema)
    except SchemaError as exc:
        raise GuardrailError("response_schema must be a valid JSON Schema") from exc


def _validate_argument_urls(
    value: object,
    *,
    allowed_url_hosts: Collection[str],
) -> None:
    """Recursively reject private, file, and non-allowlisted URL-shaped values."""
    if isinstance(value, str):
        _validate_url_value(value, allowed_url_hosts=allowed_url_hosts)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _validate_argument_urls(nested, allowed_url_hosts=allowed_url_hosts)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_argument_urls(nested, allowed_url_hosts=allowed_url_hosts)


def _validate_url_value(value: str, *, allowed_url_hosts: Collection[str]) -> None:
    """Validate one string only when it is shaped like a URL."""
    stripped = value.strip()
    parsed = urlparse(stripped)
    if not parsed.scheme and not stripped.startswith("//"):
        return
    if parsed.scheme.lower() not in _NETWORK_SCHEMES:
        raise GuardrailError("Tool arguments may not contain non-HTTPS URL schemes")
    hostname = parsed.hostname
    if hostname is None:
        raise GuardrailError("Tool arguments contain an invalid URL")
    normalized_host = hostname.rstrip(".").lower()
    if _is_private_ip(normalized_host):
        raise GuardrailError("Tool arguments may not target private or reserved IP addresses")
    if normalized_host not in allowed_url_hosts:
        raise GuardrailError("Tool arguments may not target a non-allowlisted URL")


def _is_private_ip(hostname: str) -> bool:
    """Return whether a hostname is an IP literal outside public unicast space."""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return not address.is_global
