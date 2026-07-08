# Role-aware alert workflow + real auth + live LLM (local-first, Azure last)

Three tracks, sequenced:

- **Track A — Role-aware alert UX** (the reported bug): show each role only the actions
  it can perform. Ships independently; works whether the role comes from the local dev
  bypass or a real JWT.
- **Track B — Go live locally**: real **Supabase Auth** (email/password, admin-invite),
  real **Supabase Postgres**, and **live LLM via OpenRouter**, all running against the
  real services **from local dev first**. Removes the "default admin" bypass from the
  real path.
- **Track C — Azure deploy (LAST)**: only after B works locally. Kept scaffolded/inert
  until the Azure account exists.

Track A is independent (UI gates on `session.role`, populated identically by a demo
pick or a verified JWT). Do A → B → C.

---

## Context

The 2026-07-06 RBAC hardening made the backend fail closed correctly and gates every
mutation by permission. Remaining gaps:

1. **Frontend doesn't reflect roles** — [`AlertDetail.tsx`](../frontend/src/pages/AlertDetail.tsx)
   renders every button to everyone, so an analyst sees **Approve** and learns it's
   forbidden only via a 403 toast. (Track A)
2. **Auth is still a dev bypass** — for an internal product this must be real: Supabase
   Auth issuing RS256 JWTs. The backend already ships the verifier
   ([`JwksTokenVerifier`, `deps.py:217`](../backend/src/fraudlens_backend/api/deps.py)),
   tenant + RBAC enforcement, and a password-less `users` table; it needs a real issuer
   wired. (Track B)
3. **LLM is mocked** — SAR generation runs a stub. OpenRouter is already a fully
   configured provider ([`config/llm/providers.yml`](../config/llm/providers.yml)); it
   just needs `llm_mode=live` + a key. (Track B)

**Decisions locked:** email/password auth; admin-invite provisioning; SAR model
`openrouter/openai/gpt-5-mini`; a single shared Supabase project for local + (future)
prod.

---

## Part 1 — How the workflow works in the real world (plain terms)

### The two-person rule (why the analyst doesn't approve)

A SAR is a **legal filing to the regulator**. AML shops run a **maker–checker /
four-eyes** control: whoever *investigates* isn't whoever *signs off the filing* — a
guard against both filing junk and *suppressing* a real report (plan §6.3; §10.4 shows
the **Reviewer** doing `sar/review {approve}` and the final `resolve`).

### "If the analyst sends everything to review, what's their job?"

