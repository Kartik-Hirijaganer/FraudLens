# Real AML data, real RAG embeddings & honest end-to-end investigations

## Context

**Why this change.** The June 2026 handoff ([docs/handoff/AML_Fraud_System_Handoff.docx](../docs/handoff/AML_Fraud_System_Handoff.docx))
planned to train the XGBoost model on a real public dataset and demonstrate a live
XGBoost → SHAP → regulatory-RAG → LLM-SAR workflow. The shipped system has the engineering
plumbing, but the normal demo is driven by **fabricated seed data and a mock SAR drafter**:

- The model trains on a synthetic numpy generator
  ([scripts/lib/synthetic_fraud.py](../scripts/lib/synthetic_fraud.py), 16k rows), **not a real dataset**.
- Demo data = 12 hand-written transactions + ~29 directly-seeded alerts + ~16 hardcoded SARs
  ([scripts/seed.py](../scripts/seed.py)). At audit time the DB had **0 `analysis_results`, 0 RAG
  retrievals, 0 inference logs** — the populated dashboard is seed scaffolding, not evidence a
  pipeline or LLM ran.
- RAG = 6 hand-written markdown provisions + a `HashingEmbedder` (no real embeddings, no LangChain).
- SAR default is `MockSarDrafter` (deterministic canned text); the real LLM path exists but only
  runs via `make run-live` and is never tested against a live provider.

**Intended outcome.** A real, runnable AML training path a developer invokes locally, producing an
honestly-gated **CANDIDATE** model. The **synthetic generator stays the system-of-record** for CI,
tests, the committed `v0-fixture` active model, the LR baseline, and retrain — shipped/hermetic
behavior is unchanged. **In addition, RAG retrieval is upgraded from the placeholder hash embedder
to real `text-embedding-3-small` embeddings** (Phase 6), so "RAG" performs genuine semantic
retrieval rather than keyword overlap — wired as an opt-in live path with the same
offline-default / Infisical-keyed philosophy as the training and SAR paths.

**Honesty fixes bundled in.** The same research surfaced three places where the UI/demo overstates
reality; these are now in scope: the SAR "Submit" button actually files the report (Phase 7); the
live LLM path is proven against a real provider and streams natively instead of faking it
(Phase 8); and seeded demo alerts are marked distinct from real pipeline-generated alerts so the
dashboard can't be mistaken for live evidence (Phase 9).

## Dataset choice: IBM AML-Data (primary), IEEE-CIS (optional)

The primary real dataset is **IBM AML-Data (AMLworld)**, not IEEE-CIS. IEEE-CIS is card/e-commerce
fraud — a poor fit for FraudLens's structuring/layering/multi-bank/SAR claims — and under the
IEEE→canonical mapping 3 of the 10 model features collapse to constants. IBM AML-Data fits better:

- **Domain fit:** transaction-level laundering labels, multi-bank, multi-currency, real AML graph
  typologies (structuring, layering) — matches what FraudLens claims.
- **Cleaner feature mapping:** `Payment Format` (ACH/Wire/Cash/Credit Card/Bitcoin/Cheque) maps
  directly onto the existing `_CHANNEL_RISK` tokens, `Timestamp`→`occurredAt`, `From/To Bank`+
  `Account`→`origin/dest` and **`agency_id` tenancy**, `Is Laundering`→label. This makes
  `channel_risk`/`velocity`/direction non-degenerate — dissolving the IEEE constant-feature problem.
