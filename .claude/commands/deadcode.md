---
description: Run the dead-code sweep (make deadcode) and summarize findings.
---

Run the dead-code sweep and report findings (advisory by default).

1. Run `make deadcode` (vulture + ruff unused-symbol codes + knip). It is **warn-only**
   by default; run `DEADCODE_STRICT=1 make deadcode` to treat findings as failures.
2. Summarize each finding and whether it is a true positive or a framework false
   positive (FastAPI route handlers / Pydantic models can look unused).
3. For true positives, propose the removal — but **do not commit**; per Golden Rule 1,
   wait for explicit human permission before changing history.
