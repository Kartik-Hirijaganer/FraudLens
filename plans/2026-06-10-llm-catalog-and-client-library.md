# Plan: LLM Catalog, Client Library, and Security Guardrails (`fraudlens-llm`)

## Context

FraudLens — and the additional backend services planned later — need to call LLMs,
but model choice, versioning, tuning, and **model selection** must stay
**configurable, not coded**. Because this is a healthcare-adjacent AML system under
the **FraudLens** governance rules (no PHI in logs/errors, tenant isolation, least
privilege), **security guardrails and compliance-aware routing are part of v1**.

**Goal.** A pip-installable, **catalog-driven, standalone** async library so that:
- *Add a model* → one entry under its provider in `config/llm/catalog.yml`. *No code.*
- *Bump a version* → add/point to the new model id. *No code.*
- *Tune a model* → change its `default_params`. *No code.*
- *Add an OpenAI-compatible provider* → one `config/llm/providers.yml` entry. *No code.*
- *Pick a model by requirement* (kind/modality/intelligence/speed/price) → a
  **selection API** over catalog metadata. *No code.*
- **Nothing hardcoded**; **secrets only in Infisical**; **no public call path reaches a
  provider without first running the input guardrail pipeline** (PHI masking, etc.),
  fail-closed; **fallback never weakens data-governance posture.**

**Two config files (separation of concerns).**
- **`config/llm/catalog.yml`** — a **capability + trust registry**, keyed
  `provider → model-id → card` (`kind`, `context_window`, `modality`, `default_params`,
  pricing, `speed`, `reasoning_capable`, `intelligence`, plus **trust fields**:
  `source_url`, `verified_at`, `lifecycle`, `callable`, `pricing_basis`). Metadata for
  **discovery + selection**; no secrets, no endpoints.
- **`config/llm/providers.yml`** — a **non-secret connection + governance registry**,
  keyed by the same provider names (`protocol`, `base_url`, `api_key_env`, `timeout_s`,
  `max_retries`, `headers`, plus **governance fields**: `region`, `data_retention`,
  `zdr_supported`, `training_opt_out`, `baa_required`, `allowed_data_classes`). API
  keys are env-var **references** resolved at runtime from Infisical `/llm`; **no key
  values, no hardcoded base URLs in code.**

A catalog provider with **no** `providers.yml` entry is *discoverable but not callable*
(raises a clear error) — how "reference-only" providers like Ollama are represented.

**Transport.** Official-SDK adapters: `openai` SDK (`openai_compatible` protocol —
OpenAI, OpenRouter, Gemini via Google's OpenAI-compatible endpoint, any compatible
`base_url`) and `anthropic` SDK (`anthropic` protocol — Claude). Two adapters cover
every wired provider; SDK-native retries/timeouts; fully owned request/response path.

**Scope (v1).** Async **chat** (`generate`) **and embeddings** (`embed`).
**Embeddings require an `openai_compatible` provider** (OpenAI/Gemini/OpenRouter-routed);
**Anthropic has no native embeddings → `embed()` on an `anthropic` provider is
unsupported** (Voyage would be a future provider/protocol). No streaming, tool-calling,
audio/transcription execution, UI, or public API route — audio models are *registered*
(`callable: false`) but not invoked in v1. Guardrails are **deterministic, local**;
external moderation is future.

**Wired providers (v1):** `openrouter`, `openai`, `anthropic`, `gemini` (direct). Other
vendors (xAI, DeepSeek, Qwen, …) are reachable as `openrouter/...` catalog entries with
no extra wiring.

## Current state (on disk, untracked)

- `packages/fraudlens-llm/pyproject.toml` — standalone skeleton (deps `openai>=1.40`,
  `anthropic>=0.39`, `pydantic`, `pydantic-settings`, `pyyaml`; ruff `banned-api` for
  all three internal packages). Matches plan.
- `config/llm/catalog.yml` — capability-registry shape exists, but its entries are
  **illustrative** and **lack the new trust fields** (`source_url`/`verified_at`/
  `lifecycle`/`callable`/`pricing_basis`). Must be verified + annotated in Phase 1.
- `config/llm/providers.yml` — connection registry exists (openrouter/openai/anthropic/
  gemini) but **lacks the governance fields**; to be added in Phase 1.
- `scripts/check_no_secrets.py` passes on both files today (reviewer-confirmed).

