# FraudLens — Screen Redesign to "Direction A" (full-stack)

> **Canonical home on approval:** save this file to `plans/2026-06-24-screens-redesign-fullstack.md`
> (per AGENTS.md §Plans, `YYYY-MM-DD-<title>.md`), then run `drift-check` per phase.

## Context

`docs/FraudLensScreens.html` is a **bundled SPA mockup** ("Chosen direction (A), built into the
real app shell") of the analyst surface. It is authored with **legacy inline styles** —
hardcoded hex (`#f6f7f4`, `#fff`, `#d4d7d1`), px radii (`border-radius:14px`), fixed widths
(sidebar `178px`, decision rail `288px`). We do **not** port that markup. We reimplement the same
screens in the existing **React 19 + Vite + Tailwind + TypeScript** app using the **centralized
`wise` design tokens**, and we **extend the backend** so the screens are backed by real data.

The app already implements every screen (`frontend/src/pages/*`) and every domain widget
(`FraudGauge`, `ProgressSteps`, `ShapBarChart`, `RagPanel`, `SarStream`, `ModelLifecyclePanel`),
but the screens are visually plainer than the mockup and contain **real duplication**:
`Transactions.tsx` inlines a `<table>` (lines ~150–216) and `AlertTable.tsx` is a *second* table;
the page-header block and stat-card markup are copy-pasted across all six pages. This effort
(a) factors those repeats into a small set of **shared, token-only primitives**, (b) re-composes
the screens to match Direction A, responsively, and (c) adds the backend data the mockup shows.

**Two product decisions confirmed with the user:**
1. **Active nav = green *indicator stripe*** (not a full green fill). Honors the mockup's green
   cue while upholding DESIGN.md's "green = CTA only" rule — sanctioned by `DESIGN.md`
   `ex-app-shell-row.activeIndicator: {colors.primary}`.
2. **Extend the backend to match** the mockup's data (new alert statuses, list totals,
   transaction tags, dashboard counts, actor roles) — not a presentation-only reinterpretation.

## Non-negotiable constraints (carry on every change)

- **Centralized theme, zero duplication of tokens.** `frontend/tailwind.config.ts` is the SOLE
  source of color/radius/spacing/type values (mirrors `DESIGN.md`). **No new hex/px** in
  components; reuse tokens. Extend the config **only** if a genuinely new token is required, and
  only by mirroring `DESIGN.md`. `src/index.css` stays a thin `@tailwind` + base layer.
- **No duplication of logic/markup.** Build each repeated pattern **once** (see Shared Components)
  and reuse. Reuse existing primitives (`Button`, `Card`, `Badge`, `Select`, `Textarea`), libs
  (`lib/risk.riskTone`, `lib/format.{formatCurrency,formatDateTime,humanize}`,
  `useAsync`/`useAsyncAction`, `AsyncBoundary`). Backend: extend existing list responses with a
  `total` field — **never** add parallel/near-duplicate endpoints or tables. **Banned name
  prefixes:** `v2`/`new_`/`temp_`/`old_`/`legacy_`/`copy_`/`_refactored`.
- **Responsive.** Mobile `<768` / tablet `768–1023` / desktop `≥1024` → Tailwind `md:`/`lg:`
  (defaults: `md`=768, `lg`=1024). Fixed widths are layout, not tokens (arbitrary `lg:w-[288px]`
  is acceptable; ad-hoc colors are not).
- **Every source file** (`.py`/`.ts`/`.tsx`) carries the top-of-file SUMMARY header
  (`Summary`/`Key classes`/`Key functions`/`Notes`) — `scripts/check_headers.py` is CI-blocking.
- **Tests + ≥90% coverage** both stacks (branch coverage on Python). New/changed behavior needs
  behavioral tests. Frontend pages take an injectable `client` prop (`src/test/factories.ts`);
  backend integration tests run on in-memory SQLite (`tests/conftest.py`).
- **Governance (AGENTS.md §Security):** no PHI in UI/logs/URLs; every tenant query scoped by
  `agency_id` (reuse `TenantScopedRepository`); actor identity surfaced as **role**, never a name.
