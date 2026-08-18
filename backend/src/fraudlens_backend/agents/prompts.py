"""Summary: Versioned agent prompt loading and PHI-safe message assembly.
Each role has an independently versioned static prompt under config/llm/prompts/agents.
The shared prompt loader supplies exact-file hashing, while dynamic inputs stay in
the user message and receive defense-in-depth PHI masking before the LLM client.

Key classes:
- AgentPromptTemplate: one loaded, hashed role prompt.

Key functions:
- build_agent_messages: assemble a masked system/user message pair.

Notes:
- Tool results are appended and fenced by the bounded runtime, not interpolated here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.prompting import PromptMeta, VersionedPrompt, load_versioned_prompt
from fraudlens_backend.settings import find_config_dir
from fraudlens_core.phi import mask_text
from fraudlens_llm import LlmMessage, Role


class AgentPromptTemplate(VersionedPrompt):
    """Loaded static instructions and provenance for one agent role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: AgentRole = Field(..., description="Role this prompt constrains.")
    meta: PromptMeta = Field(..., description="Validated prompt front matter.")

    @classmethod
    def load(
        cls,
        agent: AgentRole,
        template_id: str,
        *,
        config_dir: Path | None = None,
    ) -> AgentPromptTemplate:
        """Load one `config/llm/prompts/agents/<role>/<id>.md` template."""
        base = config_dir or find_config_dir()
        path = base / "llm" / "prompts" / "agents" / agent.value / f"{template_id}.md"
        loaded = load_versioned_prompt(
            path,
            template_id=template_id,
            meta_type=PromptMeta,
            prompt_label=f"Agent {agent.value}",
        )
        return cls(agent=agent, **loaded.model_dump())


def build_agent_messages(
    template: AgentPromptTemplate,
    user_content: str,
) -> list[LlmMessage]:
    """Build the static system and PHI-masked dynamic user messages for one agent."""
    masked = mask_text(user_content).value
    return [
        LlmMessage(role=Role.SYSTEM, content=template.system_text),
        LlmMessage(role=Role.USER, content=masked),
    ]