**Remaining:** trust + governance fields on the two config files;
`scripts/check_llm_catalog.py`; `src/fraudlens_llm/` modules + `py.typed`; root
`pyproject.toml`/`Makefile` gate wiring; tests; docs/tooling.

## Key decisions

| Decision | Rationale |
|---|---|
| **Catalog = capability+trust registry; connection+governance in `providers.yml`** | Keeps metadata pure; selection/fallback/cost depend on *verified* data and governance posture. |
| **Reference = `provider/model-id`** (split on first `/`) | `openai/gpt-5-mini`, `openrouter/anthropic/claude-sonnet-4.6`; the id (may contain `/`) is what the adapter sends. |
| **No public provider call without input guardrails** (phase ordering) | Enforces "PHI-masked before leaving FraudLens" *structurally* — adapters are private until the guardrailed client wraps them. |
| **Adapters validate params per capability; reject (not silently drop) unsupported** | Predictable cross-provider behavior; no accidental param loss across OpenAI/Gemini/Anthropic. |
| **`task_type` policy (not a boolean bypass)** | `analysis`/`extraction`/`generation` change *input* handling but never disable unsafe-generation blocking. |
| **Raw output locked down** | `allow_raw_output=false` default; prod forbids; scans run on raw *before* sanitization; prod returns only `safe_text`. |
| **Compliance-aware routing** | OpenRouter/fallback can move sensitive text across data-retention/jurisdiction postures; fallback must be equal-or-stricter. |
| **Standalone package**, **safe-logging allowlist**, **mocked-SDK no-network tests** | (Kept from prior revision.) |

---

## Catalog schema contract (`catalog.py`)

Keyed `provider → model-id → ModelCard`. Pydantic v2, `Field(..., description=...)`.

- **Enums** — `Kind` ∈ `chat | embed` (audio gets `transcribe` in a future plan);
  `Modality` ∈ `text | vision | audio`; `Speed` ∈ `very_fast|fast|medium|slow`;
  `Intelligence` ∈ `low|medium|high|highest`; `Lifecycle` ∈
  `ga | preview | deprecated | retired | reference`.
- **`ModelCard`** — `ConfigDict(extra="allow", frozen=True)` (tolerates new *descriptive*
  fields; known fields typed/bounded): `kind`; `context_window: int(ge 0)`;
  `max_token_output: int|None(ge 0)`; `modality: list[Modality]=[text]`;
  `knowledge_cutoff: date|None`; `default_params: GenerationParams`; pricing
  (`input_price_per_million`/`output_price_per_million`/`input_price_per_minute`,
  `ge 0`); `speed|None`; `reasoning_capable: bool`; `intelligence|None`;
  audio extras (`max_audio_duration`, `supported_languages`, `features`).
  **Trust fields:** `source_url: str|None`, `verified_at: date|None`,
  `lifecycle: Lifecycle = reference`, `callable: bool = false`,
  `pricing_basis: Literal["per_million_tokens","per_minute"]|None`.
- **`GenerationParams`** (only generation surface; `extra="forbid"`, bounded allowlist):
  `temperature(0–2)`, `max_tokens(ge 1)`, `top_p(0–1)`, `stop(bounded list)`,
  `dimensions(ge 1)`, `response_format(str)`, `language(str|None)`, `seed`,
  `frequency_penalty(−2..2)`, `presence_penalty(−2..2)`, `reasoning_effort(str)`. No
  `api_key`/`base_url`/`headers`/`tools`/`functions`/`tool_choice`.
- **`Catalog`** — `get(ref) -> (provider, model_id, ModelCard)` (split on first `/`;
  `ModelNotFoundError` if absent). `select(*, kind=None, modality=None,
  min_intelligence=None, max_input_price=None, reasoning_capable=None, speed=None,
  provider=None, include_unverified=False) -> list[str]`. **Default selection returns
  only `callable: true` AND `lifecycle ∈ {ga,preview}` AND `verified_at` set**;
  unverified/reference/deprecated/retired and audio-only models are excluded unless
  `include_unverified=True`. Ranked intelligence-desc then input-price-asc.
- **`load_catalog(path)`** wraps `yaml.YAMLError`/`ValidationError` as `CatalogError`.

**Catalog freshness gate — `scripts/check_llm_catalog.py`:** validates the file against
the schema; flags `callable: true` models missing `verified_at`/`source_url`/
`pricing_basis`; warns on `verified_at` older than a threshold. An **optional**
`--live` mode (network, off by default) checks model ids against each provider's
model-list endpoint. Wired as a `make llm-catalog-check` target included in `make ci`.

