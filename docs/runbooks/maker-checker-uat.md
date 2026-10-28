# Maker–checker local UAT

The case workflow owns preparation and decisions. Investigation shows machine evidence and reuses
the case narrative editor for inline working-draft changes. Submission and decisions use the case
workspace. All data in this procedure is synthetic; all services run on loopback interfaces.

## Release cutover prerequisites

The API and schema change together: deploy the backend and frontend in one controlled release
window after the integration branch passes CI and UAT. Before production promotion:

1. Pause or pin the scheduled portfolio reset to the currently deployed version. The new bootstrap
   writes workflow states that the previous backend cannot read safely.
2. Record the current backend image, frontend deployment and database recovery point. Verify the
   migration and recovery procedure against a disposable database first.
3. Apply migration `0016_maker_checker` and provision trusted account-to-person links. Unmapped
   accounts fail closed; role selection alone cannot establish a person identity.
4. Promote matching backend/frontend versions, then verify health/readiness, tenant-scoped pickup,
   submission, self-decision denial, independent decision and approved PDF retrieval.
5. Run the synthetic reset only with matching deployed code, verify its expected baseline, then
   resume its schedule. Verify backend traffic and retire idle active revisions.

A rollback after new workflow writes needs a compatible database recovery strategy as well as the
previous application versions. Do not run an automatic schema downgrade over newly submitted cases.
This local UAT procedure does not authorize a production deployment or reset.

## Start a reproducible baseline

Requirements: installed repository dependencies (`uv sync --all-packages`, `npm ci --prefix frontend`)
and Docker. Run from the repository root. These commands create only a local disposable PostgreSQL
container; its trust authentication is restricted to loopback and must never be used for deployment.

```sh
docker run -d --name fraudlens-maker-checker-pg \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=fraudlens \
  -e POSTGRES_DB=maker_checker_ready -p 127.0.0.1:55441:5432 postgres:16-alpine
```

If that container already exists, start it instead with `docker start fraudlens-maker-checker-pg`.
For the implementation session it already contains `maker_checker_ready` and verification databases.
Wait for `docker exec fraudlens-maker-checker-pg pg_isready -U fraudlens` to report ready.

Set the following **non-secret local configuration** in each backend terminal:

```sh
export DATABASE_URL=postgresql+asyncpg://fraudlens@127.0.0.1:55441/maker_checker_ready
export FRAUDLENS_ENVIRONMENT=dev
export FRAUDLENS_AUTH_DEV_BYPASS=true
export FRAUDLENS_PORTFOLIO_DEMO_ENABLED=true
export FRAUDLENS_LLM_MODE=mock
export FRAUDLENS_RAG_EMBEDDING_MODE=offline
export FRAUDLENS_RAG_INDEX_DIR=.local/maker-checker/chroma
export FRAUDLENS_STORAGE_BACKEND=local
export FRAUDLENS_STORAGE_LOCAL_DIR=.local/maker-checker/artifacts
export FRAUDLENS_CORS_ALLOW_ORIGINS='["http://127.0.0.1:15180"]'
make db-migrate db-seed ingest-rag portfolio-demo-reset portfolio-demo-verify
uv run uvicorn fraudlens_backend.main:app --host 127.0.0.1 --port 18100
```

In a second terminal:

```sh
VITE_API_BASE_URL=http://127.0.0.1:18100 VITE_AUTH_DEV_BYPASS=true \
  VITE_DEMO_AUTH_ENABLED=false npm --prefix frontend run dev -- \
  --host 127.0.0.1 --port 15180 --strictPort
```

