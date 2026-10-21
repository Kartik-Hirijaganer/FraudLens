# Upload validation and local SAR drafting — verification

Original provider-free verification completed locally on 2026-10-21. The implementation follows
[ADR-032](../architecture/adr/ADR-032-upload-validation-and-local-sar-templates.md).
That verification made no billable provider request. The separately authorized live acceptance test
below ran on 2026-10-22. Neither session committed, pushed, deployed or mutated cloud resources.

## GPT-5 mini parameter correction and strict live retest — 2026-10-22

The earlier strict-routing rejection exposed a catalog/default-merging mismatch. Both GPT-5 mini
entries now declare `temperature: null`. Parameter merging preserves explicitly supplied nulls, so
they clear inherited values; omitted fields still inherit, request overrides retain precedence, and
provider serialization omits nulls. The existing `minimal` reasoning and 3,000-token SAR ceiling are
unchanged. OpenAI documents the restriction in its
[GPT-5 parameter guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2).

| Check | Result |
|---|---|
| Offline regression | Nine passing cases in [generation-parameter tests](../../tests/integration/test_llm_generation_params.py): inheritance, explicit null at model/request layers, request precedence, both GPT-5 mini catalog routes through real SDK serialization over `httpx.MockTransport`, and fallback in both directions. No socket or provider credential is used. |
| Backend and repository gates | `make backend-ci llm-catalog-check quality-gates file-length-check header-check docs-check docs-links-check no-hardcoding-check secrets-scan` passed. Backend: 2,367 passed, 17 skipped, 25 deselected, 96.42% branch-aware coverage. Separate offline quality/egress suite: 33 passed. Changed-line coverage from the completed suite: 98% overall, 100% in both modified LLM modules. |
| Fresh browser test | Migrated and seeded local database `fraudlens_gpt5_params_20261022`. Downloaded the bundled CSV in the browser and uploaded it unchanged. All three rows were accepted; one High case automatically generated a passing live SAR at **version 1**. No regeneration or retry occurred. Previous local databases were retained. |
| Strict provider boundary | The loopback guard required OpenRouter `require_parameters: true`, refused any incoming `temperature` field, and recorded the serialized parameter names without rewriting them. Exactly one request reached the provider: HTTP 200, `stop`, zero blocked requests, zero SDK retries and no model/provider fallback. |
| Spend cap and actual charge | One-attempt admission reserved a conservative maximum of **$0.00870375** below the **$0.05** cap. OpenRouter generation metadata reconciled **$0.0007236**, 1,712 input tokens and 335 completion tokens, served by OpenAI as `openai/gpt-5-mini-2025-08-07`. The app's uncached catalog estimate was $0.001098; it is distinct from the provider-reported charge. |
| Draft checks and review | `sar-gate-v2` passed with three valid citations and no reported fabricated citations, unsupported claims, mismatched facts, unmapped spans or missing required elements. Analyst approval was disabled. The reviewer approved version 1 through the browser; reload retained approval and four completed machine steps. |
| Local-only control and cleanup | An altered synthetic CSV produced a quality-passed local template at zero tokens/cost, with zero provider requests before the trusted upload. Retrieval and embeddings stayed offline. The credential-bearing guard was stopped and the app restarted in mock mode; readiness shows the LLM provider skipped. No browser console errors were observed. |

Retest run `de3e2a82-e78f-4339-9751-b37dee7802ae`, approved draft
`4f61dc3c-f9e8-45e1-aed6-1a329c615455`, approval request
`bc427c9c30134262911fd8b7db23f712`. Non-secret usage/routing metadata, database assertions, logs and
the approval screenshot are under `.local/verification/gpt5-config-check/` (gitignored). Raw prompts,
responses and provider keys are absent from the evidence files. No commit, push, deployment, cloud
resource mutation or regulatory filing was performed.

## Live trusted-CSV acceptance — 2026-10-22

Tested the existing app through the browser at `http://127.0.0.1:15173`, using an isolated database
`fraudlens_csv_live_20261022` in the existing local PostgreSQL container at `127.0.0.1:55439`.
Migrated the database through 0015 and seeded only identities, rules, configuration and the active
`v0-fixture` model pointer. The original local database and all 22 of its transactions were retained.
Storage, jobs, regulatory retrieval and embeddings remained local; no Supabase or deployed app was
used. The sole external inference used the existing OpenRouter credential injected at runtime from
Infisical `prod` `/llm`.