## Providers schema contract (`providers.py`)

Keyed `provider-name → ProviderConfig`. `frozen=True, extra="forbid"`.

- **`Protocol`** ∈ `openai_compatible | anthropic`.
- **Connection:** `protocol`; `base_url: str|None` — **required + `https://`** for
  `openai_compatible`, optional for `anthropic`; `api_key_env: str` matches
  `^[A-Z][A-Z0-9_]*$` (**an env-var reference, never a secret value**);
  `timeout_s: float(gt 0, le 600)`; `max_retries: int(ge 0, le 10)`;
  `headers: dict[str,str]={}` with **name denylist** (`authorization`, `api-key`,
  `x-api-key`, `proxy-authorization`, `cookie`, `set-cookie`, or anything matching the
  repo secret regex) and **values rejected if secret-like**.
- **Governance:** `region: str` (e.g. `us`/`eu`/`global`); `data_retention: str` (e.g.
  `none`/`30d`/`provider-default`); `zdr_supported: bool`; `training_opt_out: bool`;
  `baa_required: bool`; `allowed_data_classes: list[DataClass]`.
- **`load_providers(path)`** wraps errors as `CatalogError`.

A test asserts both files pass `check_no_secrets`, and a bare `api_key:` with a
real-looking value **is** flagged.

---

## Compliance-aware routing (governance)

- **`DataClass`** ∈ `synthetic | deidentified | internal | restricted` (ordered by
  sensitivity). `LlmSettings.default_data_class = synthetic` (this repo uses no real
  PHI); per-call `data_class` override.
- **Enforcement (pre-call):** the resolved provider's `allowed_data_classes` must
  include the call's `data_class`, else `PolicyError` (no provider call, no fallback).
- **Fallback policy:** a fallback target is eligible only if it (a) allows the call's
  `data_class` and (b) has an **equal-or-stricter posture** (`zdr_supported`,
  `training_opt_out` at least as protective; not a weaker `region`/`data_retention`).
  An explicit `allow_policy_downgrade=True` override is honored **only outside prod**
  (prod guard forbids it).
- **Logging:** the policy decision (provider, data_class, allowed?) is in the safe-log
  allowlist — never the content. The library **enforces** org-maintained policy
  metadata; it does not certify compliance. Vector storage and `agency_id` scoping of
  embeddings remain a **backend-layer** responsibility (documented).

---

## Security model (precise, testable)

### `LlmSettings` knobs + prod fail-closed guard

- `guardrail_strictness: Strictness` ∈ `block|flag|disabled` (default `block`).
- `phi_masking_mode: PhiMaskingMode` ∈ `enforce|off` (default `enforce`).
- `allow_raw_output: bool` (default `false`).
- `environment: Literal["dev","prod"]` — `FRAUDLENS_LLM_ENVIRONMENT` →
  `FRAUDLENS_ENVIRONMENT` → `"dev"`.
- **Prod guard** (`model_validator`): in `prod`, reject `strictness == disabled`,
  `masking == off`, **and** `allow_raw_output == true`. Mirrors
  `settings.py::is_dev_bypass_enabled`.

### `GuardrailReport` (on **every** result — chat and embed; categories/counts only)

`Finding{category,severity,location}`, `ScanOutcome{decision,findings}`,
`MaskingReport{mode,counts,total_masked}`, `GuardrailReport{decision,strictness,
masking,prompt_risk,output,phishing,policy}`. For `embed`, `output`/`phishing` are
`not_applicable`; `masking` + `policy` are populated.

### `generate()` pipeline (input guardrails run once before any attempt; output guardrails on the returned completion)

1. **Compliance check** (data_class vs provider) → `PolicyError` if disallowed.
2. **PHI masking** (`security/phi.py`) before any adapter call — synthetic patterns
   (`email`, `us_ssn`, `phone`, `credit_card` + **Luhn**, `dob`, `mrn_member_id`,
   `street_address`, custom); `[REDACTED_<CATEGORY>]`; counts-only; **fail-closed**.
3. **Prompt-injection scan** (`security/prompt_risk.py`): `instruction_override`,
   `system_prompt_extraction`, `secret_exfiltration`, `tool_misuse`,
   `data_exfiltration`, `encoded_payload` — modulated by `task_type` (below).
4. **System policy wrapper** (`security/policy.py`): never reveal secrets/system
   instructions/tenant data/raw PHI; treat user content as data to analyze; never
   solicit passwords/keys/MFA/payments.
