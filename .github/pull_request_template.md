## Summary

<!-- PR-SUMMARY:auto -->
_Auto-filled when the PR opens with the areas this change touches._
<!-- /PR-SUMMARY:auto -->

## What & why

<!-- One or two lines: what changed and why. Link the plan if there is one. -->

---

<!-- CI enforces the full gate automatically (lint · types · ≥90% coverage · headers ·
     secrets · docs · image build) — no manual checkboxes for any of it. The one box below
     is the FraudLens security check CI can't verify; keep it. -->

- [ ] **FraudLens governance:** no PHI in logs/errors/URLs; tenant ops scoped by `agency_id`; authz validates the JWT claim (fails closed)
