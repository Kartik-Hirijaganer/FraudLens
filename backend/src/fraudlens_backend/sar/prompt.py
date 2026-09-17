"""Summary: The versioned SAR prompt loader + PHI-safe prompt assembly (plan §7.3, §8.1, §16
Phase 7). `SarPromptTemplate.load` reads a semantic-versioned markdown template
(`config/llm/prompts/sar/<id>.md`, YAML front matter + static instruction body), and records the
template's `prompt_version` (`<id>@<semver>`) plus a `prompt_hash` (SHA-256 of the EXACT file
bytes) — so every SAR persists which prompt produced it and any edit to the template changes the
hash (auditable A/B + golden tests, plan §7.3). `build_messages` turns a closed `SarModelInput` into
the `[system, user]` chat messages: the system message is the static (hashed) template; the user
message is assembled from the structured non-PHI facts (band, probability, amount, rule hits, SHAP
drivers), the deterministic EVIDENCE CATALOG a draft must reference and match (release 0.5.0
Phase 2.2), and the digest-verified regulation block (RAG-as-data, plan §8.1). As
defense-in-depth the assembled user text is run through the deterministic core masker before it
leaves this module, so even a crafted free-text field cannot carry a PHI-shaped span into the
prompt (the "PHI masked before the prompt" guarantee, plan §7.8 — on top of the client's own mask).

Key classes:
- SarPromptMeta: the validated YAML front matter of a prompt template (version + description).
- SarPromptTemplate: a loaded, hashed SAR prompt template (system text + version + hash).

Key functions:
- build_messages: assemble the masked [system, user] chat messages for a SarInput.

Notes:
- The template body is STATIC (no interpolation), so the hash identifies the prompt independent of
  any one transaction's data — the dynamic facts live in the user message, not the versioned text.
- Messages are returned as plain role/content dicts so this module imports no provider types; the
  live drafter hands them to the guardrailed `fraudlens_llm` client, which masks again and prepends
  its own system-policy message.
- The default template is `v3`: it pre-registers the remediation prompted by the adverse live run
  and matches the fully closed response schema. `v1` and `v2` stay immutable for evidence lineage.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from fraudlens_backend.prompting import (
    PromptMeta,
    VersionedPrompt,
    load_versioned_prompt,
    split_front_matter,
)
from fraudlens_backend.sar.egress import SarModelInput
from fraudlens_backend.sar.evidence import (
    SarEvidenceCatalog,
    build_evidence_catalog,
    required_narrative_facts,
)
from fraudlens_backend.settings import find_config_dir
from fraudlens_core.phi import mask_text

DEFAULT_SAR_PROMPT_ID = "v4"


class SarPromptMeta(PromptMeta):
    """The validated YAML front matter of a SAR prompt template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(..., min_length=1, description="Semantic version of the prompt template.")
    description: str = Field(..., min_length=1, description="What this prompt version is for.")