| Boundary | Observed evidence |
|---|---|
| Unverified control | Uploaded a synthetic copy with changed external ids through the file picker. Three rows accepted, one High and two Low. The High case saved a quality-passed `template` draft with zero tokens, zero cost and zero provider attempts. The forwarding guard still recorded zero requests. |
| Trusted upload | Downloaded the backend-owned demo CSV in the browser and uploaded it unchanged. Three rows accepted; the real XGBoost/SHAP pipeline produced one High and two Low results. The High case used the existing single-writer live drafter and locally retrieved regulations. |
| Spend admission | A loopback-only verification guard held the runtime key; the application received only a local placeholder. Text-only input, serialized-byte bounds, a 3,000 completion-token ceiling and OpenRouter price limits kept the total authorized spend below **$0.05**. SDK retries and model/provider fallbacks were disabled. Offline guard tests checked invalid routes, token/byte limits and concurrent duplicate refusal before live use. |
| Routing recovery | The first strict test route returned HTTP 404 before generation because the catalog temperature setting is unsupported by GPT-5 mini under `require_parameters`. Removed that test-only strictness requirement, retaining model, price and token limits. Used the browser's **Regenerate** action on the same saved investigation. The failed version remains in history; only one request generated tokens. The combined conservative reservations for both routing attempts were $0.017417. |
| Real provider result | OpenRouter generation metadata independently reconciled **$0.001076**, 1,712 input tokens, 324 completion tokens, `stop`, upstream **OpenAI**, served model `openai/gpt-5-mini-2025-08-07`, no BYOK. These match the streamed usage and persisted draft cost. |
| Draft checks | Version 2 passed `sar-gate-v2`: three valid citations, precision/recall 1.0, no fabricated ids, unresolved evidence references, unsupported claims, mismatched facts, missing FinCEN elements or unmapped narrative spans. The narrative uses case aliases; uploaded account and external-id literals are absent. These are the existing deterministic checks, not a claim of exhaustive legal or semantic validation. |
| Review | The analyst's **Approve SAR** control was disabled. Signed in as the reviewer, confirmed risk, drivers, citations and narrative, and approved through the browser. The tenant-scoped `sar.approve` audit actor matches `reviewed_by`; version 2 is `approved` with quality `passed`. No regulatory filing was performed. |
| Persistence and teardown | Stopped the credential-bearing guard and restarted the API in mock mode without provider credentials. Browser reload retained the live narrative and showed **Human review approved**, four of four machine steps complete. Readiness reports the LLM provider skipped. No browser console errors were observed. |

Run `94fcb52e-ca4f-4958-aac8-333b4169a2c6`, approved draft
`8ce15853-1c67-4cc4-aebe-7c2ca24bee0f`, approval request
`6b96ebb30a4f4512a2cdbdd0b7a2ea34`. Raw prompts and provider responses were not written to logs or
reports. Non-secret provider metadata, database assertions, screenshots and the scoped test guard
are retained under `.local/verification/live-check/` (gitignored). The approved case remains viewable
in the local app. This test cap is a local acceptance harness; it does not establish a general
production single-writer spending guarantee or alter the normal provider configuration.

