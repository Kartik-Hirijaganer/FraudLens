---
name: maintain
description: Refresh code-file docs, the architecture doc, OpenAPI, and README, then propose the next release version + changelog from Conventional Commits. Use for "maintain docs", "update documentation", "refresh README/architecture", "prep a release", "what version should this be", "bump the version".
---

# Maintain

Bring the docs, architecture, API spec, README, and release metadata back in sync with the
code — **propose-only**. This skill regenerates the deterministic artifacts, rewrites the
hand-authored prose the generators cannot, and proposes the next SemVer + changelog. It
**never commits, tags, or pushes** (Golden Rule 1); it ends by showing a diff and the exact
tag command for the human.

## When To Use

When the user asks to update/refresh documentation, maintain `ARCHITECTURE.md`, regenerate
OpenAPI, update the README, or decide the next release version (feature vs bug → SemVer).

## Core Rules

- **Propose-only. Never `git add`/`commit`/`tag`/`push`** — no exceptions (Golden Rule 1).
  Make and describe edits to working-tree files; stop before recording history.
- **Reuse the deterministic engine; never hand-edit machine-owned regions.** `make docs`
  owns: the `Key classes` / `Key functions` inventory lines of every SUMMARY header, the
  OpenAPI files under `docs/reference/generated/api/`, the ERD under
  `.../generated/erd/`, and every `<!-- AUTOGEN:* -->` region of `ARCHITECTURE.md`. Run it;
  do not edit those by hand.
- **You own the prose.** The `Summary` / `Notes` sections of headers, function/class
  docstrings, the hand-authored narrative of `ARCHITECTURE.md` (outside AUTOGEN), and
  `README.md` are human-authored — that is what this skill rewrites with judgment.
- **FraudLens governance holds.** No PHI in any text or example; every tenant-scoped operation
  is described as scoped by `agency_id`; JWT `agency_id` is validated against the resource.
  Diagrams are Mermaid only (no binary images) — rule 6.
- **Version bumping is propose-only and conventional:** `feat` → minor, `fix`/`perf` →
  patch, `!` or `BREAKING CHANGE` → major. The human cuts the tag.

## Inputs

Parse the user's request after the skill name:

- (default) no scope → operate on files **changed vs `origin/main`** (the working set).
- `all` → operate on the whole repository (a full sweep).
- `base=<ref>` → compare against `<ref>` instead of `origin/main`.

## Workflow

1. **Scope.** Run `uv run python scripts/changed_files.py --category all --base <ref>`
   (default `origin/main`) to get the changed source files, or take the whole tree for
   `all`. List what you will review before editing.

2. **Regenerate deterministic docs.** Run `make docs`. This syncs header inventories,
   OpenAPI, the ERD, and the `ARCHITECTURE.md` AUTOGEN regions. Note what it changed.

3. **Refresh code-file documentation (judgment).** For each changed source file:
   - Update the SUMMARY header's `Summary` and `Notes` so they describe what the file does
     *now* (do not touch the `Key classes`/`Key functions` inventory — step 2 owns it).
   - Update function/class docstrings that drifted from the implementation. Keep
     `interrogate` docstring coverage ≥90%; keep the four header sections in order.

4. **Maintain the architecture doc.** If the change altered components, flows, boundaries,
   layering, or governance mapping, update the matching **hand-authored** prose/diagrams of
   `docs/architecture/ARCHITECTURE.md` (C4 context/containers/components, the pipeline
   sequence, the FraudLens mapping table). Leave AUTOGEN regions to `make docs`.

5. **OpenAPI.** Already regenerated in step 2. Confirm `docs/reference/generated/api/`
   reflects the current routes; call out any new/changed/removed endpoint.

6. **README.** Update `README.md` so it states what the project is, how to set up
   (`make install`), run (`make dev`), test/gate (`make pre-pr`), and where the docs live —
   reflecting the current code. Match `DESIGN.md` only if adding UI snippets.

7. **Release version + changelog (propose-only).**
   - Run `make version-next` → read the JSON: the feature/bug/breaking classification and
     the proposed `next_version`. Surface its `notes` (e.g. the 0.x breaking-change caveat).
   - Run `make changelog-unreleased` → the pending changelog section for that version.
   - If asked to *stage* it, you may update the `## [Unreleased]` block of `CHANGELOG.md`
     with that content — but still **do not tag or commit**.

8. **Validate.** Run `make docs-check` (must pass) and, when source changed, the relevant
   read-only gates (`make header-check`, `make lint-changed`, `make typecheck`). Fix any
   prose you introduced that breaks them.

9. **Report and stop.** Output the sections below, run `git status --short` +
   `git diff --stat`, and print the exact release command for the human — then **stop**:

   ```
   git tag v<next_version> && git push origin v<next_version>   # human runs this
   ```

## Output Format

### 1. Scope
Files reviewed (changed-vs-base or full sweep) and the comparison base.

### 2. Generated (deterministic)
What `make docs` regenerated (headers/OpenAPI/ERD/architecture AUTOGEN), with paths.

### 3. Prose updated (judgment)
Per file: header Summary/Notes, docstrings, architecture narrative, README — with a
one-line reason each.

### 4. Release proposal
- Classification: N feat, N fix, N breaking (cite commit subjects from `version-next`).
- Current → **proposed** version, and the bump level, with any `notes`.
- The pending changelog section (from `changelog-unreleased`).

### 5. Verification
`make docs-check` result and any other gates run, read-only.

### 6. Next step (human)
The `git tag …` command. State plainly that nothing was committed, tagged, or pushed.

## Failure Modes To Avoid

- Editing a machine-owned region (header inventories, AUTOGEN, generated OpenAPI/ERD) by
  hand instead of running `make docs`.
- Committing, tagging, or pushing — ever.
- Writing prose that breaks `docs-check`, `header-check`, or `interrogate`.
- Inventing endpoints/config keys; the generated tables are the source of truth.
- Putting PHI or secrets in any doc, example, or changelog entry.