- **Governance:** CDLA-Sharing-1.0, download/cache-only (never committed) — same posture as any
  real dataset. Available on Kaggle (so the Infisical `/ml` Kaggle-creds decision holds) and via
  [IBM/AML-Data](https://github.com/IBM/AML-Data).

The loader is built **dataset-pluggable** (`--source {synthetic,ibm-aml,ieee-cis}`); **IBM AML-Data
is the recommended/built primary**, IEEE-CIS remains an optional secondary fraud track that slots
into the same mapping framework later.

**Dataset variant, size & what gets deployed.** The Kaggle page is ~8 GB because it bundles **six
separate datasets** (HI/LI × Small/Medium/Large), not one training set — you train on **one**.
Default is **`HI-Small`** (~5M transactions — already large; "Small" is only relative to Large's
~180M). The variant is config-named, so `HI-Medium`/`HI-Large` are available, but **not** recommended:
on our fixed 10-feature space more rows give steep diminishing returns (the feature ceiling, not row
count, is the limit) while `HI-Large` costs hours of training + SMOTE. **Nothing dataset-related is
ever deployed or committed:** training is offline and emits a small **model artifact** (booster +
calibration + SHAP background, a few MB) — that artifact is the only thing served (committed
`v0-fixture` for the demo; **Azure Blob** in prod). The raw CSV stays in gitignored
`.local/aml_data/` on the training machine — never in git, never in the deployed image, never needed
at runtime.

## Locked decisions

1. **Opt-in real, synthetic stays served.** Real training is local/offline/never-committed; the
   committed `v0-fixture` and demo active model stay synthetic. Promotion of a real candidate to
   active stays human-gated.
2. **Kaggle creds via Infisical `/ml`** (`--env=prod`), injected at runtime; never commit
   `kaggle.json`/`access_token`. Uses Kaggle's **new API Tokens** → env var **`KAGGLE_API_TOKEN`**
   (The `/ml` path + `KAGGLE_API_TOKEN` real value are set, and the target dataset is ungated — see
   Manual prerequisites; nothing else is needed for the real-data fetch.)
3. **No raw or real-derived data committed** (license + PHI posture). Raw files → gitignored
   `.local/aml_data/`; only the PHI-free 10-feature matrix leaves the loader.
4. **Do not lower the committed gate thresholds.** A failing real candidate is reported honestly,
   never gamed; a separate documented gate profile may be used for local benchmarking only.
5. **All LLM + embedding calls route through the single OpenRouter provider** (`OPENROUTER_API_KEY`
   in `/llm`) — SAR chat *and* RAG embeddings. No direct OpenAI/Anthropic/Gemini provider keys are
   used or required; the direct providers in `config/llm/providers.yml` are left inert. OpenRouter
   exposes an OpenAI-compatible embeddings endpoint, so `text-embedding-3-small` is reachable through
   it. This centralizes cost/governance on one key.

## Manual prerequisites (one-time, human — not code)

Everything below is outside the codebase. Implementation, `make ci`/`pre-pr`, all hermetic tests, and
`make run` (mock) need **none** of it — credentials are required only for the opt-in *live*
verification. On the current dev machine the toolchain items (libomp, Docker, uv, npm, importable
xgboost) are **already satisfied**; the outstanding items are credentials.

- **Real Kaggle credentials (Phase 4 real train).** The `/ml` Infisical path +
  `KAGGLE_API_TOKEN` real value is **✅ set** in `/ml` (Kaggle's new API-token flow; env var
  `KAGGLE_API_TOKEN`). **✅ nothing else outstanding:** the dataset
  **`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`** is an *ungated* public dataset (no
  terms/rules to accept — confirmed on the dataset page), so the API token alone can pull it. Caveat:
  the full dataset is **~8 GB** (all HI/LI × small/medium/large), so the fetch script pulls only the
  **`HI-Small_Trans.csv`** variant (see Phase 2), never the whole zip.
- **`/llm` provider key (Phases 6 & 8 live paths).** `OPENROUTER_API_KEY` is **✅ set** in `/llm` —
  the single key for *both* live SAR (Phase 8) and live embeddings (Phase 6), since all LLM +
  embedding traffic goes via OpenRouter. No direct `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` needed. Not
  needed for CI or the mock default.
- **Supabase (for `make run-live` / real Auth) — ✅ largely set up.** A dedicated **`fraudlens`**
  project (ref `xvgmouphkvdkpsjiqkva`, us-east-1) is created; `SUPABASE_URL`, `DATABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY` are in Infisical prod **root `/`**; the Alembic schema is migrated (23
  tables) and the [auth-claims SQL](../supabase/2026-07-06-auth-claims.sql) (custom-claims hook + `users`
  RLS) is applied. `DATABASE_URL` uses the **aws-0-us-east-1 session pooler** (the direct
  `db.<ref>.supabase.co` host is IPv6-only and unresolvable in this environment); asyncpg's default
  no-verify SSL works. **`make run-live` was fixed to `infisical run --path=/ --recursive`** so it
  resolves `OPENROUTER_API_KEY` (`/llm`) + root Supabase secrets together. **JWT algorithm ✅ done
  (Option A):** the project already ships an asymmetric **ES256** key + working JWKS, so
  `auth_jwt_algorithm` was widened to `Literal["ES256","RS256"]` with default **`ES256`** (matches
  Supabase) plus an ES256 verifier test — no RSA/dashboard work needed. **Auth dashboard toggles ✅
  done:** `public.custom_access_token_hook` is registered + enabled as the **Custom Access Token
  hook** and **open signup is disabled**. Supabase Auth is now fully configured; `make run` (mock,
  local Postgres via Docker) still needs none of this. **First admin for `run-live` login:** the
  fixed-UUID demo users (incl. "Demo Admin", role ADMIN) work for the `make run` dev-bypass, but real
  Supabase Auth keys the token `sub` to `auth.users.id` (Supabase-generated), which won't match those
  UUIDs — so use the built-in reconciliation: create one user in Supabase Auth (Authentication →
  Users → Add user, auto-confirm), then set `bootstrap_admin_user_id`/`bootstrap_admin_email` and
  re-run the seed (`_ensure_bootstrap_admin` in [seed.py](../scripts/seed.py) upserts a `public.users`
  ADMIN row bound to that real auth id).
- **Local toolchain (already satisfied here):** `brew install libomp` (xgboost import on macOS),
  Docker running (local Postgres), `uv sync --all-packages`, `npm --prefix frontend ci`. The `kaggle`
  package installs via `uv sync` once Phase 1 adds it.

No other Infisical paths or manual secrets are introduced by this plan.

## Anti-skew principle (core mechanic)

The live scorer builds the 10 features (`FEATURE_NAMES` in
[features.py](../packages/fraudlens-ml/src/fraudlens_ml/scoring/features.py)) via graded
`country_risk`/`channel_risk` lookups and a strict `[t-24h, t)` same-account window
(`extract_features`). **Both the trainer and `import_ieee.map_ieee_row` must produce features
through the exact same mapping**, or the model trains on a different distribution than it serves.
Enforced by a shared mapping module and a test asserting the trainer's per-row output equals
`extract_feature_vector(context)` column-for-column.

## Phase 1 — Shared dataset→canonical mapping + plumbing
- Add **`kaggle>=1.8`** to [packages/fraudlens-ml/pyproject.toml](../packages/fraudlens-ml/pyproject.toml)
  (CLI ≥1.8 supports the new `KAGGLE_API_TOKEN` auth, lists files, and downloads a **single file** via
  `-f <name>` — important since the full dataset is ~8 GB; training-time only, not in the backend
  runtime); `uv lock`. (`kagglehub>=0.4.1` `dataset_download(..., path=...)` is an equivalent fallback.)
- **Verify Kaggle access (Phase 1 acceptance check):**
  `infisical run --env=prod --path=/ml -- kaggle datasets files ealtman2019/ibm-transactions-for-anti-money-laundering-aml`
  should list the dataset's files — confirming `KAGGLE_API_TOKEN` authenticates and the dataset is
  reachable, and revealing the exact variant filenames (e.g. `HI-Small_Trans.csv`) to pin in Phase 2.
- New setting in [settings.py](../backend/src/fraudlens_backend/settings.py):
  `aml_data_dir: str = Field(default=".local/aml_data", ...)` (relative → `REPO_ROOT`, like
  `_artifacts_root`).
- `.gitignore`: add `/.local/aml_data/`, `/data/aml_data/` (belt-and-suspenders), `**/kaggle.json`;
  fix the stale "Akeyless" comment → "Infisical". Add `check-added-large-files` (`--maxkb=1024`) to
  [.pre-commit-config.yaml](../.pre-commit-config.yaml) as a hard stop against committing raw CSVs.
- **New `scripts/lib/aml_mapping.py`** — the single source of truth for source→canonical proxies,
  imported by BOTH the trainer and (for the IEEE track) `import_ieee.py`. Per-source mappers that
  emit the **canonical channel/country/direction tokens** the scorer's `channel_risk`/`country_risk`
  understand:
  - IBM AML-Data: `Payment Format`→channel token (ACH→`ach`, Wire→`wire`, Cash→`cash`,
    Credit Card→`card`, Bitcoin→`crypto`, Cheque/Reinvestment→documented default); currency/bank →
    country proxy; direction from send/receive; account key = `Bank+Account` for windowing.
  - IEEE-CIS (optional): `ieee_channel(ProductCD)`, `ieee_country(addr2)`, `IEEE_EPOCH`,
    `is_outbound=1.0` constant. Refactor `import_ieee.map_ieee_row` to consume these (replacing the
    raw `ProductCD` / hardcoded `"US"`), verified by `test_import_ieee.py`.
  - Constants named (no magic values); mapping tables + rationale documented in the docstring and
    `model-lifecycle.md`.

## Phase 2 — Dataset fetch script
- **New `scripts/fetch_dataset.py`** (SUMMARY header; Pydantic boundary): `--source`, `--dir`,
  `--force`. Downloads a **single file** via `kaggle datasets download -d <slug> -f <variant>` —
  default slug **`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`**, variant
  **`HI-Small_Trans.csv`** (config-named; ~5M rows, higher illicit ratio) — into
  `settings.aml_data_dir` (never the ~8 GB full bundle), verifies the file, returns a frozen Pydantic
  `DatasetPaths` (files + per-file sha256 + row_count). Reads creds only from
  the `KAGGLE_API_TOKEN` env var (Kaggle's new API-token auth); never logs the token; refuses
  train/import in `environment=="prod"` (download itself is read-only).

## Phase 3 — Real training loader → `DataSplit`
- Lift `DataSplit` + split helpers into neutral **`scripts/lib/dataset.py`**; update imports in
  `synthetic_fraud.py`, `train_model.py`, `train_baseline.py`, `retrain.py`, `test_train_model.py`
  (keeps `train_candidate(split, gates, seed=...)` unchanged).
- **New `scripts/lib/aml_fraud.py`** mirroring `synthetic_fraud.py`'s surface:
  - `load_frame(paths, source) -> pd.DataFrame` (keep only mapping-relevant columns).
  - `build_feature_matrix(df, source) -> (X in FEATURE_NAMES order, y)` — **PHI-free output only**;
    replicates `extract_features` semantics exactly: `amount_log=log1p(amount)`; hour/day from
    timestamp; `is_round_amount` via cent-precision integer check equivalent to `Decimal % 100`;
    `country_risk`/`channel_risk` by mapping tokens through the **actual** `features.*_risk`
    functions (never re-implemented); `velocity_24h`/`amount_24h_sum_log`/`distinct_countries_24h`
    via groupby account-key sorted by timestamp with a two-pointer `[t-86400, t)` half-open window
    (velocity excludes current row; sum & distinct-countries include it); `is_outbound` from
    send/receive (IBM) or constant (IEEE). Assert column count/keys == `FEATURE_NAMES`.
  - **Chronological split** (`split_chronological`): split by timestamp (train = earliest,
    calibration/holdout = latest), **not** random permutation — never random-row-sample rare AML
    patterns, and preserve laundering subgraphs (keep an account's transactions together within a
    fold). Document that this differs from the synthetic path's seeded random split (which stays as
    is for CI determinism).
  - **Column selection & encoding are dictated by the scorer, NOT chosen freely (anti-skew).** Keep
    only the raw columns needed to compute the 10 features (Timestamp, Amount Paid + currency, Payment
    Format, From/To Bank+Account, Is Laundering); drop the rest (`Amount Received`, etc.). **No
    one-hot and no learned encoding** — categoricals (Payment Format, country) collapse to a single
    graded numeric via the scorer's `channel_risk`/`country_risk` lookups (auditable, ordinal, robust
    to unseen values), and **no feature is invented that `extract_features` can't reproduce at
    inference**. Account/bank ids are used only transiently to group the 24h window, then discarded
    (PHI-free matrix). Unknown categories → the same documented defaults the scorer uses (not NaN).
    XGBoost needs no scaling; the LR baseline's `StandardScaler` stays internal to `train_baseline`.
    (Richer features — one-hot, currency pairs, graph/typology signals — require extending
    `FEATURE_NAMES` + `extract_features` + the canonical schema together; out of scope here.)