The analyst does ~90% of the labor: owns the investigation, gathers evidence, writes the
case notes + **SAR narrative draft**, and makes the **recommendation** ("false positive
— clear it" / "suspicious — please review"). The reviewer does **QA and sign-off**, not
a second investigation. The analyst has a full-time job; they just don't hold the pen on
the regulatory signature or the ML-training disposition.

### Do we need a new "Send to review" state? — No.

`escalate` (a `triage_alert` action analysts *can* do) already moves the alert to
`ESCALATED` — that **is** "send to review." "Send for review" is a **relabel**.

### Every scenario, one role at a time

Lifecycle: `open → in_review (assign) / escalated (escalate) → resolved | dismissed`;
SAR: `draft → reviewed → approved | rejected`.

- **A — Analyst, false positive:** investigate → **Comment** → **Dismiss** (clears
  noise; records a `false_positive` label — see A1).
- **B — Analyst, suspicious:** investigate → SAR draft produced → **Regenerate** to
  refine → **Send for review** (= escalate). Never sees Approve/Reject; SAR shown
  read-only with "Awaiting reviewer approval."
- **C — Reviewer:** reads case + SAR → **Approve** (files → PDF) / **Reject** (reason) /
  **Save edit** → **Resolve** with a training label.
- **D — Admin:** all of the above + model/rules/config.
- **E — Auditor:** read-only; reads SAR, notes, timeline; **no action buttons**.

### Division of duties (end state)

| Action (button)         | Backend permission       | auditor | analyst | reviewer | admin |
|-------------------------|--------------------------|:------:|:-------:|:--------:|:-----:|
| View alert / SAR / log  | `view`                   |   ✅   |   ✅    |    ✅    |  ✅   |
| Comment                 | `triage_alert`           |        |   ✅    |    ✅    |  ✅   |
| **Send for review** (escalate) | `triage_alert`    |        |   ✅    |    ✅¹   |  ✅   |
| **Dismiss** (clear FP)  | `dismiss_alert` *(new)*  |        |   ✅    |    ✅    |  ✅   |
| SAR Approve/Reject/Edit | `review_sar`             |        |         |    ✅    |  ✅   |
| Resolve (writes label)  | `finalize_alert`         |        |         |    ✅    |  ✅   |

¹ Reviewer/admin keep the label **"Escalate"**; analyst sees **"Send for review."**

---

## Track A — Role-aware alert UX

**Invariant:** the backend 403 is the **only** source of truth. The UI only *hides doors
the user can't open*; the 403 mapping in [`errors.ts`](../frontend/src/lib/errors.ts)
stays as the safety net.

### A0 — Confirm backend invariants (verify only)

Rules CRUD is `manage_rules`-gated
([`rules.py:59`](../backend/src/fraudlens_backend/api/v1/rules.py)); model/config are
admin-only; alerts/SAR/transactions are permission-gated. Guard against regression only.

### A1 — Backend: split `dismiss` out of `finalize`; dismiss records a `false_positive` label

- [`api/deps.py`](../backend/src/fraudlens_backend/api/deps.py): add
  `Permission.DISMISS_ALERT = "dismiss_alert"`; grant to `ANALYST`, `REVIEWER`, `ADMIN`.
- [`api/v1/alerts.py`](../backend/src/fraudlens_backend/api/v1/alerts.py) `act_on_alert`
  (~234-237): map `dismiss → DISMISS_ALERT`, `resolve → FINALIZE_ALERT`, else
  `TRIAGE_ALERT`; drop `_FINAL_ALERT_ACTIONS`.
- **Dismiss writes a label:** when `payload.action == DISMISS`, create a training label
  (mirroring the resolve path ~254-262): `label=FALSE_POSITIVE`,
  `source=LabelSource.ANALYST_DISMISS`, `matured_at=now+load_label_maturity_days(...)`,
  `created_by=actor_id`.
- [`enums.py`](../backend/src/fraudlens_backend/db/models/enums.py): add
  `LabelSource.ANALYST_DISMISS = "analyst_dismiss"` (currently only `ANALYST_REVIEW`) —
  **no migration** (`native_enum=False`, stored as a string). This keeps provenance
  honest vs. §9.2's "reviewed decisions" so the training pipeline can weight/filter it.
- [`repositories/alerts.py`](../backend/src/fraudlens_backend/db/repositories/alerts.py):
  extend `create_training_label` (~248) with `label`/`source` params (`source` defaults
  to `ANALYST_REVIEW`, so the resolve call site is unchanged).
- Tests: analyst can `dismiss`, still 403s on `resolve`+SAR; a dismiss creates one
  `false_positive`/`analyst_dismiss` label row; resolve keeps its chosen label.

### A2 — Frontend: mirror the permission

- [`session.ts`](../frontend/src/lib/session.ts): add `"dismissAlert"` to
  `SessionPermission` + `ROLE_PERMISSIONS` (analyst/reviewer/admin).

### A3 — Frontend: make `AlertDetail` role-aware (the reported bug)

Read `useSession()`, derive `canTriage/canDismiss/canReviewSar/canFinalize`, gate each
control in [`AlertDetail.tsx`](../frontend/src/pages/AlertDetail.tsx) (mirror
[`App.tsx:106`](../frontend/src/App.tsx)):

- SAR review block (Approve/Reject/Save edit): only when `canReviewSar`; else draft
  read-only + "Awaiting reviewer approval."
- Comment: `canTriage`. Escalate: `canTriage`, labeled **"Send for review"** when
  `!canReviewSar` else "Escalate". Dismiss: `canDismiss`. Resolve + label `Select`:
  `canFinalize`. None (auditor): a read-only note instead of an empty rail.

### A4 — Frontend: sweep other write surfaces (hide, don't disable)

- [`Transactions.tsx`](../frontend/src/pages/Transactions.tsx): Import CSV →
  `ingestTransactions`; row Investigate → `startInvestigation`.
- [`Investigation.tsx`](../frontend/src/pages/Investigation.tsx): Regenerate →
  `startInvestigation`. ModelAdmin already page-gated; Rules has no UI.

### A5 — Cosmetic: rename `reviewer` persona label "Senior Analyst" → "Reviewer"

In [`session.ts`](../frontend/src/lib/session.ts) + [`App.tsx`](../frontend/src/App.tsx);
update `session.test.ts` / `Login.test.tsx`.

### A6 — Tests

Per-role button matrix in `AlertDetail.test.tsx`; auditor-hidden/analyst-shown in
`Transactions`/`Investigation` specs. Keep both stacks ≥90%.

---

## Track B — Go live locally (real Supabase Auth + Postgres + OpenRouter LLM)

**Model of "local live":** run with `FRAUDLENS_ENVIRONMENT=dev` (keeps local FS storage +
local job runner) but override the specific settings to hit real services —
`FRAUDLENS_AUTH_DEV_BYPASS=false` (forces real JWT), real `DATABASE_URL`, the Supabase
JWKS/issuer/audience, and `FRAUDLENS_LLM_MODE=live`. This avoids `config/prod.yaml`'s
Azure-only storage/queue backends while exercising the real auth + DB + LLM path.

### B1 — Supabase project setup (real project, shared local + prod)

- **Enable asymmetric JWT signing (RS256).** ⚠️ The backend only accepts **RS256 via
  JWKS** ([`auth_jwt_algorithm`](../backend/src/fraudlens_backend/settings.py)); the
  legacy HS256 shared secret won't work. Turn on Supabase's RSA JWT signing keys so
  `/auth/v1/.well-known/jwks.json` serves an RSA key.
- **Auth config:** enable email/password; **disable open signup** (admin-invite only).
- **Install a Custom Access Token hook** (`public.custom_access_token_hook`, tracked SQL
  under a new `supabase/` dir) that merges top-level **`agency_id`** and **`user_role`**
  claims from `public.users` at token issuance.
- **RLS** on `public.users`.

### B2 — Custom claims wiring ⚠️ role-claim collision

Supabase tokens carry a built-in top-level `role="authenticated"`, which collides with
our RBAC role. So:

- Set [`auth_role_claim`](../backend/src/fraudlens_backend/settings.py) to **`user_role`**
  (leave `auth_agency_claim="agency_id"`); the hook (B1) provides both top-level.
- Wire (env / Infisical, non-secret): `FRAUDLENS_AUTH_JWKS_URL =
  https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`,
  `FRAUDLENS_AUTH_JWT_ISSUER = https://<ref>.supabase.co/auth/v1`,
  `FRAUDLENS_AUTH_JWT_AUDIENCE = authenticated`.
- **Identity reconciliation:** provision `public.users.id = auth.users.id` so token `sub`
  == our `user_id` and `require_actor`
  ([`deps.py:366`](../backend/src/fraudlens_backend/api/deps.py)) resolves.

### B3 — Backend: `/me`, readiness, live LLM (OpenRouter)

- Add **`GET /api/v1/me`** → `{email, role, agencyId}` from verified claims + `users`
  row (the frontend's role source; no client-side JWT parsing).
- Add a **`/readyz` JWKS reachability probe** in
  [`ops.py`](../backend/src/fraudlens_backend/api/ops.py) (currently absent).
- **Live LLM:** in [`config/llm/sar.yml`](../config/llm/sar.yml) set primary
  `model: openrouter/openai/gpt-5-mini` and update `fallbacks` so it no longer duplicates
  the primary (keep `openrouter/google/gemini-2.0-flash-001`). This drops the
  Anthropic-key dependency — the only provider key needed is **`OPENROUTER_API_KEY`**
  (Infisical `/llm`, per [`docs/runbooks/infisical-secrets.md`](../docs/runbooks/infisical-secrets.md)).
  Run with `FRAUDLENS_LLM_MODE=live`. Governance already allows OpenRouter for
  `synthetic`/`deidentified` and SAR inputs are PHI-masked. RAG stays offline
  (HashingEmbedder — no embedding key needed); build the index once with `make ingest-rag`.

### B4 — Admin-invite provisioning + bootstrap

- **`POST /api/v1/users`** behind `AdminDep`: (1) create/invite the auth user by email via
  the Supabase Admin API (service-role key → **Infisical secret**); (2) insert a
  `public.users` row `{id=auth uid, agency_id=admin's agency, email, display_name, role}`.
  Non-admins 403.
- **Bootstrap the first admin** (chicken-and-egg): create one admin in the Supabase
  dashboard + insert its matching `public.users` row (extend
  [`scripts/seed.py`](../scripts/seed.py) to upsert the demo agency + this admin against
  the real DB). That admin then invites everyone else.
- Tests (mock Supabase Admin API + JWKS): admin invites; non-admin 403; invited user's
  token carries the right `agency_id`/`user_role`.

### B5 — Frontend: real login, refresh, 401, SSE

- Add `@supabase/supabase-js`; add `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`
  (publishable — safe in bundle) to [`config.ts`](../frontend/src/lib/config.ts) +
  `vite-env.d.ts`.
- [`Login.tsx`](../frontend/src/pages/Login.tsx) already has an email/password form —
  wire submit to `supabase.auth.signInWithPassword`; on success fetch `/api/v1/me` and
  call existing `signIn(email, remember, role, accessToken)`. `withSessionHeaders`
  already sends `Authorization: Bearer`.
- Token refresh via `supabase.auth.onAuthStateChange` (update stored token); on `401`
  attempt one silent refresh then retry, else `signOut()`. `signOut()` also calls
  `supabase.auth.signOut()`.
- **SSE auth:** switch the investigation stream to `fetch` + `ReadableStream` so it sends
  the `Authorization` header (EventSource can't) — no token in the URL.
- Gate the **demo-persona picker behind `import.meta.env.DEV`**; note personas only
  authenticate when the backend runs with the dev bypass (`make run`), while `make
  run-live` uses real login.

### B6 — Run it live locally (the near-term milestone)

- Add a **`make run-live`** target (sibling of `make run`) that wraps startup in
  `infisical run --env=prod --path=/` so **both** `/backend` (DATABASE_URL + Supabase
  service-role key) **and** `/llm` (`OPENROUTER_API_KEY`) are injected, and sets the
  overrides: `FRAUDLENS_ENVIRONMENT=dev`, `FRAUDLENS_AUTH_DEV_BYPASS=false`, the three
  `FRAUDLENS_AUTH_*` values, `FRAUDLENS_LLM_MODE=live`. Frontend gets `VITE_SUPABASE_URL`
  / anon key / `VITE_API_BASE_URL` via `frontend/.env.local`.
- Run migrations against Supabase Postgres (`DATABASE_URL=... make db-migrate`; use the
  **direct/non-pooled** connection for Alembic) and seed the bootstrap admin.
- Document the flow in a new/updated [`docs/runbooks/local-dev.md`](../docs/runbooks/local-dev.md)
  section "Running live locally."

### B7 — Inputs I'll need from you (gather before implementing Track B)

Secrets → **Infisical `prod`** at the path shown; non-secrets → env / `frontend/.env.local`:

- Supabase **project URL** `https://<ref>.supabase.co` (non-secret) and confirmation that
  **RSA JWT signing keys are enabled**.
- Supabase **anon/publishable key** (public → `frontend/.env.local`).
- Supabase **service-role key** (SECRET → Infisical `/backend`) — for admin-invite.
- Supabase **Postgres connection string** — pooled for the app + **direct** for
  migrations (SECRET → Infisical `/backend` as `DATABASE_URL`).
- **`OPENROUTER_API_KEY`** (SECRET → Infisical `/llm`, matching the existing convention).
- (Derived, non-secret) JWKS URL / issuer / audience per B2.

### B8 — Tests

Backend: verifier accepts a Supabase-shaped token with `user_role`/`agency_id`; `/me`
returns the role; invite admin-gated; `/readyz` reports JWKS up/down; prod-env test proves
the bypass is inert. Frontend: Login stores the token and sends `Authorization: Bearer`
(not the demo header); 401 → refresh → sign-out; persona picker hidden when
`!import.meta.env.DEV`.

---

## Track C — Azure deploy (LAST, after local live works)

No Azure account yet, so this stays scaffolded/inert (per AGENTS.md) until it exists.
When ready, follow [`docs/runbooks/azure-deploy.md`](../docs/runbooks/azure-deploy.md) and
[`plans/2026-07-06-azure-single-environment-deploy.md`](2026-07-06-azure-single-environment-deploy.md):
`config/prod.yaml` already flips `storage_backend=azure_blob`,
`queue_backend=container_apps_jobs`, `llm_mode=live`; secrets come from Infisical via
Azure managed identity; the same Supabase project + OpenRouter key from Track B are
reused. Only new work here is provisioning + wiring the Infisical→Azure runtime identity
and CORS origins — not app code.

---

## Verification

**Track A:** `uv run pytest -q --no-cov tests/integration/test_alerts_api.py`;
`npm run test`/`typecheck`/`lint`; preview each persona (analyst sees Comment / Send for
review / Dismiss, no Approve/Reject/Resolve; reviewer full toolset; auditor read-only).

**Track B (local live):**
- `make run-live` boots against real Supabase + OpenRouter.
- Real email/password login yields a token whose `agency_id`/`user_role` claims are
  stamped by the hook; requests carry `Authorization: Bearer`; SSE stream authenticates;
  forced token expiry triggers silent refresh.
- An admin invites a user who then signs in with the right role.
- A real investigation produces a **live** SAR draft via `openrouter/openai/gpt-5-mini`
  with recorded `token_usage`/`cost_usd`; `/readyz` shows DB + JWKS ok.
- `uv run pytest -q --no-cov` green (bypass inert in prod-env test).

**Both:** `make pre-pr` and `drift-check plans/2026-07-06-rbac-auth-live-llm.md all`.

## Out of scope
- SSO/OAuth providers (Supabase is the issuer; addable later without backend changes).
- JIT/self-service signup (chosen model is admin-invite only).
- A rules-management UI (backend already gated; no frontend surface exists).
- Provisioning the live **Azure** account (Track C stays inert until it exists).
