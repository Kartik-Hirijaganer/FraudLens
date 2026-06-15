# FinCEN / BSA regulatory corpus (RAG source)

This directory is the **committed, diff-friendly source corpus** the FinCEN/BSA RAG index
is built from (plan §16 Phase 6). `make ingest-rag` (`scripts/ingest_rag.py`) loads every
`*.md` document here, chunks it deterministically, embeds the chunks, and persists a
ChromaDB index that the investigation pipeline retrieves cited regulations from.

## Why curated text (not raw PDFs)

The authoritative regulations are published as large PDFs. We commit **curated excerpts**
as text/markdown instead of binary PDFs so the corpus is **diff-reviewable, deterministic,
license-clean, and small** (plan §16 Phase 6 risk note: "curate corpus", "parse once").
Each excerpt is a faithful paraphrase/quote of the cited provision sufficient to ground a
Suspicious Activity Report (SAR) draft — it is **reference material, never legal advice**,
and contains **no PHI**.

## Document format

Each `*.md` file is one regulatory provision with a small metadata header followed by the
body text:

```
---
docId: bsa-structuring
title: Structuring transactions to evade reporting requirements
citation: 31 CFR 1010.314
source: FinCEN / Bank Secrecy Act
---

<body text — one provision, a few short paragraphs>
```

`docId` is a stable kebab-case id (also the chunk-id prefix), `citation` is the exact
regulatory cite surfaced to analysts, and `source` is the publisher. Files are loaded in
sorted filename order so the build is reproducible.

## Provenance

Excerpts are drawn from the public U.S. Code (31 U.S.C.) and the Code of Federal
Regulations (31 CFR Chapter X), which are U.S. government works in the public domain.