- **Tenancy:** map source bank/institution → `agency_id` in the demo-ingest path so the real data
  exercises multi-tenant isolation (recommended: a real **three-agency** demo). The training feature
  matrix itself stays agency-agnostic (global training, ADR-015), but the ingested demo rows carry
  real `agency_id`s.

## Phase 4 — Source switch in the trainers (fixture stays synthetic)
- [scripts/train_model.py](../scripts/train_model.py): `_load_split(source, *, seed, rows, settings)`
  for `source ∈ {synthetic, ibm-aml, ieee-cis}`. Real sources resolve `settings.aml_data_dir`, call
  `fetch_dataset._verify_present` (**fail fast if files absent — never auto-download in train**),
  build features, chronological-split.
  - **Versioned dataset manifest:** `TrainingDataset.snapshot_query` + `content_hash` record
    `source`, **license** (e.g. `CDLA-Sharing-1.0`), per-file sha256, **schema (column list)**,
    dataset **date/version**, and the deterministic transform id. Still PHI-free
    (hashes/counts/schema/spec only). `_version_label` includes `source` so candidates never collide.
  - CLI: `--source` **defaults to `synthetic`** (preserves CI/fixture hermeticity); real runs pass
    `--source ibm-aml`. Optional `--sample-rows` (seeded, stratified-on-label) for fast iteration;
    full dataset is the default for a registered candidate. `--fixture`, `--rows`, `--seed` unchanged.
  - **`write_fixture_bundle` / `--fixture` stay synthetic** — the committed `v0-fixture` must
    regenerate hermetically with no download; keeps
    `test_fresh_train_reproduces_committed_fixture_metrics` valid.