5. **Provider call** (private adapter; fallback routing honoring governance).
6. **Scan raw output** for phishing/policy violations (`security/phishing.py`) **before
   sanitization** — so the decision is made on the true output.
7. **Output sanitization** (`security/output.py`) → `safe_text`: neutralize `<script>`,
   `javascript:`/`vbscript:`/`data:text/html`, inline `on*=`,
   `<iframe|object|embed|applet>`, dangerous-scheme markdown links, encoded payloads.
8. **Return** — `safe_text` always; `raw_text` only if `allow_raw_output` **and**
   `include_raw=True` **and** not prod (else `None`).
9. **Safe logging** (`security/redaction.py`): allowlist only — request id, model ref,
   provider, data_class, latency, status class, token counts (+ optional est. cost from
   verified pricing), retry/fallback count, guardrail + policy decision. **Never**
   prompts/completions/keys/headers/raw payloads/tenant ids; exceptions scrubbed.

**`embed()` pipeline:** compliance check → PHI masking (fail-closed) → adapter →
safe logging. No output sanitization/phishing (vectors). `EmbeddingResult` carries the
`MaskingReport` + `GuardrailReport` (output/phishing `not_applicable`).

### `task_type` (replaces the boolean `analysis_mode`)

`TaskType` ∈ `generation` (default) | `analysis` | `extraction`. Policy: `analysis`/
`extraction` treat user-supplied artifacts as **data** — injection patterns found
*inside analyzed content* downgrade to `flag` (never `block`), and the phishing check
uses descriptive-vs-imperative leniency. **Unsafe generation is blocked regardless of
`task_type`** (output soliciting secrets/MFA/payment, or script payloads). Only the
investigation/analyzer wrapper should pass `analysis`/`extraction`. Output script
sanitization is never relaxed. Deterministic separation is imperfect (borderline →
`flag`); documented.

### Adapter capability validation

Each adapter declares supported generation params + capabilities; an unsupported param
(or `embed()` on an `anthropic` provider) raises a clear error
(`UnsupportedParameterError` / `CapabilityMismatchError`) instead of silently dropping.

---

## Public API surface (typing-clean, safe by default)

- **`py.typed`** shipped; `mypy --strict` clean.
- **`__init__.py` `__all__`**: `LlmClient`, `BoundModel`, `load_catalog`,
  `load_providers`, `Catalog`, `Providers`, `ModelCard`, `ProviderConfig`, `Protocol`,
  `Kind`, `Modality`, `Speed`, `Intelligence`, `Lifecycle`, `DataClass`,
  `GenerationParams`, `LlmSettings`, `get_llm_settings`, `Role`, `LlmMessage`,
  `GenerationOverrides`, `TaskType`, `LlmUsage`, `LlmResult`, `EmbeddingResult`,
  `GuardrailReport`, `Finding`, `ScanOutcome`, `MaskingReport`, `Strictness`,
  `PhiMaskingMode`, `GuardrailDecision`, + exceptions.
- **`LlmClient`** — `from_config(catalog, providers, settings=None)` /
  `from_settings(settings=None)`.
  - `async generate(messages, *, model=None, overrides=None, task_type=TaskType.GENERATION,
    data_class=None, include_raw=False, fallbacks=None) -> LlmResult` (model =
    `provider/model-id`; `None` → `settings.default_model`; requires a `callable`
    `kind=chat` model).
  - `async embed(inputs, *, model, overrides=None, data_class=None) -> EmbeddingResult`
    (requires a `callable` `kind=embed` model on an `openai_compatible` provider).
  - `get_model(ref) -> BoundModel` (factory convenience).
- **`GenerationOverrides`** = `GenerationParams`; precedence **overrides > card
  `default_params` > `LlmSettings` defaults**.
- **`LlmResult`** (`frozen, extra="forbid"`): `safe_text`, `model`, `provider`,
  `served_model`, `finish_reason`, `usage`, `guardrail: GuardrailReport`,
  `raw_text: str|None = Field(default=None, exclude=True, repr=False)`.
  **`EmbeddingResult`**: `embeddings`, `model`, `provider`, `usage`,
  `guardrail: GuardrailReport`.

### Fallback semantics (enumerated)

