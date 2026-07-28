# Portfolio demo story — configuration ownership and change safety

> The portfolio demo is **one** runtime tenant whose every visible number is produced by the real
> rules → XGBoost → SHAP → blend → alert → SAR pipeline and then **asserted** against
> [`config/portfolio-demo.yaml`](../../config/portfolio-demo.yaml). Nothing is written directly into
> `transactions.risk_band`, `alerts`, or `sar_drafts`. A distribution that no longer matches the
> configuration **fails** — it never quietly adapts. The rationale is
> [ADR-018](../architecture/adr/ADR-018-portfolio-demo-data-provenance.md); the acceptance walkthrough
> is [portfolio-demo-uat.md](portfolio-demo-uat.md).

## The four configuration surfaces

Every demo-related value lives in exactly one of these. If you find one restated somewhere else,
`make demo-literals-check` is supposed to fail — it derives its forbidden literals *from* the story
document, so a re-pin re-aims the guard with no edit to the checker.

| Surface | File | Loaded by | Holds |
|---|---|---|---|
| **Story** | `config/portfolio-demo.yaml` | `load_portfolio_demo_config()` (frozen Pydantic, `extra="forbid"`) | The demo agency, its personas, the pinned model, the authored case pack, the expected distribution, the workflow actors and notes |
| **App config** | `config/default.yaml` → `config/<env>.yaml` → `FRAUDLENS_*` | `AppSettings` (`pydantic-settings`) | The enable flag, the story **filename**, and the synthetic-password value |
| **Workflow** | `.github/workflows/portfolio-demo-reset.yml`, `deploy-backend.yml` | GitHub Actions | Reset schedule, concurrency group, and the two enablement repo variables |
| **Infisical `prod`** | — | `infisical run` at job/command time | `DATABASE_URL`, Supabase keys, `FRAUDLENS_DEMO_AUTH_PASSWORD` |

### Story document (`config/portfolio-demo.yaml`)

| Value | Key |
|---|---|
| Agency id, name, slug, research partition key | `agency.*` |
| Persona key, seed user id, email, display name, initials, role, picker name/tag/accent | `personas[]` |
| Dev-bypass fallback persona | `default_bypass_persona` |
| Pinned model label + feature-spec version | `model.*` |
| Provider modes the story was calibrated under | `execution.*` |
| Authored transactions (amounts, accounts, channels, countries, anchored offsets) | `scenarios[].transaction` |
| Per-scenario expected band, expected rule **codes**, alert/SAR targets, scored flag | `scenarios[]` |
| Pinned totals: transactions, unscored, per-band, per-alert-state, per-SAR-state | `expected.*` |
| Workflow actors, assignee, synthetic review notes | `workflow.*` |
| Story identity (external-id namespace, schema/story version, anchor) | top level |
| Case-pack partition count and tenant weights | `case_pack_*` |
| Calibration report windows | `probe.*` |

Deliberately **not** here, because they belong to other owners:

| Value | Real owner |
|---|---|
| Band lower bounds, alert threshold, blend model weight | global `system_config` (`riskBandThresholds`, `alertThreshold`, `riskBlendModelWeight`) — read through `load_risk_policy()`, tunable via `PATCH /api/v1/config` |
| Rule thresholds, windows, high-risk country list | `aml_rules.params`, seeded from `DEFAULT_RULE_DEFINITIONS`; the story references rule **codes** only |
| Low-confidence review window | `AppSettings.review_low_confidence_margin` (cross-checked against `probe.low_confidence_margin` at load) |
| Label maturity days | global `system_config.labelMaturityDays` |
| Batch-score cap, history window/cap | existing `AppSettings` fields — the demo introduces no second limit |
| RBAC, the alert state machine, API routes, enums, FK delete order | source code, as protocol/domain definitions |

### App config (`AppSettings`)

| Key | Default | Meaning |
|---|---|---|
| `portfolio_demo_enabled` | `false` **in Python and in YAML** | Gates the projection route and is required before the bootstrap will run in prod. It is a security gate, so it fails closed in code — deleting the YAML key leaves it off rather than crashing the app. `make run-live-demo` overlays it to `true` for its own children (the demo is what that command is for); `make run-live` leaves it closed. |
| `portfolio_demo_config_file` | `portfolio-demo.yaml` | A **filename**, resolved under `find_config_dir()`. Absolute paths, `~`, upward traversal, and symlinks escaping the config dir are rejected by the loader. |
| `demo_auth_password` | unset | The public synthetic demo credential; supplied by `FRAUDLENS_DEMO_AUTH_PASSWORD`. |