- [scripts/train_baseline.py](../scripts/train_baseline.py): same `--source` switch on `main`; the
  "beats baseline" gate auto-compares on the same real split.
- `retrain.py`: only update its `DataSplit` import here. **Flagged follow-up:** make reviewed matured
  labels actually train a candidate from ingested real data (today it re-uses the synthetic
  generator, so reviewed labels only gate eligibility) — separate Phase-10 design.

## Phase 5 — Makefile, Infisical, docs
- [Makefile](../Makefile): `fetch-data: infisical run --env=prod --path=/ml -- $(UV) run python
  scripts/fetch_dataset.py --source ibm-aml`; `train-aml: infisical run --env=prod --path=/ --recursive
  -- $(UV) run python scripts/train_model.py --source ibm-aml` (**must** wrap in `infisical run` — the
  candidate registration needs `DATABASE_URL` at root; `--recursive` also pulls `/ml`). Keep
  `train-model` synthetic (CI/demo/`--fixture`). Fix stale help text; update `.PHONY`.
- `docs/runbooks/infisical-secrets.md`: add `/ml | prod | KAGGLE_API_TOKEN`.
- `docs/runbooks/model-lifecycle.md`: document the layered dataset strategy (IBM AML-Data primary,
  IEEE-CIS optional, synthetic for CI), the mapping tables, chronological-split rationale, licenses,
  and the fetch/train workflow; state the committed fixture stays synthetic and gates are honest.
- Reword the now-false [synthetic_fraud.py](../scripts/lib/synthetic_fraud.py) docstring
  ("ships no real IEEE-CIS download").
- Update [ARCHITECTURE.md](../docs/architecture/ARCHITECTURE.md) to separate **target-state** claims
  from **implemented** behavior (RAG hashing vs real embeddings; mock vs live SAR; synthetic vs real
  training). Run `make docs` (never hand-edit AUTOGEN regions).

## Phase 6 — Real RAG embeddings (`text-embedding-3-small`), opt-in like the SAR live path

**Why.** Today both ingest and retrieval use the 256-dim offline `HashingEmbedder` — a signed
hashed bag-of-tokens ([ingest.py:117](../packages/fraudlens-ml/src/fraudlens_ml/rag/ingest.py)) —
and [pipeline_wiring.py:385](../backend/src/fraudlens_backend/pipeline_wiring.py) wires it
**unconditionally**. That is keyword overlap dressed up as vectors: it cannot match a query to a
provision that means the same thing in different words, so "RAG" is effectively lexical. The
`Embedder` protocol seam exists but the live provider embedder was never built. This phase wires
real `text-embedding-3-small` embeddings as an **opt-in** path, mirroring the mock/live SAR split.

**Design principle (same as training + SAR):** the offline `HashingEmbedder` stays the
**default / CI / committed-fixture-index** embedder (deterministic, keyless, hermetic —
system-of-record for tests and `local-demo`); real embeddings are opt-in, keyed from Infisical
`/llm`, consistent with `llm_mode=live` / `make run-live`.