SDK `max_retries` (same provider) → per-call `fallbacks=[ref,...]` (next ref) after
retries exhausted, **subject to the governance equal-or-stricter rule above**.
Retryable → next: `LlmTimeoutError`, `LlmRateLimitError`, transient `ProviderError`
(`408/409/429/500/502/503/504`), connection errors. **Never fallback:** `GuardrailError`,
`PolicyError`, `MissingApiKeyError`, `ProviderAuthError`, `CatalogError`,
`ModelNotFoundError`, `ProviderNotConfiguredError`, `CapabilityMismatchError`,
`UnsupportedParameterError`, `ValidationError`, non-transient `4xx`.

---

## Phases (guardrails precede any public provider call)

### Phase 1 — Catalog, providers, config, settings, scaffold + gate wiring
- [ ] Add trust fields to `catalog.yml` (verify ids/pricing/cutoffs against provider
      sources; set `callable`/`lifecycle`/`verified_at`/`source_url`/`pricing_basis`;
      audio-only → `callable:false`); add governance fields to `providers.yml`.
- [ ] `catalog.py` (+ `select` default-callable+verified filter), `providers.py`
      (governance), `settings.py` (prod guard incl. `allow_raw_output`, `default_model`,
      `default_data_class`, discovery), `__init__` + `py.typed`.
- [ ] `scripts/check_llm_catalog.py` + `make llm-catalog-check` (included in `ci`).
- [ ] Root `pyproject.toml` (ruff.src, mypy.mypy_path, isort.known-first-party,
      coverage.source) + `Makefile` `PY_SRC` wiring; `uv sync`/`uv lock`.
- [ ] `config/README.md` documents both files + keys. Green: import, ruff, mypy.
      drift-check phase=1.

### Phase 2 — Types, exceptions, **private** adapters (transport only)
- [ ] `models.py`, `exceptions.py` (incl. `PolicyError`, `UnsupportedParameterError`,
      `ProviderNotConfiguredError`, `CapabilityMismatchError`).
- [ ] `adapters/` `ProviderAdapter` protocol + `OpenAiCompatibleAdapter` +
      `AnthropicAdapter`: lazy key read; SDK→lib mapping w/ `retryable`; **capability
      validation (reject unsupported params; anthropic embed unsupported)**;
      normalization. **No public guardrail-free `generate()` is exported here.**
      drift-check phase=2.

### Phase 3 — Security guardrails
- [ ] `Strictness`/`PhiMaskingMode`/`GuardrailDecision`/`TaskType`/`DataClass` enums;
      `security/` (`phi`, `prompt_risk`, `policy`, `output`, `phishing`, `redaction`).
      Unit-tested in isolation. drift-check phase=3.

### Phase 4 — Public client (first point a prompt can leave; guardrailed by construction)
- [ ] `client.py`: `LlmClient.from_config/from_settings`, `generate`/`embed`/
      `get_model`, `BoundModel`; wires **compliance → input guardrails → adapter →
      output guardrails → safe logging**; fallback routing w/ governance; precedence.
- [ ] **Invariant test:** the mocked SDK never receives unmasked PHI, and no adapter is
      reachable without the input pipeline. `__init__` exports `__all__`. drift-check
      phase=4.

### Phase 5 — Docs, tooling & final gate
- [ ] `scripts/lib/docs_arch.py::render_module_map` adds the `fraudlens-llm` (standalone)
      node + edges (`backend`/`ml` `-.may use.-> llm`).
- [ ] Architecture doc (catalog vs providers, selection, routing, governance, guardrails)
      + `docs/runbooks/infisical-secrets.md` (`/llm` keys: `OPENROUTER_API_KEY`,
      `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`; run pattern).
- [ ] `make docs` clean; `make docs-check` passes. drift-check all.

## Test plan (central `tests/`, ≥90% branch; SDKs mocked at the boundary, no network)

Mock `AsyncOpenAI.chat.completions.create` / `.embeddings.create` /
`AsyncAnthropic.messages.create`; fakes raise real SDK exceptions. Dummy keys via
`monkeypatch.setenv`.

- **Catalog/trust** — load; `get` split incl. routed slug; `select` filters/ranks and
  **excludes non-callable/unverified/audio by default**, includes with
  `include_unverified`; bad enum; out-of-bounds params; `extra="allow"` keeps unknown
  descriptive fields. **`check_llm_catalog`**: flags `callable:true` missing
  `verified_at`/`source_url`/`pricing_basis`.
- **Providers/governance** — base_url required+https (openai_compatible)/optional
  (anthropic); header denylist; `api_key_env` pattern; governance fields parse;
  unknown protocol.
