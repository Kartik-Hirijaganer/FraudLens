"""Summary: The OFFLINE research partitions the GFP tenant-isolation study splits IBM AML rows
across (`benchmark_gfp.py`). These are analysis partitions owned by the study, NOT runtime
tenants: FraudLens runs exactly one persistent demo agency (`config/portfolio-demo.yaml`), and
multi-tenancy is proven by tests that mint throwaway tenants. The names live here because the
committed study artifact (`frontend/src/data/gfp-tenant-isolation-study.json`) records them and
indexes its cross-tenant motifs by their position, so changing or reordering them invalidates a
published result.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The count (3) is what makes a CROSS-partition motif expressible at all; a single-partition
  study could not demonstrate the isolation gap the artifact exists to show (ADR-017).
- Order is part of the frozen artifact: index 0 is the primary partition the runtime demo
  agency mirrors via its configured `research_partition_key`.
"""

from __future__ import annotations

RESEARCH_PARTITIONS: tuple[str, ...] = (
    "Demo Financial Agency",
    "AML Demo Agency Two",
    "AML Demo Agency Three",
)