**Layering (critical constraint):** `fraudlens-ml` must **not** import `fraudlens-llm`
(ruff-banned). So the real embedder is **injected**, exactly like `SarDrafter`:
- The `Embedder` protocol stays in `fraudlens_ml.rag.ingest` (unchanged).
- **New backend package `backend/src/fraudlens_backend/rag/`** (mirrors `backend/.../sar/`) with
  `LlmClientEmbedder` — adapts the async `LlmClient.embed()` → the sync `Embedder` protocol
  (`embed_documents`/`embed_query`). The backend may import both `ml` and `llm`, so this is
  layering-clean.
- **Shared factory `build_embedder(settings)`** returns `HashingEmbedder` (offline) or
  `LlmClientEmbedder` (live), used by **BOTH** `pipeline_wiring.build_pipeline_components` **and**
  `scripts/ingest_rag.py` — so ingest and retrieval can never use different embedders (which would
  silently corrupt retrieval; see provenance below).

**Async→sync bridge (the key implementation decision).** `Embedder.embed_query`/`embed_documents`
are sync and `Retriever.retrieve` is sync, but `LlmClient.embed` is async and the pipeline runs
inside a live event loop — so a naive `asyncio.run` inside a running loop would raise. Recommended:
`LlmClientEmbedder` owns a **dedicated background event-loop thread** and bridges via
`run_coroutine_threadsafe` (keeps the sync `Embedder` protocol and the `Retriever` untouched).
Ingest is a plain script → it can `asyncio.run` directly. (Alternative considered — add an async
`Retriever.aretrieve` + async `Embedder` seam; heavier, touches `ml`. Document whichever is chosen.)

**Embedding-space compatibility + provenance (correctness-critical).** Hashing = 256 dims;
`text-embedding-3-small` = 1536 dims ([catalog.yml:111](../config/llm/catalog.yml)) — **different
spaces and dimensions**, so an index built by one embedder cannot be queried by the other (ChromaDB
dimension mismatch, or garbage cosine scores). Therefore:
- `build_index` records **embedding provenance** (embedder kind + model id + dimension) in the
  ChromaDB collection metadata (alongside the existing `hnsw:space: cosine`).
- Extend `rag_version` to encode the embedder (e.g. `rag-v1` hashing → `rag-v2-te3s` for
  text-embedding-3-small), so every `rag_retrievals` audit row states which embedding space produced
  it.
- **Retrieval fails closed on mismatch:** if the retriever's embedder provenance ≠ the index's
  recorded provenance (or the dimension disagrees), fall back to the existing deterministic
  **lexical** path and flag it (`mode="lexical"`) — never return garbage vector hits. Extend
  `Retriever`/`index_status` to read and check provenance.
- Switching embedders requires an **index rebuild** (`make ingest-rag`). The committed/baked fixture
  index stays hashing-based; the live 1536-dim index is built locally / at deploy.

**Config (no hardcoded model id — rule 4).** New `config/llm/rag.yml` (mirrors
[config/llm/sar.yml](../config/llm/sar.yml)) naming the embedding model id, plus a setting
`rag_embedding_mode: Literal["offline","live"] = "offline"`. **Route via the `openrouter` provider**
(OpenAI-compatible, already in [providers.yml](../config/llm/providers.yml) with `OPENROUTER_API_KEY`),
not the direct `openai` provider — consistent with locked decision 5 (all LLM+embeddings via
OpenRouter). Add an OpenRouter embedding entry to `catalog.yml` (e.g. `openrouter/openai/text-embedding-3-small`,
1536 dims; confirm the exact OpenRouter model id + that the key has embeddings access at
implementation). `LlmClient.embed` requires an `openai_compatible` provider — OpenRouter is one, so
no client change is needed.

**Governance / PHI.** Ingest embeds **public** FinCEN/BSA text → no PHI, safe to send to the provider
(via OpenRouter).
The retrieval **query must be PHI-free**; routing through `LlmClient.embed` adds PHI masking +
data-class + provider-policy enforcement as defense-in-depth (already implemented —
[client.py:204](../packages/fraudlens-llm/src/fraudlens_llm/client.py)); pass an appropriate
`DataClass` for public-reg retrieval. Cost is negligible (`text-embedding-3-small` = $0.02/1M
tokens; corpus is a handful of provisions; one short query per investigation) and is logged via
`LlmClient` usage.

**Tests (hermetic — no network in CI).**
- Keep every existing hashing-based RAG test unchanged (deterministic chunking, top-k relevance,
  lexical fallback) — hashing stays the tested default.
- `LlmClientEmbedder` unit test with a **fake `LlmClient`/adapter** returning canned vectors (mirror
  [test_sar_drafter_live.py](../tests/integration/test_sar_drafter_live.py)) — asserts the
  async→sync bridge, batch `embed_documents`, and vector dimension.
- Provenance mismatch test: a 256-dim index queried by a 1536-dim embedder → fails closed to lexical,
  flagged (never a dimension crash or garbage hit).
- `build_embedder` factory: `offline`→`HashingEmbedder`, `live`→`LlmClientEmbedder`.
- No real OpenAI call in CI (like the live SAR path); an opt-in real-provider test may sit behind an
  env flag.

**Docs / Makefile.**
- Reword the now-stale "not wired here" / "the seam" language in
  [scripts/ingest_rag.py](../scripts/ingest_rag.py) + [ingest.py](../packages/fraudlens-ml/src/fraudlens_ml/rag/ingest.py) docstrings.
- `ingest-rag` stays offline default; add `ingest-rag-live` (or `--mode live`) via
  `infisical run --env=prod --path=/llm`.
