---
name: design-review
description: Review FraudLens frontend changes against DESIGN.md, the Wise token system, accessibility requirements, responsive behavior, and understandable investigation-state communication.
---

# Design Review

Audit frontend work for visual-system fidelity and operational clarity.

## When To Use

Use before or after a FraudLens UI change, especially investigation states, case review, research
pages, responsive layouts, or accessibility-sensitive interactions.

## Rules

- Read `DESIGN.md` before reviewing or editing UI; it is the canonical Wise design contract.
- Use configured Tailwind theme tokens only: no ad hoc colors, pixel sizes, radii, or spacing.
- Reserve Wise green for the primary CTA, not success status; use semantic status tokens.
- Review accessibility and state comprehension as functional requirements, not polish.
- Preserve the change's intended user-visible behavior and the API's error/state vocabulary.

## Steps

1. Identify the affected routes, components, states, breakpoints, and acceptance criteria.
2. Compare type scale, weight, spacing, radius, color, borders, cards, controls, and motion directly
   with `DESIGN.md` and existing shared components.
3. Verify keyboard order, visible focus, semantic HTML, labels, names/roles/values, headings, live
   regions, contrast, reduced motion, and text alternatives for charts.
4. Exercise desktop and 400-pixel layouts with loading, empty, success, retrying, blocked, failed,
   and long-content cases relevant to the flow.
5. Confirm investigation state labels explain what is happening, what remains available, whether a
   retry is automatic, and what action the analyst can take. Keep completed risk analysis visible
   when drafting is blocked or fails.
6. Run frontend lint, typecheck, tests, and the planned browser verification; report findings by
   severity with file/component evidence.

## Verification

- Every style maps to a design token or an existing shared component.
- The primary CTA is unique, status colors are semantic, and cards/buttons use the prescribed scale.
- The flow works by keyboard and screen-reader semantics and has no critical narrow-screen clipping.
- Charts have readable table alternatives and state changes are not communicated by color alone.
- Investigation and quality states remain understandable without internal implementation jargon.

## Never Do

- Never re-theme the application or rerun the design importer during an ordinary review.
- Never introduce a one-off visual primitive when a shared component exists.
- Never hide completed risk evidence because downstream drafting failed.
- Never approve inaccessible controls, unlabeled icon actions, or color-only state communication.