Open [the local app](http://127.0.0.1:15180). Select a configured persona and click **Sign in**.
This explicitly enabled local bypass needs no password or external provider credential. Production
ignores persona headers and uses verified Supabase authentication. No secret belongs in this runbook
or a local `.env` file. Use **Sign out** before switching personas.

## Four roles, four demo logins

The picker exposes only the existing Analyst, Reviewer, Admin and Auditor accounts. Account aliases
and additional reviewers exist only as automated-test fixtures. The authoritative names, emails,
account ids and person ids live in [`config/portfolio-demo.yaml`](../../config/portfolio-demo.yaml).

| Persona key | Existing role | Use |
|---|---|---|
| `analyst` | Analyst | Take, investigate, author and submit; dismiss only when current flags allow. |
| `reviewer` | Reviewer | Prepare a case or independently decide someone else's submission. |
| `admin` | Admin | Administrative tools and independent review; no self-decision override. |
| `auditor` | Auditor | Read evidence, rounds, reasons and approved PDFs. |

## Inline SAR editing

On Investigation's **SAR draft** step, click **Take case to edit** if the case is available,
then **Edit SAR**. Click the narrative field and type. **Save changes** creates a version through
the existing case command; **Cancel** discards the unsaved edit. The wizard's forward/back controls
are disabled while changes are unsaved. Save errors preserve the typed text. The single green
**Continue to case** flow leads to submission and checking; the duplicate preparation shortcut is
removed. **Reload SAR** refreshes the saved narrative and editing eligibility when no edit is dirty.

Only the owning person may edit. Submitted reports stay locked until an independent checker returns
them. Approved/rejected reports remain read only; use a working case from **Returned** or
**Available** to test editing instead of the approved baseline report.

## Baseline and walkthrough

The configured story contains 20 transactions, 15 scored runs and five cases. Its latest artifacts
are two working drafts, one submitted SAR, one approved SAR and one declined SAR. Scoring assertions
and rule-hit expectations remain pinned to the existing model.

1. **Analyst → independent approval:** choose **Available**, open the case and **Take case**.
   Read risk, drivers and citations. Edit **Working SAR narrative**, save, add a rationale and
   **Send for review**. Reload: the narrative is frozen. Sign in as Reviewer, choose **Awaiting my
   review**, open the round, enter a decision reason and a compatible outcome, and approve. The
   case closes with one label and one decision. Download the PDF when ready.
2. **Reviewer as maker:** repeat with Reviewer taking a fresh case. Their own account cannot
   decide it; the independent Admin can. Use a synthetic baseline reset between
   walkthroughs when a fresh available case is needed.
3. **Self-decision protection:** after Reviewer submits, their own account cannot approve, return
   or decline it. A changed role also cannot erase participation. Linked-account denials are covered
   by isolated automated fixtures, without adding extra logins to the portfolio demo.
4. **Return → revise → resubmit:** the baseline **Returned** queue already has a returned round.
   Inspect its stored reason and original frozen narrative. Revise and save the working version,
   add a rationale and resubmit. The next round has a different id and version. The returning
   checker remains eligible because returning/commenting did not prepare the SAR.
5. **Decline/no-SAR:** choose **Recommend no SAR**, supply a rationale and submit. An independent
   checker can return it or decline with a benign/false-positive outcome. Approval is unavailable
   without a submitted usable SAR. Declined cases expose no approved PDF.
6. **Dismissal:** on the available high-risk case, pickup and add a rationale. With no current
   flags, dismissal closes it as false positive and retains its draft as withdrawn. The critical
   baseline case continues to require independent review. Recovering a failed SAR clears only its
   availability flag, not critical risk or low confidence that still exists.
7. **PDF recovery:** an approved baseline artifact may start pending. As an independent Reviewer,
   use **Retry PDF**, then **Reload case**. Pending/failed readiness does not undo approval.
8. **Stale tabs:** keep a case open in two tabs, mutate one and try the other. Expect a conflict
   directing you to reload; no partial version, submission or decision is created.

Use **All cases** for a complete audit view. Available/My cases/Awaiting my review/Returned queues
are filtered by the backend using canonical ownership and independence. Frozen round details include
rationale, maker/submitting accounts, person identities, evidence, checker reason and final outcome.

## API and database proof

Use the browser's network panel to retain the actual response and request id for a walkthrough.
The local-only `X-FraudLens-Demo-Role` header selects an existing configured role; no tenant/person header
can establish identity. Every command rechecks authorization in the backend.

| Command | Required fields |
|---|---|
| `POST /api/v1/alerts/{id}/actions` | `action`: assign/comment/dismiss, `expectedCaseVersion`; nonblank `note` for comment/dismiss. |
| `PATCH /api/v1/alerts/{id}/sar` | `expectedCaseVersion`, `expectedSarVersion` (null only for first authoring), `content`. |
| `POST /api/v1/alerts/{id}/submissions` | `Idempotency-Key`, expected case version, rationale, `recommendation`: sar/no_sar, selected `sarVersion` only for sar. |
| `POST /api/v1/alerts/{id}/submissions/{round}/decision` | `Idempotency-Key`, expected case version, decision, reason, compatible terminal outcome. |
| `GET …/submissions/{round}/pdf` | Authenticated tenant access to an approved ready artifact. |
| `POST …/submissions/{round}/pdf/retry` | Trusted mapped Reviewer/Admin; idempotent artifact recovery. |

The old `/sar/review` route returns 404. Resolve/escalate action bodies return validation errors.
No checker decision accepts inline narrative editing. Identical keyed retries return the original
recorded result; a reused key with different content or actor returns a conflict.

For tenant-scoped database assertions, set the agency id from the configuration and inspect:

```sql
SELECT a.id, a.status, a.case_version, a.assigned_to, a.owner_person_id,
       s.id AS submission_id, s.round, s.sar_version, s.pdf_status,
       d.decision, d.outcome, reason.note AS decision_reason
FROM alerts a
LEFT JOIN case_submissions s ON s.agency_id = a.agency_id AND s.alert_id = a.id
LEFT JOIN case_decisions d ON d.agency_id = s.agency_id AND d.submission_id = s.id
LEFT JOIN alert_actions reason ON reason.agency_id = d.agency_id AND reason.id = d.action_id
WHERE a.agency_id = :'agency_id'
ORDER BY a.id, s.round;
```

Compare `training_labels` under the same `agency_id` and case `run_id`: one terminal outcome,
no label for a return, and no duplicates after retries. Reasons occur in masked action notes, while
audit metadata holds only identifiers/transitions and masking counts.

## Verify, reset and stop

```sh
make portfolio-demo-verify
make docs pre-pr
```

For PostgreSQL evidence, use a **separate local disposable test database**, migrate it, then run
`POSTGRES_TEST_DATABASE_URL=<local-test-url> make postgres-run-test`. The migration round-trip test
creates its own temporary database on that local host and drops it in `finally`. Never point these
mutating tests at the UAT database or a cloud database.

`make portfolio-demo-reset` rebuilds only the configured synthetic tenant's operational rows. It
preserves identity mappings, audit logs, foundational configuration, models and unrelated tenants.
Reset is intended for local UAT here; production reset is not authorized by this procedure.

Stop both servers with Ctrl-C. `docker stop fraudlens-maker-checker-pg` preserves local data.
When the entire disposable environment is no longer needed, `docker rm -f
fraudlens-maker-checker-pg` removes its container. Verify with `docker ps -a --filter
name=fraudlens-maker-checker-pg`; it should return no matching container. No cloud teardown is needed.