- Update [ARCHITECTURE.md](../docs/architecture/ARCHITECTURE.md) RAG description to state the
  hashing-default vs live-`text-embedding-3-small` split, provenance, and the rebuild requirement.

## Phase 7 — Make "Submit the report" actually file the SAR (fix the UI no-op)

**Why.** The investigation wizard's final step ("Submit the report") is a **no-op that lies**.
`handlePrimary` on the last step ([Investigation.tsx:259](../frontend/src/pages/Investigation.tsx))
fires a positive `notify({title: "SAR submitted for review", ...})` toast and `navigate(paths.alerts)`
— but makes **no API call**. The SAR status never transitions, nothing persists, no audit log is
written; the UI reports a successful filing that never happened. (The neighboring "Regenerate"
button on the SAR step already does it right — POST + spinner + success/failure handling — so Submit
is the odd one out.)

**The backend + client already support this** — the gap is purely the unwired button:
- Endpoint exists: `POST /api/v1/alerts/{alertId}/sar/review` → `review_sar`
  ([alerts.py:314](../backend/src/fraudlens_backend/api/v1/alerts.py)), body
  `SarReviewRequest {decision: "approve"|"reject"|"edit", editedContent?, reason?}`
  ([models/alerts.py:143](../backend/src/fraudlens_backend/models/alerts.py)); `approve` →
  SAR `APPROVED` + deferred PDF + audit log (all implemented).
- Client method exists: `apiClient.reviewSar(alertId, body)`
  ([api.ts:574](../frontend/src/lib/api.ts)).

**The one real blocker — the view has `runId` but not `alertId`.** `InvestigationSnapshot`
([api.ts:204](../frontend/src/lib/api.ts)) carries `runId`, `transactionId`, `sarDraftId`,
`sarStatus` — but **no `alertId`**, and the review endpoint is keyed by `alertId`. Alerts link to
runs by `run_id`. So:
- **Backend:** add a nullable `alertId` to the investigation snapshot (and the SSE snapshot),
  resolved via the existing `alerts.run_id` relationship. Null when the run raised no alert
  (low-risk) → Submit is disabled.
- **Frontend:** thread `alertId` into `InvestigationState`; on the last step call
  `client.reviewSar(alertId, {decision: "approve", editedContent?})` inside an async handler with a
  spinner — **mirroring the existing `handleRegenerate` pattern** — and only show the success toast +
  `navigate(paths.alerts)` **on resolve**, `notifyError` on failure. Disable Submit when there is no
  `alertId` or no approvable draft. Keep the existing `hasPermission` RBAC gate.
- `reject`/`edit` are supported by the same endpoint and may be surfaced too, but `approve` is the
  minimum to stop the lie.

**Governance.** `reason`/`editedContent` are PHI-masked server-side; the review already writes the
audit log + status transition. No new endpoint, no new permission.

**Tests.** The Investigation page's SSE factory is injectable — extend its tests to assert Submit
calls `reviewSar` with `approve`, renders success only on resolve, fires an error toast on failure,
and stays disabled/no-op when there is no alert. Backend test: the snapshot exposes `alertId` for a
run that raised an alert, `null` otherwise.

## Phase 8 — Prove the live LLM path end-to-end + real provider token streaming

Two must-have gaps between the handoff's "live Claude/GPT SAR drafting with token streaming" and
the build.

**8a — Opt-in real-provider E2E test.** The live SAR drafter is only ever tested with a fake adapter
returning canned JSON ([test_sar_drafter_live.py](../tests/integration/test_sar_drafter_live.py),
`_FakeAdapter`), so nothing proves a real provider call works (auth, request shape, response
parsing, grounding, cost). Add a real-provider E2E test:
- A registered `pytest` marker (e.g. `llm_live`) that **skips by default** and runs only when opted
  in via `infisical run --env=prod --path=/llm -- pytest -m llm_live` (real `OPENROUTER_API_KEY`;
  all traffic via OpenRouter). Never runs in normal CI (no keys) — mirrors the opt-in
  real-dataset train.
- Drives `LiveSarDrafter` against the config-selected model ([config/llm/sar.yml](../config/llm/sar.yml))
  on a PHI-free `SarInput`; asserts a **grounded** `SarDraftResult` (no fabricated citation ids
  survive), token usage + USD cost recorded, `prompt_version`/`prompt_hash` set, and that a forced
  provider error degrades to a terminal `failed` result (never throws except the budget 429).
- Documented in `docs/runbooks/model-lifecycle.md` (how to run; expected cost is cents).

**8b — Native provider token streaming.** Streaming is currently faked: the live client returns the
**full** completion, then `stream_result` re-chunks the finished text into word-sized deltas — its
own docstring admits "the completed text is re-chunked"
([streaming.py:6](../backend/src/fraudlens_backend/sar/streaming.py)). With a real LLM this is
*worse* than nothing (the analyst waits for the whole generation, then watches a replay). Wire real
streaming:
- **fraudlens-llm:** add a streaming generate path on `LlmClient` + the **`openai_compatible`
  adapter** (`AsyncOpenAI` `chat.completions.create(stream=True)`, pointed at OpenRouter), yielding
  provider deltas through the existing guardrail pipeline. (The direct Anthropic adapter can gain the
  same but is inert under OpenRouter-only routing — decision 5.)