### The synthetic password is public by design — and still injected

The demo password is **intentionally non-secret and frontend-visible**: the login picker fetches it
from `GET /api/v1/portfolio-demo/config` so a visitor can click a persona and be signed in without
typing anything. It authenticates only the synthetic personas in the single demo tenant.

It is nevertheless **never committed**. `scripts/check_no_secrets.py` matches the key `password` and
rejects any non-placeholder value, so the story document carries the scanner's sanctioned
env-reference form instead:

```yaml
auth:
  public_synthetic_password_env: FRAUDLENS_DEMO_AUTH_PASSWORD
```

The value lives in Infisical `prod` at `/` and reaches the app as an environment variable. This is
strictly better than the source literal it replaced: `make secrets-scan` stays strict, and rotating
the credential is a secret-store change rather than a commit. **Do not** inline the value, weaken the
repo-wide scanner, or rename the key to dodge its regex.

### Workflow surface (GitHub Actions)

| Control | Where | Effect |
|---|---|---|
| `vars.PORTFOLIO_DEMO_BOOTSTRAP_ENABLED` | `deploy-backend.yml`, `migrate` job | Runs the bootstrap pre-promote, so a story that does not match its configuration blocks the traffic shift |
| `vars.PORTFOLIO_DEMO_RESET_ENABLED` | `portfolio-demo-reset.yml` | Enables the scheduled reset; the job also runs in `environment: production` because it destroys visitor state |
| `on.schedule.cron` | `portfolio-demo-reset.yml` | **The one honest literal.** GitHub Actions cannot read a repo variable in a cron expression, so the schedule is written in the workflow. Only the schedule — enablement is the variable above, and `workflow_dispatch` covers ad-hoc runs. Do not duplicate this cron in Python, Make, or the story YAML. |

Both are repo **variables**, never secrets: enablement is a deployment decision, not a credential.

Before flipping `PORTFOLIO_DEMO_BOOTSTRAP_ENABLED` on, two preconditions must hold — the pinned model
bundle named by the story must be reachable at the artifacts root, and the runner's resolved
`llm_mode` / `rag_embedding_mode` must equal the story's `execution:` block (the bootstrap refuses a
mismatch rather than telling a differently-calibrated story).

## Which changes are safe, and which force recalibration

Recalibration means: re-run `make portfolio-demo-probe`, read the achieved bands, and **pin the new
`expected:` block by hand as a reviewed commit**. Never lower a threshold, retune a rule parameter, or
write a band directly to make a distribution fit.

### Tier 1 — cosmetic; no re-verification needed

Presentation-only fields that no assertion reads:

- `personas[].display_name`, `initials`, `picker_name`, `picker_tag`, `picker_accent`
  (the accent must be one of the semantic tokens **code** owns, so the palette rules in
  [`DESIGN.md`](../../DESIGN.md) cannot be violated from YAML).
- `workflow.resolution_note` / `approval_note` / `rejection_note`.
- Comments anywhere in the document.

### Tier 2 — re-run the story; the pinned numbers do not move

Changes the load-time algebra or the bootstrap validates for you. Run
`make portfolio-demo-reset && make portfolio-demo-verify`:

- `story_version` — re-keys every derived external id, the advisory-lock key, and the audit request
  id at once. The previous story's rows are **not** adopted; reset first.
- `workflow.assignment_actor` / `resolution_actor` / `sar_review_actor` / `assignee` — validated at
  load against the RBAC policy, so an under-privileged persona fails before any write.
- `default_bypass_persona`, and adding a persona whose role duplicates an existing one.
- `probe.report_top_n`.

### Tier 3 — recalibration required

Anything that can move a band, an alert, or a SAR:

| Change | Why it recalibrates |
|---|---|
| `model.version_label` or `feature_spec_version` | A different bundle has different calibrated probabilities and its own `risk_thresholds`, which is what maps model score onto the band scale |
| Any `scenarios[].transaction` field | The features, the rule evaluation, and the same-account history window all change |
| Adding, removing, or reordering scenarios | History is windowed on the **masked** account and `occurred_at < before`, so a row's neighbours determine what fires |
| `story_anchor` or any `occurred_offset_hours` | Rapid-movement, velocity, and structuring windows are time-relative |
| `execution.llm_mode` / `rag_embedding_mode` | The bootstrap refuses a runtime mismatch; a RAG index built with one embedder cannot be queried with another |
| `system_config.riskBandThresholds` / `alertThreshold` / `riskBlendModelWeight` | These *are* the band boundaries and the alert decision |
| `aml_rules.params` or enabling/disabling a rule | Changes which codes fire and the `r` denominator (the summed weight of **enabled** rules) |
| `AppSettings.review_low_confidence_margin` | Flips an alert between `open` and `pending_review`; the loader cross-checks it against `probe.low_confidence_margin`, so a change must be made in both places or startup fails |

Two rows in the committed story sit within ~0.03 of the critical boundary; the YAML records which
ones and by how much. That note is **maintenance information, not a contract** — the contract is
`tests/integration/test_portfolio_demo_calibration.py`, which re-derives every band from the live
policy and the pinned bundle. A retune that flips either row is meant to fail that test, not to be
absorbed by widening the story.

## The recalibration loop

```bash
make run                      # throwaway DB: migrate, seed, activate the gates-passed bundle
make portfolio-demo-probe     # resolved-policy header + per-row p/r/codes/band + a paste-ready block
# ...edit the authored inputs, re-probe, and only then paste `expected:` into the YAML by hand...
make portfolio-demo-bootstrap
make portfolio-demo-bootstrap # second run must change nothing but `attempts`
make portfolio-demo-verify
```

`--probe` never writes `expected:` into the configuration. Pinning is a human commit, on purpose: an
auto-heal would turn a real regression into a silent rewrite of the story.

## Commands

| Command | What it does | Writes? |
|---|---|---|
| `make portfolio-demo-bootstrap` | Apply or resume the configured story; idempotent | yes |
| `make portfolio-demo-probe` | Calibration report (resolved policy, per-row `p`/`r`/codes/band) | ingests rows; persists no run, band, alert, or draft |
| `make portfolio-demo-verify` | Read-only expected-vs-actual table; non-zero exit on any delta | no |
| `make portfolio-demo-reset` | Delete the tenant's **operational** rows, then rebuild the baseline | yes (destructive) |
| `make portfolio-demo-smoke` | Run the smoke suite against a running demo (`SMOKE_BASE_URL=<url>`) | no |
| `make run-live-demo` | Boot live dev **and** bootstrap the story; prints the URL | yes |
| `make demo-literals-check` | Fail if any story value is restated outside the canonical document | no |

None of these targets names a config path: they all resolve the story location from the layered
settings. Pass `--config <path>` to `scripts/bootstrap_portfolio_demo.py` for a one-off document.

They do, however, need `DATABASE_URL` in the environment — the same way `db-seed` and
`activate-model` get it, and deliberately not baked into the recipe, so the same target works
against local Postgres and against Supabase. Against the deployed database that means wrapping them:

```bash
infisical run --env=prod --path=/ --recursive -- make portfolio-demo-verify
```

Without it they exit non-zero with `DATABASE_URL is not configured`. `make run-live-demo` already
wraps itself.

`--reset` keeps the agency, users, identities, rules, model registry, job history, and audit logs;
it deletes only the operational evidence and rebuilds it through the same ensure path. The FK delete
order stays in code — it is a schema invariant, not a deployment value.

## Related

- [portfolio-demo-uat.md](portfolio-demo-uat.md) — the human acceptance checklist.
- [ADR-018](../architecture/adr/ADR-018-portfolio-demo-data-provenance.md) — why the state is
  pipeline-produced, why the assertion fails rather than adapts, and why exactly one runtime tenant.
- [local-dev.md](local-dev.md) — booting the stack.
- [model-lifecycle.md](model-lifecycle.md) — how the pinned bundle is promoted.
- [infisical-secrets.md](infisical-secrets.md) — the secret boundary.
