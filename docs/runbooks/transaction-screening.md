# CSV screening and saved investigations

CSV upload accepts valid new rows and queues their investigations atomically. Closing the browser
or restarting the API does not remove accepted work. Single-record and JSON batch ingestion retain
their existing behavior; signing in never schedules analysis or backfills old transactions.

## User workflow

1. Import a synthetic CSV from Transactions. The acknowledgement distinguishes accepted,
   duplicate and rejected rows from background screening completion.
2. Watch queued/running/retrying/completed/failed counts. The transaction list, Dashboard and Alerts
   refresh from persisted results while screening is active. Filters and paging remain intact.
3. Open **View progress** for active work, **View results** for saved analysis, or **View failed run**
   for a failure. These actions observe existing runs and never POST another investigation.
4. Use **Re-run** explicitly after the current run is terminal. Analysts and reviewers use normal
   active deployment routing. Only admins can select a model for the next investigation. Selection
   itself does not score anything; previous runs remain addressable at their original URLs.
5. Auditors can open saved evidence but cannot upload, investigate or rerun.

The deterministic rules → XGBoost → SHAP pipeline persists scores and raises threshold-crossing
alerts before optional regulatory retrieval and SAR drafting. Below-threshold rows finish without
an alert or SAR. A drafting refusal or provider failure leaves the risk analysis and alert usable.
Only an exact copy of the backend-bundled demo CSV receives trusted `synthetic-generator` provenance
at ingestion. In explicitly enabled live mode, flagged trusted rows use the existing LLM drafter and
its factual/citation checks. With providers disabled, they use the labeled mock. Other CSV/API uploads
remain ineligible for external AI and use an evidence-based **Template-generated draft** with the same
deterministic quality gate. All CSV jobs use a single writer and local regulatory retrieval.
The UI shows the actual mode and restriction. Missing or tampered citations fail explicitly; risk
results remain available. A draft is never an approval or regulatory filing.

## CSV contract and validation report

Choose **Download synthetic demo CSV** in Transactions. The authenticated
`GET /api/v1/transactions/template` API returns the
[backend-owned fixture](../../config/demo-transactions.csv); save and upload it unchanged to retain
synthetic trust. Even newline or column-order changes select local processing. This exact-file check
establishes the origin of the known demo content, not anonymity of arbitrary uploads.
Send raw `text/csv`, strictly encoded UTF-8 (a UTF-8 BOM is accepted), with each header exactly once:

`externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country`

Header order may vary. No notes, source declarations, manifests or additional feature columns are
accepted. JSON single/batch ingestion uses the same fact checks; optional `features` must be `{}`.
The scorer derives numeric features from validated transaction facts and tenant-scoped history.

| Check | Contract |
|---|---|
| Request | At most 5,242,880 bytes, checked on each received chunk; no original CSV is stored. |
| CSV structure | At most 10,000 data rows, eight columns and 128 decoded characters per field; valid quoting and exact row widths. |
| File failure | Bad encoding, headers, quoting, widths or limits reject the entire file before ingestion. |
| Row failure | Bad facts reject that row; valid rows and their screening work commit atomically. |
| Amount | Positive finite exact cents, below 10^16; excess precision is rejected, never rounded. |
| Time | ISO-8601 with explicit timezone/offset, not future; no assumed timezone. |
| Identifiers | 1–128 ASCII characters; start alphanumeric, followed by alphanumeric, `_`, `.`, `:`, `-`. No surrounding spaces. |
| Codes | Supported ISO subsets in [shared validation](../../packages/fraudlens-core/src/fraudlens_core/upload_validation.py); case is normalized. Unsupported codes are rejected. |
| Channel | Exactly `wire`, `card`, `ach`, `swift`, `crypto`, `cash`, or `other`. |
| Report | Accepted/duplicate/rejected counts, `trustedSynthetic`, planned `draftingMode` (`ai`, `mock`, `template`), and bounded value-free `sampleErrors` with physical `rowNumber`. This reports eligibility for newly accepted flagged rows, not a completed draft or a quality verdict. |

Uploaded external ids become opaque tenant-specific `upload-…` references. Both account displays are
withheld; full tenant-scoped account digests preserve history without last-four collisions. Digests
are pseudonyms, not anonymity proof. Use synthetic data only. Historical rows without full account
keys have unavailable linkage; saved evidence is retained and no provenance is inferred or backfilled.

## Analyst walkthrough

1. Download and import the unchanged synthetic demo CSV, then read the trust and validation report.
   Duplicate/rejected rows create no
   additional screening work. Fix rows using line numbers and safe reasons in the bounded sample.
2. Watch automatic screening. Threshold-crossing transactions create saved investigations and alerts;
   the supplied fixture is an example, not a guarantee that the active model will flag every row.
3. Open saved results and inspect rules, scores, SHAP and verified regulatory citations. This is an
   observation action. Choose **Re-run** explicitly only when another scored version is intended.
4. Read the actual draft mode: AI when enabled and trusted, mock when trusted with providers disabled,
   or **Template-generated draft** for other files. Ownership, identity and
   purpose remain unknown until a human verifies them. If evidence is missing, inspect the explicit
   drafting failure; scores and alerts remain saved.
5. A reviewer/admin may edit the narrative (another version; automatic quality resets to `not_run`)
   and explicitly approve/reject it. Approval is internal and does not file with a regulator. The
   generation mode remains visible on edited versions. Analysts/auditors cannot approve.

Local template generation requires the baked regulation index (`make ingest-rag` in an explicitly
local/offline configuration). It uses no provider, agent or embedding calls and zero provider tokens.
Regeneration uses saved retrieval evidence, without re-scoring or retrieving again. Ordinary uploads
always retain `api-upload`; validation, masking, filename and caller provenance cannot enable AI.
An exact trusted replay deduplicates normally and creates no provider work. Existing duplicates retain
their original facts and source; downloading the demo never upgrades a historical upload. There is no
signing service, manifest, new table or secret. [ADR-033](../architecture/adr/ADR-033-bundled-synthetic-csv-provenance.md)
records this fixed-fixture scope. Live calls remain disabled until explicitly authorized; real data
handling and regulatory submission are outside this personal-project workflow.

## Runtime and recovery

- Worker mode: use the existing `make worker` process with the same settings/database as the API.
- Inline mode: API lifespan supervises an upload-only durable worker automatically.
- Production worker liveness writes to `/tmp/fraudlens-worker/heartbeat`, outside the non-root
  image's application directory. Kubernetes overrides this with its writable volume path.
- `batch_score_limit` is the upload enqueue chunk size; all chunks are accepted. `run_claim_batch`
  limits a scheduling pass, and subsequent passes drain remaining work.
- Automatic execution deadlines start on first claim and are retained across retries. Lease expiry,
  retry backoff, maximum attempts and terminal events follow the durable-worker contract.
- Read `GET /api/v1/transactions/screening` for tenant counts, or supply `jobId` from the CSV response
  for that upload. Another tenant's job is indistinguishable from a missing job.
- If progress stops, check worker liveness and database availability. Read-only retries/focus checks
  reconnect observation; they do not enqueue replacement work. Terminal core failure requires an
  authorized, explicit rerun.

## Local verification

Use synthetic fixtures and mocked providers. Run `make pre-pr` and `make test-coverage-diff`.
For locking evidence, point both database variables at an isolated local PostgreSQL database and
run `make db-migrate postgres-run-test`. Never point these mutating migration/test commands at
Supabase or another live database. The tests do not authorize cloud jobs or paid provider calls.

The completed local check results and scope are recorded in the
[upload verification report](../reference/upload-validation-verification.md).