- **Secret guard** — `check_no_secrets._scan_yaml` on both files == `[]`; bare `api_key:`
  flagged.
- **Settings** — `FRAUDLENS_LLM_*` overrides; no backend-prefix leak; `environment`
  resolution; **prod rejects `disabled`/`off`/`allow_raw_output=true`**; discovery;
  defaults; cached.
- **Adapters** — chat + embed happy paths; usage/finish mapping; missing key→
  `MissingApiKeyError`; full SDK→lib ladder incl. unknown→`LlmError`; `retryable`;
  **unsupported param→`UnsupportedParameterError`**; **anthropic `embed`→
  `CapabilityMismatchError`**.
- **Routing/fallback/governance** — retryable→next; exhausted→raise; non-retryable→
  immediate; precedence overrides>card>defaults; unconfigured provider
  (`ollama/...`)→`ProviderNotConfiguredError`; capability mismatch; **data_class
  disallowed→`PolicyError`**; **fallback skips weaker-posture providers**; downgrade
  override honored only non-prod.
- **PHI masking** — each category masked **before** adapter sees content (mocked-SDK
  receives no raw PHI — the invariant); Luhn gate; counts-only; `off` non-prod;
  failure→fail-closed; applies to `embed` inputs; `EmbeddingResult` carries the report.
- **task_type / injection / output / phishing** — per-strictness block/flag;
  `analysis`/`extraction` downgrade artifact-internal hits but **still block unsafe
  generation**; `<script>`/`javascript:`/`on*=`/iframe/encoded neutralized; phishing
  **generation** blocked but **descriptive analysis passes**.
- **Raw-output lockdown** — `include_raw` ignored unless `allow_raw_output` (non-prod);
  prod forbids; `raw_text` absent from `model_dump`/`json`/`repr`/logs.
- **Packaging/typing** — `import fraudlens_llm` exposes every `__all__`; `py.typed`.
- **Integration** — `generate()` and `embed()` end-to-end (SDK-mocked), full pipeline,
  no network (not `smoke`-marked).

## Verification (full repo SSOT)

1. `uv sync --all-packages` → import smoke.
2. Dev loop: `uv run pytest -q tests/unit/test_llm_*.py tests/unit/test_*adapter*.py tests/integration/test_llm_integration.py --cov=fraudlens_llm --cov-report=term-missing` ≥90% branch.
3. `uv run python scripts/check_llm_catalog.py` (offline schema+trust gate).
4. **Final acceptance:** `make docs` then **`make ci`** (Makefile = SSOT; both stacks;
   includes `llm-catalog-check`).
5. Optional live check (real keys): `infisical run --env=prod --path=/llm -- uv run python -c "import asyncio, fraudlens_llm as f; c=f.LlmClient.from_settings(); print(asyncio.run(c.generate(messages=[f.LlmMessage(role='user', content='ping')])).safe_text)"`. Then prove **add-a-model / bump-a-version / pick-by-requirement = catalog edit only**.
6. `drift-check plans/2026-06-10-llm-catalog-and-client-library.md all`. **No
   commit/push without explicit permission** (Golden Rule 1).

## Risks / caveats

- **Catalog trust:** ids/pricing/cutoffs must be verified (`check_llm_catalog`,
  `verified_at`/`source_url`); unverified entries are non-selectable by default so
  stale data can't silently drive routing/cost.
- **Governance metadata is org-maintained**; the lib enforces, it does not certify
  compliance. Real PHI still requires BAA/data-handling diligence; embeddings'
  vector storage + `agency_id` scoping live in the backend.
- **Masking ↔ utility tension** (configurable + fail-closed; regex masking is
  defense-in-depth, not a guarantee).
- **Deterministic guardrails / `task_type`** reduce, not eliminate, risk
  (borderline → `flag`).
- **SDK pinning** (`openai>=1.40`/`anthropic>=0.39`): confirm exception classes + async
  signatures (incl. `.embeddings.create`) against locked versions.

## Keep (validated strengths — do not regress)

Two-file catalog/provider split; first-slash `provider/model-id` rule; standalone
package with internal-import bans; safe-logging allowlist; explicit fallback
semantics; no-network SDK-mocked tests.

## Out of scope (v1) / future

Streaming, tool-calling, audio/transcription (`kind: transcribe`), a Voyage
provider/protocol for Anthropic-ecosystem embeddings, UI/public route,
model-based/external moderation, external wheel publishing.