Provider controls follow OpenRouter's
[price and routing limits](https://openrouter.ai/docs/guides/routing/provider-selection#max-price);
the [model endpoint metadata](https://openrouter.ai/api/v1/models/openai/gpt-5-mini/endpoints) was
checked before the test. The $0.05 limit includes both routing attempts; actual inference spend was
about 0.11 US cents.

## Evidence

| Check | Result |
|---|---|
| `make ci` | Passed the repository umbrella, including SAR quality/egress, scripts, full-data, benchmark, GPU-operator and Kubernetes contract checks. |
| Final `make test-coverage-diff` | Passed: 2,350 backend tests, 17 skipped and 25 deselected; 451 frontend tests. Both diff checks include untracked files. |
| Backend coverage | 96.39% aggregate with branch coverage; CSV parser, shared upload validator and local template module each have 100% coverage. Changed-line gate: 98%. |
| Frontend coverage | 97.53% lines and 90.68% branches. Changed-line gate: 96%. |
| Final lint, formatting, type checks and generated documentation | Passed through the Makefile targets. OpenAPI documents raw CSV, rejection reasons and drafting mode. |
| Local PostgreSQL | Fresh migration to 0015 and all five lease/schema/upload-race tests passed. The disposable container was removed and its absence verified. |
| Browser follow-up frontend gate | `make frontend-ci frontend-coverage-diff` passed: 453 tests across 73 files, lint, formatting and type checks; 97.53% line coverage, 90.73% branch coverage and 97% changed-line coverage. Documentation freshness, headers, file length, links and whitespace checks also passed. |
| Trusted CSV scope update (2026-10-22) | Full `make ci` passed: 2,358 backend tests (96.42% branch-aware coverage), 458 frontend tests (97.55% lines, 90.77% branches), offline quality/egress gates, tooling and generated documentation. No paid inference was run. |
| Trusted CSV changed-line gate | `make test-coverage-diff` passed with untracked source included: 98% backend and 97% frontend changed-line coverage. |

The final full-suite rerun includes the `reasonCode` response and LF/CR/CRLF physical-line regression
cases added after the umbrella run. Production credentials and live data were not used. The browser
acceptance run below supplements the component/interaction tests.

## Local browser acceptance — 2026-10-21 (2026-10-22 UTC)

Applied `0015_account_identity_keys` to the existing local PostgreSQL container
`fraudlens-screening-local-pg` at `127.0.0.1:55439`. Verified both nullable 64-character account-key
columns and both tenant/account indexes. All 11 existing transactions remain; their missing account
keys were not backfilled. No cloud database was accessed.

Started the API at `http://127.0.0.1:18039` and Vite at `http://127.0.0.1:15173` with a clean child
environment: no inherited provider credentials, mock LLM mode, offline embeddings, local queue and
storage, and the existing non-production persona bypass. Built a separate offline index from the
committed corpus: 12 chunks across six documents. Readiness reported database and ChromaDB `ok`,
and the unconfigured LLM provider and Supabase Auth `skipped`.

| Browser action | Observed and persisted result |
|---|---|
| Sign in as analyst and import a three-row synthetic CSV through the file picker | Two accepted, zero duplicates, one rejected. Physical row 4 reports `not_a_number`; its supplied text is not reflected. |
| Automatic screening without an explicit investigation request | The $100,000 wire to NG scored High; the $50 US card payment scored Low using the existing active `v0-fixture` XGBoost model. No score or threshold override was applied. |
| Open saved High results and advance through risk, drivers, citations and SAR | Completed run, real SHAP drivers, three locally retrieved regulatory citations, and a quality-passed **Template-generated draft**. External AI restriction and identity/ownership/purpose unknowns are visible. |
| Reach approval as analyst | Approve SAR is disabled with the reviewer-permission explanation. |
| Sign out, select reviewer, open the flagged alert and approve | API returned 200. The draft persisted as `approved`, reviewed by the seeded reviewer; the tenant-scoped `sar.approve` audit entry carries the same actor and request id. Reload retains approval. |
| Inspect provider and privacy evidence | Model id `template`, zero input/output/total tokens, $0 cost and reservation, and no provider generation attempts. Both new uploads retain source `api-upload`, opaque external ids, `[WITHHELD]` account displays and full 64-character identity keys. Raw fixture identifiers and invalid amount text are absent from application logs. |

The browser run exposed a stale shared timeline: an approved SAR still displayed "Awaiting human
review." The timeline now uses the persisted SAR decision for both workflow modes. Browser reload
shows "Human review approved" and four of four steps complete; regression coverage exercises approval
and rejection. Approval is internal; the alert remains open and no filing or final fraud label was
submitted.

Evidence identifiers: run `096a8e0b-3c40-4e18-ba66-02ea282d5de8`, alert
`99d6d845-1237-4f56-851a-0daac9e38ea4`, draft `007163fa-a287-46c5-a20e-3bc0b6999fbf`, approval
request `190bfc1ded2049c79927f689a77aeab8`. Local screenshots, the synthetic fixture, asserted database
evidence, server logs and process metadata are retained under `.local/verification/` (gitignored).

## Behavioral contracts exercised

The 2026-10-22 scope update in
[ADR-033](../architecture/adr/ADR-033-bundled-synthetic-csv-provenance.md) adds exact bundled-file
provenance, not a general PII scrubber. The new
[synthetic CSV tests](../../tests/integration/test_synthetic_csv.py) run the production live drafter
and LLM client against a recording SDK double, including transport fallback, factual/citation checks,
failed quality, saved results, regeneration and authorized reviewer approval. They also prove that
edited bytes stay untrusted, missing fixtures fail closed, duplicates cannot upgrade provenance, and
unverified origin/counterparty history cannot enter model-eligible evidence. No actual provider call
is part of this evidence.

Browser follow-up downloaded and re-uploaded the backend-owned CSV unchanged. The active local
XGBoost model produced one High and two Low results; the High case saved a quality-passed `mock`
draft while providers were disabled. A changed synthetic CSV produced the local restriction and a
quality-passed `template` draft for its High case. Desktop and 400-pixel report layouts were checked;
screenshots are in `.local/verification/trusted-csv-{desktop,mobile}.jpg` and
`.local/verification/local-only-csv-desktop.jpg`. The canonical CSV revision uses distinct external
ids; earlier local verification records were retained rather than reset.

Cleanup removed the frontend-only template asset and its unused text-decoding test stub. Validation,
account-key linkage/migration 0015, local retrieval, templates, reviewer history and approval display
remain active. The advisory `make deadcode` sweep found no Python issues and eight existing frontend
exported types used by public interfaces or function signatures; their declarations and consumers
were inspected and retained.

- Strict UTF-8/BOM handling, bounded chunk reads, cell/column/row limits, quoting, duplicate/missing/
  unexpected headers, inconsistent widths and physical row numbers across multiline fields.
- Exact-cent amounts, finite/range checks, explicit timezones, future timestamps, supported currency/
  country codes, controlled channels and closed upload features. Internal dataset adapters retain
  their normalization contract.
- Partial acceptance, duplicate imports, rollback of failed queue admission, automatic screening,
  saved-result observation, explicit rerun permissions and idempotency, and tenant-scoped access.
- PII-shaped values and injection in every CSV field, identifier-shaped cards/SSNs/phones, arbitrary
  feature keys and caller provenance declarations. Reports, application logs and stored upload
  identifiers do not reflect those supplied strings.
- Equal-length accounts sharing last-four characters remain distinct; matching full identities link
  within a tenant, while display masks and missing historical keys cannot authorize account joins.
- Socket-denied provider capture proves zero outbound provider bytes for unverified uploads, including
  configured SDK retries and transport fallback. Direct agent/fallback and regulation-search tool
  tests prove those routes cannot bypass source authorization. Upload retrieval uses the local index
  without invoking its embedding route.
- A flagged upload completes scoring and SHAP, saves its alert, retrieves verified local regulations
  and produces a passing **Template-generated draft** with null provider, zero tokens and zero cost.
  Missing/tampered citations fail explicitly. Unknown identity, ownership and purpose are stated.
- Regeneration creates another draft version without another investigation. Analyst approval is
  denied; a reviewer can edit and approve. An edit resets automatic quality to `not_run`, preserves
  local generation mode and retains all earlier versions. Approval remains internal.

Core evidence lives in [CSV validation tests](../../tests/unit/test_csv_validation.py),
[upload privacy and reviewer tests](../../tests/integration/test_upload_privacy.py),
[local-drafter tests](../../tests/unit/test_upload_drafting.py),
[transport tests](../../tests/quality/test_model_egress.py) and
[PostgreSQL upload tests](../../tests/integration/test_upload_postgres.py).

## Operating limits

Apply migration 0015 only to the intended database before starting the updated API, and provide its
offline regulation index. The acceptance run migrated the existing local database only.
The [analyst walkthrough](../runbooks/transaction-screening.md) and
[CSV template](../../config/demo-transactions.csv) describe the supported input contract.

Ordinary uploads always remain `api-upload`; validation, masking and pseudonyms do not establish
anonymity or synthetic provenance. [ADR-033](../architecture/adr/ADR-033-bundled-synthetic-csv-provenance.md)
now admits byte-identical copies of the bundled synthetic fixture through the existing trusted
generator source; all other uploads remain local. No signing service or universal PII-removal claim
is implemented. CSV retrieval stays local. Real production data/provider handling requires a
separately approved policy. Historical display masks cannot be reliably backfilled into full account
identities; saved results are retained. The supported ISO subsets and empty uploaded-feature contract
are deliberate constraints, and missing regulatory evidence yields a drafting failure.
