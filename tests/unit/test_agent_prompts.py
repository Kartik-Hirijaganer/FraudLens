"""Unit tests for versioned multi-agent prompts and PHI-safe message assembly."""

from __future__ import annotations

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.prompts import AgentPromptTemplate, build_agent_messages
from fraudlens_llm import GuardrailDecision, Strictness, TaskType
from fraudlens_llm.security.prompt_risk import scan_prompt_risk


def test_all_agent_prompts_load_with_stable_provenance_and_no_risk_traps() -> None:
    hashes: set[str] = set()
    for role in AgentRole:
        prompt = AgentPromptTemplate.load(role, "v1")
        assert prompt.agent is role
        assert prompt.prompt_version == "v1@1.0.0"
        assert len(prompt.prompt_hash) == 64
        assert (
            scan_prompt_risk(
                prompt.system_text,
                strictness=Strictness.BLOCK,
                task_type=TaskType.ANALYSIS,
            ).decision
            is GuardrailDecision.ALLOW
        )
        hashes.add(prompt.prompt_hash)
    assert len(hashes) == len(AgentRole)


def test_agent_messages_mask_phi_before_the_llm_client() -> None:
    prompt = AgentPromptTemplate.load(AgentRole.EVIDENCE_INVESTIGATOR, "v1")
    messages = build_agent_messages(
        prompt,
        "Contact analyst@example.com about SSN 123-45-6789.",
    )

    assert [message.role.value for message in messages] == ["system", "user"]
    assert messages[1].content is not None
    assert "analyst@example.com" not in messages[1].content
    assert "123-45-6789" not in messages[1].content
    assert "[REDACTED_EMAIL]" in messages[1].content
