# Transactions page redesign

Redesign the analyst **Transactions** page to match the dashboard chrome shown in the
UI/UX reference: a clean search + risk-filter card over a scannable table, real
server-backed pagination, friendlier copy, and a design-system-native import control.
Keep the CSV upload (still needed) and unify the model-label rendering so every page
reads the model version the same way.

## Phase 1 — Shared primitives

- Extend `ui/Pagination` with a Prev/Next mode (`rangeStart`/`rangeEnd`/`total`,
  `onPrev`/`onNext`, `hasPrev`/`hasNext`) while keeping its existing shown-of-total +
  load-more modes (backward compatible; the component was previously unused).
- `ModelSelector` renders every version label through `formatModelVersion` (drops the
  internal `-fixture` tag) so the Transactions selector and the Dashboard "Active model"
  card show the SAME version string (single source of truth). The submitted
  `modelOverride` value stays the raw registry label.

## Phase 2 — Transactions page

- `PageHeader` with friendly copy ("Every transaction is scored the moment it lands.
  Search, filter, and open one to investigate.") and an **Import CSV** action styled as a
  design-system button (label-wrapped hidden file input; stays keyboard/AT accessible).
- Card interior: a pill search input with a leading search icon, the shared risk
  `SegmentedControl`, and a compact `Score with model` selector.
- Table columns: **TXN ID · Amount · Risk · Counterparty · Time** plus a chevron affordance
  per row. The whole row (and the chevron) starts an investigation and deep-links to the
  live run; the in-flight row is guarded against double-submit.
- **Server keyset pagination**: a cursor stack drives Prev/Next (page size 10); the
  "Showing X–Y of Z" total comes from the dashboard-metrics aggregate
  (`transactions.total`, or `byRiskBand[band]` when a risk filter is active) — the same
  source the Dashboard reads, so the counts never diverge.
- Keep the client-side search box as a convenience refinement of the current page.

## Phase 3 — Tests + validation

- Update `Transactions.test.tsx` for the new page size, cursor param, import label,
  investigate affordance, and pagination controls; add `Pagination` Prev/Next tests.
- Run `npm run test`, `npm run lint`, `tsc --noEmit`, and visually verify in the preview.
