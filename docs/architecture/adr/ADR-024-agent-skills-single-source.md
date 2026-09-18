# ADR-024 — Agent skills: one canonical source with a generated Codex mirror

- **Status:** Accepted
- **Date:** 2026-09-13
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  `plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` (retired; see [retired-plans.md](../retired-plans.md#retired-plans))

## Context

FraudLens supports Claude Code and Codex, but repository instructions had two hand-copied skill
trees and three Claude-only command wrappers. The copies could drift in safety-critical details such
as cloud permission gates, synthetic-only data boundaries, budget admission, evidence requirements,
and teardown verification. The migrated Codex command wrappers also used `source-command-*` names,
which created a second naming surface without adding behavior.

Later phases depend on repeatable operational procedures for paid Azure experiments, vLLM
benchmarking, Kubernetes deployment, quality/privacy review, architecture decisions, module splits,
and design review. Those procedures must remain repository-specific: a generic cloud skill cannot
weaken Golden Rule 7, change Infisical's single `prod` environment, broaden AWS into a deploy target,
or reinterpret kind evidence as a live AKS deployment.

## Decision

`.claude/skills/<name>/` is the sole human-authored project-skill tree. Every directory contains a
`SKILL.md` and `agents/openai.yaml`. `scripts/lib/skills.py` validates the skill name against its
folder, bounds discovery descriptions to 1,024 characters and Markdown bodies to 500 lines, and
requires a non-blank `interface.display_name` and `default_prompt`, and rejects any prompt that
names the generated `.agents/` mirror — a prompt pointing at the mirror would send Codex back at
the copy it is already reading instead of the real repository.

`scripts/sync_skills.py` mirrors every canonical regular file byte-for-byte into
`.agents/skills/<name>/`. Writer mode is part of `make docs`; read-only `--check` is the
`skills-check` prerequisite of `make docs-check`, so the existing `make ci` dependency rejects a
missing, extra, changed, or invalid mirror. Symlinks and nested source/destination roots are rejected.
The generated tree is never edited directly.

The three old `.claude/commands/` workflows become the normally named `pre-pr`, `docs`, and
`deadcode` skills; the generated `source-command-*` compatibility copies are removed. The canonical
set also contains `adr`, `azure-experiment`, `benchmark-vllm`, `design-review`, `drift-check`,
`k8s-deploy`, `maintain`, `quality-gates`, and `split-module`.

## External skills review (2026-09-13)

The review covered the requested Terraform, Kubernetes, and Azure subjects:

| Catalog | Relevant result | Decision |
|---|---|---|
| [OpenAI skills catalog](https://github.com/openai/skills) and its [successor plugins catalog](https://github.com/openai/plugins) | No dedicated general Terraform, Kubernetes, or Azure project skill. Matches are provider-specific reference material, such as Cloudflare Terraform and NVIDIA infrastructure components. | Do not install; these do not replace the FraudLens operating protocol. |
| [Anthropic official marketplace](https://github.com/anthropics/claude-plugins-official/blob/main/.claude-plugin/marketplace.json) | `terraform` exposes HashiCorp's broad Terraform MCP capability; `azure` installs Microsoft's broad Azure skills/MCP integration; no standalone `kubernetes` entry was present. | Do not install without a separate human approval. They add live, generic capability but duplicate the planned workflows and broaden the mutation surface. Reconsider for a bounded live troubleshooting need. |
| [Anthropic example skills](https://github.com/anthropics/skills) | No Terraform, Kubernetes, or Azure skill is listed in the example marketplace. | No installation candidate. |

No external skill or plugin is installed by this decision. If one is proposed later, review its exact
revision, permissions, network/MCP surface, mutation behavior, maintenance ownership, and overlap
with FraudLens skills, then obtain explicit human approval before installation.

## Why — generated duplication makes drift testable

**1 · One editable tree removes policy forks.** Claude Code and Codex read the same bytes, so a
permission or evidence change has one reviewable source.

**2 · Exact generation is simpler than semantic equivalence.** Byte comparison catches content,
metadata, extra-file, and deletion drift without a second parser or subjective compatibility rule.

**3 · Validation fails before generation.** Folder identity, frontmatter bounds, body size, metadata,
and canonical plan links are rejected at the source rather than copied into both tools.

**4 · Project workflows remain narrower than generic cloud tools.** The authored skills encode this
repo's cost allocations, Infisical posture, Azure-only deployment boundary, synthetic-only evidence,
kind/AKS distinction, and human approvals. External catalogs remain optional capability sources, not
governance authorities.

## Options considered and rejected

1. **Maintain separate Claude and Codex skill trees** — rejected because every policy change would
   require a coordinated manual edit and could pass review with one platform stale.
2. **Use symlinks from `.agents/skills/` to `.claude/skills/`** — rejected because tool support and
   checkout behavior vary, symlinks obscure generated ownership, and they can escape managed roots.
3. **Keep `.claude/commands/` plus generated compatibility skills** — rejected because two names and
   two discovery mechanisms represent the same workflow and encourage divergent maintenance.
4. **Install broad Terraform/Azure plugins now** — rejected because Phase 1 needs procedural safety,
   not more mutation capability. Their useful live lookup/diagnostic surface does not justify the
   overlap or permission expansion without a concrete task and explicit approval.
5. **Embed synchronization in the documentation engine** — rejected in favor of a focused CLI that
   can be tested and invoked independently while still composing through `make docs`/`docs-check`.

## Tradeoffs accepted

- The repository stores generated duplicate bytes, increasing file count and diff size. In return,
  both agents work from ordinary checked-in directories and CI can prove exact equality.
- A writer removes files found only in the managed mirror. This is intentional because the mirror is
  generated; root separation and symlink rejection bound the deletion scope.
- The metadata contract is stricter than the underlying agent formats. Adding optional ecosystem
  fields may require extending the Pydantic models, but required discovery behavior remains explicit.
- Generic external skills may know more current provider syntax. FraudLens can still use official
  documentation; installing an operational plugin remains a separate, reviewable decision.

## Reconsider when

- Claude Code and Codex adopt one common canonical repository path with verified native support.
- One platform requires transformed rather than byte-identical metadata.
- A concrete Azure, Terraform, or Kubernetes task needs capability not covered by official docs and
  the project skills; evaluate a pinned external plugin under a separate permission review.
- Skill count or asset size makes checked-in mirroring materially burdensome; preserve a single
  editable source and an equally strict freshness gate under any replacement.

**Never** hand-edit `.agents/skills/`, restore `.claude/commands/`, or let a generic external skill
override FraudLens governance, tenant isolation, PHI boundaries, spend approval, or teardown rules.