- **Reconcile with grounding + output guardrails (state the constraint plainly):** the SAR model
  output is a structured JSON body that must be **fully received → JSON-parsed → citation-grounded →
  output-guardrail-scanned** before it is safe to show — you cannot ground a half-emitted citation
  list or scan a partial output. So the design: consume the provider stream **server-side** (real
  streaming — lower time-to-first-byte, resilient to long generations, real progress) and stream to
  the client only the **validated, grounded, guardrail-passed** narrative. Recommended prompt/schema
  change: have the model emit the human-readable **narrative first as a streamable text field** with
  the structured citation block last (grounded at end), so the narrative streams as genuine deltas
  while grounding/scan still gate the terminal result. If that schema change is deferred, at minimum
  drive the emission from a real provider stream rather than one blocking call. Update `stream_result`
  + `LiveSarDrafter` + the `streaming.py` docstring to match reality.
- **Mock drafter unchanged** (deterministic, keyless, no provider) — stays the default + tested path.
- **Tests:** a fake *streaming* adapter (assert deltas assembled in order, guardrails/grounding still
  applied, terminal `COMPLETED` carries the grounded result); CI stays hermetic — the real stream is
  exercised only by the opt-in 8a marker.

## Phase 9 — Distinguish seed/fixture alerts from real pipeline alerts (complete fix)

**Why.** `_seed_alerts` in [seed.py](../scripts/seed.py) hand-plants ~29 alerts + ~16 SARs (from
`_ALERT_PLAN` + canned `_DEMO_SAR_CONTENT`). Each is backed by a thin `analysis_runs` header, but the
seed creates **no** `analysis_results`, `rag_retrievals`, or `model_inference_logs` — the evidence a
real investigation produces. On the dashboard a seeded alert and a real pipeline alert are
**indistinguishable**, so a viewer concludes the system investigated ~29 cases when the real count is
zero. Same honesty theme as Phase 7.

**Complete fix — structural + visual:**
- **Data model:** add an `AlertOrigin` enum (`PIPELINE` | `SEED`) to
  [db/models/enums.py](../backend/src/fraudlens_backend/db/models/enums.py) and an `origin` column on
  `Alert` ([db/models/alerts.py](../backend/src/fraudlens_backend/db/models/alerts.py)) — `NOT NULL`,
  `default=PIPELINE`, CHECK-constrained via `str_enum`. An Alembic migration adds it with a
  `PIPELINE` server-default so existing rows are treated as pipeline.
- **Seed:** `_seed_alerts` sets `origin=SEED` on every planted alert (the demo SAR content is already
  labelled synthetic). Idempotency unchanged.
- **Pipeline:** the real alert-creation path sets `origin=PIPELINE` explicitly (the default, made
  explicit as a clarity + test anchor).
- **API:** surface `origin` (camelCase) on the alert read models (list + detail) — PHI-free scalar.
- **Frontend:** badge `SEED` alerts in [AlertTable.tsx](../frontend/src/components/AlertTable.tsx) /
  [AlertQueue.tsx](../frontend/src/components/AlertQueue.tsx) and the AlertDetail header with a neutral
  "Sample data" `Badge` (semantic palette per DESIGN.md — never brand green), so scaffolding is never
  mistaken for a real investigation.
- **Tests:** backend — seeded alerts persist `origin=seed`, pipeline-raised alerts `origin=pipeline`,
  migration up/down, API includes `origin`; frontend — the badge renders for seed alerts and is absent
  for pipeline alerts.
- Run `make docs` so the ERD/AUTOGEN regions pick up the new column.
- **Deeper alternative (noted, not built):** have the seed *run the real pipeline* over seeded
  transactions so demo alerts carry full evidence — heavier; the `origin` marker is the complete
  in-scope honesty fix.

## Governance checklist
- Embedding provenance recorded on the index; retrieval fails closed on embedder/dimension mismatch
  (no silent garbage). Real embeddings are opt-in via OpenRouter (`OPENROUTER_API_KEY` from Infisical
  `/llm`, the single key for all LLM+embeddings); hashing stays the keyless CI/local-demo default.
- No raw/real-derived data committed; raw only in gitignored `.local/aml_data/`; `kaggle.json`
  ignored; large-file pre-commit guard added.
- Feature matrix + manifest PHI-free (only 10 numeric features + label leave the loader; raw
  accounts/banks/ids never enter the artifact/manifest/DB).
- Prod refusal preserved in `fetch_dataset` and the real branch of `train_model._amain`.
- SUMMARY headers on new files; Pydantic (`extra="forbid"`) at boundaries; named constants; creds +
  dir from config/Infisical. Determinism: seeded synthetic split + SMOTE/Platt unchanged; real path
  deterministic given pinned sha256 + chronological split + stable sort.

## Verification
1. `make ci` / `make pre-pr` — headers, no-hardcoding, gitleaks, tenancy, **≥90% branch coverage**
   all pass with real data absent (CI stays hermetic; synthetic path untouched).
2. New unit tests (no download; run against a small **committed, canonical-column** sample fixture —
   add `data/aml_train_sample.csv` with real canonical headers + `isLaundering`):
   - `test_aml_mapping.py`: every `Payment Format` maps to a real channel token; null handling.
   - `test_aml_fraud.py` (**anti-skew guarantee**): `build_feature_matrix` per-row output equals
     `extract_feature_vector(context)` column-for-column; 24h boundary; `is_round_amount` cent
     equivalence; chronological split keeps an account's rows within one fold; determinism.
   - Extend `test_train_model.py`: a `--source ibm-aml` run on the sample registers a CANDIDATE with
     `source="ibm-aml"`, licensed PHI-free manifest, distinct label. Existing synthetic fixture tests
     unchanged.
   - `fetch_dataset`: unit-test `_verify_present` + prod refusal; mock the network.