- **Gate:** `make pre-pr` (= `fmt docs ci`) must be green; `make docs` regenerates OpenAPI/ERD/
  header inventory; run `drift-check plans/<file>.md phase=<N>` after each phase. **No commits/
  pushes without explicit permission** (Golden Rule 1).

## Single source of truth for theme (already in place — extend, don't fork)

`tailwind.config.ts` exposes: `primary{,active,neutral,pale}`, `on-primary`, `ink{,deep}`,
`body`, `mute`, `canvas{,soft}`, `positive{,deep}`, `warning{,deep,content}`,
`negative{,deep,darkest,bg}`, `accent{orange,cyan}`; radii `sm..xl,pill,full`; spacing
`xxs..3xl`; fonts `sans`(Inter)/`display`(Manrope); the `display-*`/`body-*`/`caption`/
`button-md` scale; `maxWidth.container`. **Risk→tone** is centralized in `lib/risk.ts`
(`riskTone(band)→StatusTone`). The mockup's grays (`#f6f7f4`,`#d4d7d1`,…) map to existing
tokens (`canvas`, `canvas-soft`, `mute`); its 10/12/14px radii map to `rounded-md`/`lg`/`xl`.
**Do not add these mockup values to the config.**

## Shared components — build once, reuse everywhere (the DRY core)

| Component | Location | API (props) | Replaces / consumed by |
|---|---|---|---|
| `PageHeader` | `components/ui/PageHeader.tsx` | `{title, description?, actions?, aside?}` (renders `<h1>` + sage band) | The copy-pasted `<header class="…bg-canvas-soft p-3xl rounded-xl">` in all 6 pages |
| `StatTile` | `components/ui/StatTile.tsx` | `{label, value, hint?, emphasis?:"lg"|"md", as?:"dl"}` | Dashboard's 4 inline stat cards; ModelAdmin metric `<dl>` (one component, **not** a separate `MetricStat`) |
| `DataTable<T>` | `components/ui/DataTable.tsx` | `{columns:Column<T>[], rows, rowKey, onRowClick?, empty?, caption}`; `Column={id,header,cell,align?,srOnlyHeader?}` | The 2 hand-rolled tables in `Transactions.tsx` + `AlertTable.tsx` |
| `RiskDot` | `components/RiskDot.tsx` | `{band, showLabel?}` → colored dot + visually-hidden `humanize(band)` label | "risk as a single dot" in tables (uses `riskTone`) |
| `SegmentedControl` | `components/ui/SegmentedControl.tsx` | `{options:{value,label}[], value, onChange, ariaLabel, size?}` (radio/tablist, a11y) | Alerts status chips; ModelAdmin LIVE/REGISTRY; replaces Alerts `<Select>` |
| `DecisionRail` | `components/DecisionRail.tsx` | `{title?, children, className?}` sticky right rail (layout shell) | Investigation + AlertDetail action columns |
| `Timeline` | `components/Timeline.tsx` | `{items:{id,title,meta,body?}[]}` | AlertDetail activity log; Investigation activity |
| `Pagination` | `components/ui/Pagination.tsx` | `{shown, total, hasMore?, onMore?}` → "Showing N of T" | Transactions + Alerts table footers |

Each ships with a colocated `*.test.tsx` (repo convention) and a SUMMARY header. Reuse, don't
duplicate: `Badge` keeps the worded status pills; `RiskDot` is only the table dot. `AlertTable`
**stays** as the alert-column wrapper around `DataTable` (don't delete; don't rename to a banned
prefix). Extend `ProgressSteps` with a "Step N of 5" affordance — do **not** add a second stepper.

### Shared config + display helpers (centralize — don't repeat per page)

Beyond components, the per-page **option lists and formatters are duplicated today** (`RISK_OPTIONS`
in `Transactions.tsx`, `STATUS_OPTIONS` in `Alerts.tsx`, `LABEL_OPTIONS` in `AlertDetail.tsx`, the
canary ramp steps in `ModelLifecyclePanel.tsx`). Centralize:

- **`lib/options.ts`** — single home for `RISK_BAND_OPTIONS`, `ALERT_STATUS_OPTIONS`,
  `TRAINING_LABEL_OPTIONS`, `CANARY_RAMP_STEPS`, and the model-metric **display definitions**
  (`{label, key, format}`). Pages import these instead of re-declaring.