class SarPromptTemplate(VersionedPrompt):
    """A loaded, content-hashed SAR prompt template (the system instructions + provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(..., min_length=1, description="Template file id, e.g. 'v1'.")
    meta: SarPromptMeta = Field(..., description="Parsed front-matter metadata.")
    system_text: str = Field(..., min_length=1, description="Static instruction body (the prompt).")
    prompt_version: str = Field(..., min_length=1, description="Recorded version: '<id>@<semver>'.")
    prompt_hash: str = Field(..., min_length=1, description="SHA-256 of the exact template bytes.")

    @classmethod
    def load(
        cls, template_id: str = DEFAULT_SAR_PROMPT_ID, *, config_dir: Path | None = None
    ) -> SarPromptTemplate:
        """Load + validate `config/llm/prompts/sar/<id>.md`; compute version + content hash."""
        base = config_dir or find_config_dir()
        path = base / "llm" / "prompts" / "sar" / f"{template_id}.md"
        loaded = load_versioned_prompt(
            path,
            template_id=template_id,
            meta_type=SarPromptMeta,
            prompt_label="SAR",
        )
        return cls(
            **loaded.model_dump(),
        )


def _split_front_matter(raw: str) -> tuple[SarPromptMeta, str]:
    """Split a '--- yaml --- body' template into validated metadata and its instruction body."""
    return split_front_matter(raw, meta_type=SarPromptMeta, prompt_label="SAR")


def build_messages(
    template: SarPromptTemplate, sar_input: SarModelInput
) -> list[dict[str, object]]:
    """Assemble the PHI-masked [system, user] chat messages for one SAR input."""
    user_text = mask_text(_render_user_content(sar_input)).value
    return [
        {"role": "system", "content": template.system_text},
        {"role": "user", "content": user_text},
    ]


def _render_user_content(sar_input: SarModelInput) -> str:
    """Render the structured, PHI-free facts + fenced regulation block into the user message."""
    probability_pct = f"{sar_input.fraud_probability * 100:.1f}%"
    catalog = build_evidence_catalog(sar_input)
    blocks = [
        "Draft a SAR for the following investigation.",
        "\n".join(
            (
                "Transaction facts:",
                f"- Case: {sar_input.case_alias}",
                f"- Subject: {sar_input.subject_alias}",
                f"- Counterparty: {sar_input.counterparty_alias}",
                f"- Risk band: {sar_input.risk_band.value}",
                f"- Model fraud probability: {probability_pct}",
                f"- Amount: {sar_input.transaction.amount} {sar_input.transaction.currency}",
                f"- Country: {sar_input.transaction.country}",
                f"- Channel: {sar_input.transaction.channel}",
                f"- Direction: {sar_input.transaction.direction.value}",
                f"- Occurred at: {sar_input.transaction.occurred_at.isoformat()}",
            )
        ),
        _render_rule_hits(sar_input),
        _render_top_features(sar_input),
        _render_evidence_catalog(catalog),
        _render_required_narrative_facts(catalog),
        _render_regulations(sar_input),
    ]
    return "\n\n".join(block for block in blocks if block)


def _render_rule_hits(sar_input: SarModelInput) -> str:
    """Render the deterministic rule indicators that fired (PHI-free reasons)."""
    if not sar_input.rule_hits:
        return "Rule indicators: none fired."
    lines = ["Rule indicators that fired:"]
    lines.extend(
        f"- {hit.rule_type.value} (severity {hit.severity}): {hit.reason}"
        for hit in sar_input.rule_hits
    )
    return "\n".join(lines)


def _render_top_features(sar_input: SarModelInput) -> str:
    """Render the top SHAP drivers with the direction each pushes the risk."""
    if not sar_input.shap_drivers:
        return ""
    lines = ["Top model risk drivers (SHAP):"]
    for feature in sar_input.shap_drivers:
        direction = "increases" if feature.shap_value >= 0 else "decreases"
        lines.append(f"- {feature.feature}={feature.value:g} {direction} risk")
    return "\n".join(lines)


def _render_evidence_catalog(catalog: SarEvidenceCatalog) -> str:
    """Render the closed evidence catalog every claim ref and asserted fact must come from."""
    if not catalog.facts:
        return "Evidence catalog: empty — assert no facts."
    lines = ["Evidence catalog (ref | value | as written):"]
    lines.extend(f"- {fact.ref} | {fact.value} | {fact.display}" for fact in catalog.facts)
    return "\n".join(lines)


def _render_required_narrative_facts(catalog: SarEvidenceCatalog) -> str:
    """Render the exact core refs/values required in the first generated claim."""
    lines = ["Required narrative facts (first claim; copy ref and value exactly):"]
    lines.extend(
        f"- {fact.ref} | {fact.value} | {fact.display}"
        for fact in required_narrative_facts(catalog)
    )
    return "\n".join(lines)


def _render_regulations(sar_input: SarModelInput) -> str:
    """Render the citable regulation ids plus the pre-fenced RAG-as-data excerpt block."""
    if not sar_input.regulations:
        return "Regulations: none available — cite none."
    lines = ["Regulations (cite ONLY these ids verbatim):"]
    lines.extend(
        (
            f"- {citation.citation_id}: {citation.title} ({citation.source})\n"
            f"  <regulation-data>{citation.snippet}</regulation-data>"
        )
        for citation in sar_input.regulations
    )
    return "\n".join(lines)