3. **Browser E2E** (the honest-behavior check the current demo lacks): after `make run`, upload a
   **canonical-column** CSV (NOT the IEEE-named `data/ieee_cis_sample.csv`, which the upload API
   rejects — see [transactions.py](../backend/src/fraudlens_backend/api/v1/transactions.py)), e.g.:
   ```csv
   externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country
   DEMO-AML-001,9100,USD,2026-07-01T10:00:00+00:00,SYNTH-ORIGIN-A,SYNTH-DEST-1,wire,US
   DEMO-AML-004,9000,USD,2026-07-04T23:00:00+00:00,SYNTH-ORIGIN-A,SYNTH-DEST-4,crypto,IR
   ```
   Investigate `DEMO-AML-004` and assert the pipeline **persists** `analysis_results`, a RAG
   retrieval, an inference log, and SAR provenance (prompt_version/hash), and that tenant isolation
   holds — proving a real run occurred (not just seed scaffolding). SAR stays mock unless `run-live`.
   Then **click "Submit the report" (Phase 7) and assert the SAR row transitions to `approved` and an
   audit-log row is written** — not just a toast.
4. Manual real train (local, one-time — creds already in Infisical): `make fetch-data` →
   `make train-aml`. Confirm a CANDIDATE `model_versions` + `model_evaluations` verdict +
   `job_executions(train)`; artifact under gitignored `data/models/<label>/`, NOT committed;
   `git status` clean of raw data; gates reported honestly; active pointer + committed fixture
   untouched.
5. **RAG embeddings (Phase 6):** `make ci`/`pre-pr` stay green on the hashing default (hermetic).
   Manual live check (uses `OPENROUTER_API_KEY`, already set): `make ingest-rag-live` (builds a
   1536-dim index tagged `rag-v2-te3s`) → run an investigation with `rag_embedding_mode=live` and
   confirm `rag_retrievals.mode="vector"` with the real `rag_version`. Payoff test: a query that is
   *semantically* related to a provision but shares few keywords (e.g. "breaking deposits below the
   reporting threshold" vs the structuring provision) is retrieved by the real embedder but missed
   by hashing/lexical — proving genuine semantic retrieval. Verify fail-closed: pointing a live
   retriever at the 256-dim hashing index degrades to `mode="lexical"`, never a crash or garbage hit.
6. **Live LLM (Phase 8):** `make ci`/`pre-pr` stay green (mock default + fake streaming adapter; the
   `llm_live` test skips). Opt-in check: `infisical run --env=prod --path=/llm -- pytest -m llm_live`
   calls a real provider and passes (grounded draft, cost recorded). Under `make run-live`, the SAR
   narrative streams from a genuine provider stream (first tokens appear before generation completes),
   and the persisted result is still fully grounded + guardrail-scanned.
7. **Seed-alert honesty (Phase 9):** after `make run`, the dashboard badges seeded alerts as
   "Sample data"; a freshly investigated transaction's alert (step 3) shows no badge
   (`origin=pipeline`). Backend asserts the two `origin` values + the migration up/down.
8. `drift-check plans/2026-07-10-real-aml-dataset-training.md all` before declaring done.

## Recommended follow-ups (out of this plan's scope)
- **Retrain from real matured labels** so analyst review actually feeds retraining (today `retrain.py`
  re-uses the synthetic generator, so reviewed labels only gate eligibility) — a separate Phase-10
  design.
- **LLM model selector:** restore a Claude/GPT/Gemini **SAR-model** selector in the UI (the handoff's
  cross-vendor comparison) only if that comparison is still a product goal — the current
  `ModelSelector` picks XGBoost registry versions, not LLMs. Cross-cutting guardrail for all LLM work:
  keep the LLM to narratives/summaries/Q&A/extraction — never scoring, labeling, final decisions, or
  autonomous SAR filing; evaluate against FinCEN's who/what/when/where/why rubric, human-reviewed.
- **RAG corpus depth** (the embeddings work itself is Phase 6): expand beyond the 6 curated markdown
  provisions and add authoritative source URLs + document versions/effective dates. The ingest
  pipeline already scales to it — `load_corpus` globs the directory, so this is add-files-and-reindex
  content work, not an engineering change.
- **Richer feature space (future).** Adding features is incremental, not a rewrite — the versioned
  `FeatureSpec` (persisted per model) lets old models keep serving their spec while a new model trains
  on the new spec and rolls out through the existing canary/shadow gates (no big-bang migration).
  Effort scales with where the data lives: **(Tier 1)** features derivable from the canonical
  transaction + same-account history ≈ *a day* — add to `FEATURE_NAMES` + `extract_features`, bump
  `FEATURE_SPEC_VERSION`, regenerate the fixture, update tests (the anti-skew test covers the new
  column automatically); **(Tier 2)** features needing a new canonical field also touch
  `fraudlens-core` + the ingest schema/API + a `transactions` migration ≈ *days*; **(Tier 3)**
  graph/sequence/cross-entity features need a **feature store** for train/serve parity ≈ *weeks*. The
  caveat is inherent to serving real transactions, not to this plan: a feature that lives only in the
  training dataset (not on a live transaction) can't be added without also enriching the live ingest
  path (Tier 2). The `extract_features` single-source + anti-skew discipline make all three safe.