- **`lib/format.ts`** (extend) — `formatAge(date)` → "22m" / "12d ago" (the mockup's relative ages);
  keep using `formatCurrency`/`formatDateTime`/`humanize`.
- **`lib/risk.ts`** (extend) — `severityRank(sev)` for the "highest risk first" client sort (keeps
  the single risk vocabulary alongside `riskTone`).
- **`extractModelMetrics(metrics)`** — one helper to read precision/recall/auc out of the registry's
  loose `metrics` JSON, used by **both** the Dashboard active-model panel and ModelAdmin (never parse
  the dict in two places).

## Responsive strategy (Tailwind classes only)

- **Shell** (`App.tsx`): `nav` is `flex-row overflow-x-auto md:flex-col md:w-[200px] md:shrink-0`;
  content `flex-col md:flex-row`. (Migrate the current `sm:` to `md:` to match the 768 breakpoint.)
- **KPI grid** (Dashboard): `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-lg`.
- **Tables**: wrap `DataTable` in `overflow-x-auto` (horizontal scroll on mobile — simpler than
  stacking for 4–5 columns).
- **Decision-rail pages** (Investigation, AlertDetail): `grid grid-cols-1 lg:grid-cols-[1fr_320px]
  gap-xl`; **content first in source order** so the rail drops *below* on mobile.
- **Touch targets** already ≥48px (`py-md` + `button-md`) — no change.

---

# Phases

> Ordering principle: ship the **visual redesign + DRY first using existing data** (Phase 1, no
> backend risk), then add each backend capability immediately followed by its frontend consumer.
> Every phase ends with: new-file headers, tests green, `make pre-pr` green, `drift-check phase=N`.

## Phase 0 — Setup
- Branch off `feat/foundation` (or `main`) e.g. `feat/screens-redesign`. Confirm baseline
  `make pre-pr` is green so later failures are attributable.
- Save this plan to `plans/2026-06-24-screens-redesign-fullstack.md`.

## Phase 1 — Shared primitives + shell (frontend-only, existing data)
**Goal:** introduce every shared primitive, kill the table/header duplication, and re-skin the
shell — with **no backend dependency**, so the redesign is shippable on its own.

- **Shell** `frontend/src/App.tsx`: active nav row = ink text on subtle surface + **green stripe**
  (`border-l-4 border-primary`, was `border-ink`); keep `aria-current="page"`. Update the file's
  SUMMARY note (it currently states the ink choice). Apply responsive nav (above).
- **Build primitives** (table above) with tests: `PageHeader`, `StatTile`, `DataTable`, `RiskDot`,
  `SegmentedControl`, `DecisionRail`, `Timeline`, `Pagination`.
- **Build shared config/helpers** (see above) with unit tests: `lib/options.ts`, `formatAge`,
  `severityRank`, `extractModelMetrics`.
- **Refactor pages to consume them** (existing data only):
  - All 6 pages → `PageHeader` (preserve current H1 text; `App.test.tsx` asserts the
    `Investigations` heading + `aria-current`). Pages import option lists from `lib/options.ts`.
  - `AlertTable.tsx` → thin wrapper defining alert columns over `DataTable`; severity via `RiskDot`
    + `Badge`; action label **"Review"** per the mockup (update `AlertTable.test.tsx`).
  - `Transactions.tsx` → drop inline `<table>`, use `DataTable` + `RiskDot` risk column; **keep the
    "Investigate" action** (tested by name) as a stop-propagation action cell. Add the mockup's
    **search box** (`TextInput`, placeholder "Search by ID, amount, or counterparty…") filtering the
    loaded rows client-side over externalId/amount/currency/masked accounts/channel/country/risk
    band. Risk `<Select>` → `SegmentedControl` (All/Low/Medium/High/Critical) while keeping the API
    `riskBand` query.
  - Dashboard 4 cards → `StatTile`. Alerts `<Select>` filter → `SegmentedControl` (over the
    **current 4** statuses for now). `AlertDetail` + `Investigation` action buttons → wrapped in
    `DecisionRail`; `AlertDetail` activity list → `Timeline`.
- **Tests:** one `*.test.tsx` per new primitive (row-click vs action-cell isolation, keyboard
  activation, empty slot, tone mapping, a11y labels); update `Alerts.test.tsx` (Select→chips) and
  keep `Transactions`/`App`/`AlertDetail` tests green.
- **Acceptance:** `jscpd` no longer flags the two tables; all screens visually match Direction A
  using existing fields; `make pre-pr` green.

## Phase 2 — Alert lifecycle: +2 statuses (`pending_review`, `escalated`)
**Goal:** the 7 mockup buckets (All, Open, In review, Pending review, Completed, Archived,
Escalated) become real. **Additive** approach — no destructive rename (see Decision note below).

- **Enum** `backend/src/fraudlens_backend/db/models/enums.py`: add `PENDING_REVIEW="pending_review"`,
  `ESCALATED="escalated"` to `AlertStatus`. (`resolved`/`dismissed` stay as DB values; the
  frontend maps their **labels** to "Completed"/"Archived".)
- **Migration** `alembic/versions/0002_extend_alert_statuses.py`: since columns use
  `str_enum(…, native_enum=False)` (stored as text/check, per `db/base.py`), the migration rebuilds
  the `alertstatus` check via `batch_alter_table` to include the 2 new values. Mirror
  `0001_initial_schema.py` style; provide `downgrade()`.
- **State machine** `backend/src/fraudlens_backend/db/repositories/alerts.py`:
  `_ACTION_TARGET[ESCALATE]=ESCALATED` (was IN_REVIEW); add `PENDING_REVIEW` and `ESCALATED` as
  **non-terminal** (only `COMPLETED`/`ARCHIVED`≡resolved/dismissed are terminal). Transitions:
  `open|pending_review|in_review|escalated → escalate→escalated · resolve→resolved · dismiss→dismissed
  · assign→in_review · comment→(unchanged)`; terminal admits none (409). Update `next_alert_status`.
- **Raise path** `backend/src/fraudlens_backend/db/repositories/analysis.py` (`raise_alert`): raise
  as `PENDING_REVIEW` when `review_flags` is non-empty (the mockup's "Forced review: amount > $25k"),
  else `OPEN`. Threshold already lives in config (reuse; see `settings.py`/`config/*.yaml`).
- **Dashboard** `models/dashboard.py` `AlertMetrics`: add `pending_review`, `escalated` int fields;
  `api/v1/dashboard.py` `_to_response` maps all values (default 0). (`completed`/`archived` tiles
  read the existing `resolved`/`dismissed` fields.)
- **Alert AMOUNT column** `models/alerts.py` `AlertView`: add `amount:Decimal` + `currency:str`
  projected from the linked `Transaction` (the mockup's AMOUNT column — real data, not fabricated);
  join in `db/repositories/alerts.py` `list_alerts`/`_to_alert_view`. AGE is derived on the frontend
  via `formatAge(createdAt)` — no new field.
- **Frontend** `lib/api.ts`: extend `AlertStatus` union + `AlertMetrics`. Add `STATUS_LABELS`
  (`resolved→"Completed"`, `dismissed→"Archived"`, others via `humanize`) used by chips, `Badge`,
  `Timeline`. Expand the Alerts `SegmentedControl` to All + 6 buckets; add an **Escalate** action to
  the AlertDetail `DecisionRail`.
- **Tests:** extend `tests/unit/test_alerts_workflow.py` (new transitions + new terminals);
  `tests/integration/test_alerts_api.py` (filter by new statuses, escalate→escalated, raise→
  pending_review on forced flag); `test_dashboard_api.py` (new counts); frontend chip + label tests.
- **Acceptance:** all 7 buckets filter real rows; escalate yields `escalated`; forced-review alerts
  appear under "Pending review".

> **Decision — additive, not rename.** Renaming `resolved→completed`/`dismissed→archived` would
> touch ~11 files + a data migration + the resolve-writes-training-label path, for no functional
> gain (the screen labels are cosmetic). We keep the DB values and map labels on the surface
> (the codebase already separates value from display via `humanize`). If canonical naming is later
> wanted, it's a self-contained follow-up.

## Phase 3 — List totals + pagination footer
- **Alerts** `models/alerts.py` `AlertListResponse`: add `total:int` (`Field(...,ge=0,…)`).
  `db/repositories/alerts.py`: add `count_alerts(status=…)`; `api/v1/alerts.py` calls it. (Keeps
  existing limit/offset paging.)
- **Transactions** `models/transactions.py` `TransactionListResponse`: add `total:int`.
  `db/repositories/transactions.py`: add `count(risk_band=…)`; `api/v1/transactions.py` wires it.
  (Keeps cursor paging; `total` is the filtered count.)
- **Frontend:** `Pagination` footer on both tables — "Showing N of T" from `rows.length` + `total`;
  "Load more" when `nextCursor`/more remain. Update `lib/api.ts` response types + `factories.ts`.
- **Tests:** integration asserts `total`; frontend asserts footer copy + load-more.
- **Acceptance:** footers show real totals (no fabricated counts).

## Phase 4 — Transaction tags (`new counterparty`, `cross-border`, `wire`)
- **Schema** `db/models/core.py` `Transaction`: add `tags: Mapped[list[str]] =
  mapped_column(JSONB_TYPE, nullable=False, default=list)`. Migration
  `alembic/versions/0003_add_transaction_tags.py` (add JSON column, `server_default '[]'`,
  expand-contract; `downgrade` drops it).
- **Derivation (pure, unit-tested helper)** e.g. `backend/src/fraudlens_backend/services/txn_tags.py`:
  `wire` ⇐ `channel=="wire"`; `cross_border` ⇐ `country != home_country` (home country from
  non-secret `config/default.yaml`, e.g. `transactions.home_country: "US"` + `settings.py` field —
  no Agency schema change); `new_counterparty` ⇐ no prior txn with same masked `dest_account` for
  the agency (one scoped `EXISTS` query in `repositories/transactions.py.ingest`). Store tags at
  ingest.
- **API** `models/transactions.py` `TransactionResponse`: add `tags:list[str]`; project in
  `api/v1/transactions.py` `_to_response`.
- **Frontend:** render tags as small neutral `Badge`s under the TXN ID cell in `DataTable`. Types +
  factories updated.
- **Tests:** unit for the helper (each rule); integration (wire/cross-border/new-counterparty rows);
  frontend renders tags.
- **Acceptance:** tags appear and are correct; backfill is acceptable as empty (older rows untagged).

## Phase 5 — Dashboard + model metrics
- **Backend** `models/dashboard.py`: `AlertMetrics.to_review` (alerts with non-empty `review_flags`
  OR `status∈{pending_review,in_review}`); `TransactionMetrics.today` (count since UTC midnight);
  `ModelHealthMetrics.{precision,recall,auc}` (`float|None`, read from the active
  `ModelVersion.metrics` JSON — `latest_drift_severity`/`canary_percent` already exist).
  Implement counts in `db/repositories/dashboard.py.collect`; map in `api/v1/dashboard.py`.
- **Frontend Dashboard** redesign with `StatTile` KPIs ("Open alerts", "To review", "Txns today")
  + an **ACTIVE MODEL** card: precision/recall/AUC via `StatTile` (`as="dl"`) sourced through
  `extractModelMetrics`, "Drift: low", "canary @ N%", run/version line. Missing metrics degrade to
  `—` (house style). The open-alerts queue is sorted client-side by `severityRank` then recency
  ("highest risk first").
- **Frontend ModelAdmin**: reuse `StatTile` + `extractModelMetrics` for the metric trio; add a
  **LIVE / REGISTRY** `SegmentedControl` toggling the deployment card vs the versions list
  (client-side, no refetch) in `ModelLifecyclePanel.tsx`. **Preserve all existing lifecycle
  callbacks** (retrain / promote / approve / canary ramp / evaluate / rollback) and keep the
  **drift-reports** section — this is a layout recompose, not a feature drop.
- **Tests:** dashboard integration (to_review, today, precision/recall/auc present/omitted);
  frontend tiles + LIVE/REGISTRY toggle.
- **Acceptance:** Dashboard + Model match the mockup's KPI/metric panels with live data.

## Phase 6 — Activity timeline actor role + Investigation/Review polish
- **Backend (PHI-safe role, never a name):** `db/models/alerts.py` `AlertAction` + `record_action`
  stamp `actor_role` (from `TenantContext.role`); `models/alerts.py` `AlertActionView` adds
  `actor_role:str`. Migration `0004_add_action_actor_role.py` (nullable text column). Keep
  `actor_id` opaque.
- **Frontend `Timeline`:** rows render `formatDateTime · role` (e.g. "14:42 · analyst"); synthesize a
  leading "created · system" row from `alert.createdAt`. Wire into AlertDetail (and Investigation
  if a persisted activity list is shown).
- **Investigation:** "Step N of 5" affordance on `ProgressSteps`; header "AL-… · flagged <date>" +
  "High risk · 0.87" via `PageHeader.aside`; `DecisionRail` with "Looks good — continue →" nav +
  status summary (no invented run-mutation endpoints — Investigation has none). **Preserve** the SSE
  streaming, snapshot fallback, cold-start, failed, and terminal-event handling
  (`lib/investigation.ts`/`lib/sse.ts`) — recompose layout only.
- **AlertDetail:** "Resolve & label" framing in the rail; surface `review_flags` as "Basis for
  filing" / "FinCEN SAR" context; keep every action routed through the single `useAsyncAction.run`.
- **Tests:** action role persisted + projected; Timeline renders role; stepper "Step N of 5";
  AlertDetail actions still fire.
- **Acceptance:** Investigation reads as the guided stepper + rail; Review matches "Resolve & label".

## Phase 7 — Responsive QA, docs, drift-check, gate
- Sweep every screen at 375 / 768 / 1280px (sidebar reflow, rail drops below, tables scroll, KPI
  grid columns). Fix with breakpoint classes only.
- `make docs` (OpenAPI for the new fields/statuses, ERD for the `tags`/`actor_role` columns, header
  inventory). `make pre-pr` green. `drift-check plans/2026-06-24-screens-redesign-fullstack.md all`.

---

## Verification (end-to-end)

- **Backend:** `make backend-coverage` (≥90% branch) + `make backend-coverage-diff`; targeted
  `pytest tests/unit/test_alerts_workflow.py tests/integration/test_{alerts,transactions,dashboard}_api.py`.
- **Frontend:** `npm --prefix frontend test` (Vitest, ≥90%); `npm --prefix frontend run typecheck`;
  `npm --prefix frontend run lint`; `npm --prefix frontend run build`.
- **Centralization audit (the "no duplication / centralized theme" gate):** no **static** inline
  `style=` in `frontend/src` (the only allowed `style` is for runtime values — gauge stroke, bar
  width %); no hex/rgb color literals outside `tailwind.config.ts`; no option list or tone map
  declared in more than one file (grep). `jscpd` + `eslint-plugin-tailwindcss` clean.
- **Migrations:** `make db-migrate` applies cleanly; confirm `downgrade` for each new revision.
- **Manual / visual:** `make local-demo` (boots Postgres + API + frontend), `make db-seed`; walk
  Dashboard → Transactions (search, tags, risk dot, pagination) → open Investigation (stepper +
  rail) → Alerts (7 chips, totals) → Review (resolve & label) → Model admin (LIVE/REGISTRY,
  metrics). Optionally drive screenshots via the Preview MCP tools.
- **Gate:** `make pre-pr` green; `drift-check` per phase; `make tenancy-check` (every new query
  scoped by `agency_id`); `gitleaks`/`check_no_secrets` clean. **No commit/push without explicit
  go-ahead.**

## Risk register
- **Test-name coupling (highest):** tests assert by accessible name/label (`Investigate`,
  `Investigations` H1 + `aria-current`, the "Filter by status" select). Any copy/role/label change
  must update the matching test **in the same phase**.
- **Enum migration portability:** `str_enum(native_enum=False)` rebuilds the check via
  `batch_alter_table` (SQLite test path + Postgres) — verify both `upgrade`/`downgrade` against the
  in-memory SQLite suite *and* a real Postgres via `make local-demo`.
- **`new_counterparty` cost:** the per-ingest `EXISTS` query is scoped + indexed
  (`ix_transactions_agency_id_*`); keep it a single existence check, not a scan.
- **Coverage drift:** colocate a `*.test.tsx`/`test_*.py` with every new file; `DataTable` generic
  branches and `SegmentedControl` selection need explicit cases or the changed-file gate trips.
- **Scope discipline:** extend existing models/endpoints with fields; never add `*_v2` or parallel
  tables/endpoints (rule 5 + banned prefixes).
