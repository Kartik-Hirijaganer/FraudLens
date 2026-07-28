# Portfolio demo — user acceptance checklist

> Run this yourself, after the agent's browser walkthrough, to confirm every claim independently.
> It is self-contained: **no number is written down here.** The expected values are printed by
> `make portfolio-demo-verify`, which reads them from
> [`config/portfolio-demo.yaml`](../../config/portfolio-demo.yaml) — so this checklist cannot drift
> away from the story the way a doc with counts baked into its prose would.
>
> Background: [portfolio-demo.md](portfolio-demo.md) (where each value lives, what is safe to
> change) · [ADR-018](../architecture/adr/ADR-018-portfolio-demo-data-provenance.md) (why the state
> is pipeline-produced and asserted rather than inserted).

## 0 · Machine check (30 seconds, no code reading)

Terminal 1 — leave this running; it prints the app URL **and** the API URL, and they are not always
`:5173`/`:8000` (the runner picks free fallbacks when those ports are taken):

```bash
make run-live-demo
```

Terminal 2 — the `portfolio-demo-*` targets read `DATABASE_URL` from the environment like `db-seed`
does, so wrap them the same way you wrapped `db-migrate`. Without it they exit with
`DATABASE_URL is not configured`:

```bash
infisical run --env=prod --path=/ --recursive -- make portfolio-demo-verify
```

- [ ] Every row reads **PASS** and the command exits `0`.

Keep that table open — it is the source of the numbers you are about to check on screen. If any row
reads FAIL, stop here: the demo is not in its pinned state, and the rest of the checklist would be
confirming the wrong thing. `make portfolio-demo-reset` rebuilds the baseline.

Optionally, run the API-level smoke suite against the same URL:

```bash
make portfolio-demo-smoke SMOKE_BASE_URL=<the printed API URL>
```

## 1 · Manual walkthrough

Open the printed URL. One box per screen; each asks you to compare what you see against the
`portfolio-demo-verify` table or the login picker, never against a number in this document.

### Login

- [ ] The persona picker lists personas with display names and short tags — no persona is missing,
      and none appears that the picker did not list.
- [ ] Clicking a persona fills in its email **and** password for you.
      **Do not type the password by hand.** If nothing is auto-filled, that is a defect — record it
      and stop; the credential is meant to arrive from the backend projection.
- [ ] Submitting signs you in and lands on the Dashboard.

### Dashboard

- [ ] The four KPI cards (open alerts, in review, SARs approved, active model) match the
      corresponding rows of the verify table.
- [ ] The risk-band bar below them shows five chips — low, medium, high, critical, and unscored —
      each with a count matching the verify table's per-band rows.
- [ ] The unscored chip is **not** a link (the API cannot filter on it); the four band chips are.

### Transactions

- [ ] Clicking the **high** chip navigates to Transactions with `?riskBand=high` in the URL, and the
      list total equals the high count in the verify table.
- [ ] Repeat for low, medium, and critical. Each lands on real rows, not an empty list.
- [ ] Account identifiers are masked — you see a short suffix, never a full account number.

### Live investigation

- [ ] Open one of the **unscored** transactions and start an investigation.
- [ ] Progress events stream in and the run reaches a terminal state (it does not hang).
- [ ] That row's band changes from unscored to a real band.

> This deliberately mutates state. `make portfolio-demo-verify` will now report a delta — that is
> correct behavior, not a bug, and the last step of this checklist restores the baseline.

### Alerts

- [ ] The alert queue shows the configured open / in review / resolved split from the verify table.
- [ ] The **resolved** alert shows an action trail with a synthetic review note; the states in the
      trail connect (each step starts where the previous one ended).
- [ ] The **in review** alert names who it is assigned to.
- [ ] Each alert's severity matches the band of the transaction it came from.

### SAR review

- [ ] The approved SAR renders its narrative and citations.
- [ ] A rejected SAR shows as rejected.
- [ ] The draft / approved / rejected counts match the verify table.

### Model Admin

- [ ] The active model version equals the label in the verify table's model row.
- [ ] No canary rollout is live, and the drift advisory is empty.

### Research

- [ ] The research page renders the graph-feature study, and its cross-tenant motif still shows the
      edges disappearing under the per-tenant view.
- [ ] Nothing on this page claims those partitions are live tenants — they are offline analysis
      partitions ([ADR-017](../architecture/adr/ADR-017-graph-feature-serving-boundary.md)).

### Presentation

- [ ] Narrow the window to a phone width: no horizontal scrolling of the page body.
- [ ] Switch the OS/browser to dark mode: the band chips and status colours stay legible, and Wise
      green appears only on the primary call-to-action — never as a status colour
      ([`DESIGN.md`](../../DESIGN.md)).

## 2 · Prove the reset (do this last)

Each command below needs the same `infisical run --env=prod --path=/ --recursive --` prefix as step 0.

- [ ] Change one alert by hand — assign it, or resolve an open one.
- [ ] Run `make portfolio-demo-verify`. It **fails**, naming the row that moved. (If it passes, the
      verification is not actually checking anything — record that as a defect.)
- [ ] Run `make portfolio-demo-reset`.
- [ ] Run `make portfolio-demo-verify` again. Every row reads PASS.

That last sequence is the whole guarantee in miniature: the demo's state is produced by the real
pipeline, compared against configuration, and restorable to a pinned baseline — so a visitor can
break it freely and the next visitor still sees the intended story.

## If something fails

Record it verbatim — a red step is the point of the exercise, not something to smooth over.

| Symptom | Likely cause | Where to look |
|---|---|---|
| `portfolio-demo-verify` fails immediately after `run-live-demo` | The bootstrap refused (foreign rows in the tenant, wrong active model, unreachable bundle, or a provider-mode mismatch) | The refusal line printed by `make run-live-demo`; [portfolio-demo.md](portfolio-demo.md) |
| Bands or alert counts differ but nothing was changed by hand | A rule parameter, band bound, blend weight, or the active model moved | `make portfolio-demo-probe` reports the resolved policy and per-row calibration |
| The picker renders no personas | The projection route is disabled or unreachable | `GET /api/v1/portfolio-demo/config` — it 404s unless the demo (or the non-prod dev bypass) is enabled |
| The password is not auto-filled | `FRAUDLENS_DEMO_AUTH_PASSWORD` is not injected | Infisical `prod` at `/`; the projection returns an empty password rather than failing the screen |
| Investigation never reaches a terminal state | The RAG index or the SAR drafter mode does not match the story's `execution:` block | `make ingest-rag-live`, then re-run `make run-live-demo` |
