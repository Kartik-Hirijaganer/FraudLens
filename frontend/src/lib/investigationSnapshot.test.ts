import { expect, it } from "vitest";

import { snapshot } from "../test/factories";
import { initialInvestigationState, investigationStateFromSnapshot } from "./investigation";

it.each([
  ["egress_source_not_allowed", /unavailable for uploaded transactions/i],
  ["llm_budget_exceeded", /drafting limit was reached/i],
] as const)("explains %s while preserving saved risk evidence", (sarErrorCode, reason) => {
  const saved = snapshot({ status: "completed", sarStatus: "failed", sarErrorCode });
  const state = investigationStateFromSnapshot(saved, initialInvestigationState());
  expect(state.status).toBe("drafting-blocked");
  expect(state.draftingBlockReason).toMatch(reason);
  expect(state.riskScore).toBe(saved.riskScore);
  expect(state.topFeatures).toEqual(saved.topFeatures);
});
