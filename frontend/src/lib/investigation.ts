/**
 * Summary: Stable investigation barrel plus render-ready machine-progress timeline assembly.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - investigationTimeline: build the single- or multi-agent progress timeline.
 *
 * Notes:
 * - Existing investigation imports remain valid through the barrel exports.
 */
import type {
  AgentRun,
  AgentTimelineRow,
  AgentTimelineStatus,
  InvestigationState,
} from "./investigationTypes";

export * from "./investigationTypes";
export * from "./investigationReducer";

const AGENT_PURPOSES: Record<string, string> = {
  evidence_investigator: "Collect the governed transaction, rule, model, and alert evidence.",
  regulatory_analyst: "Match the case evidence to applicable regulatory provisions.",
  sar_writer: "Synthesize only supplied evidence into a traceable SAR narrative.",
  compliance_reviewer: "Check evidence support, citations, materiality, tone, and regulatory fit.",
};

const AGENT_LABELS: Record<string, string> = {
  evidence_investigator: "Evidence investigator",
  regulatory_analyst: "Regulatory analyst",
  sar_writer: "SAR writer",
  compliance_reviewer: "Compliance reviewer",
};

function timelineStatus(
  run: AgentRun | undefined,
  missing: AgentTimelineStatus,
): AgentTimelineStatus {
  if (!run) {
    return missing;
  }
  if (run.status === "started") {
    return "running";
  }
  return run.status;
}

function roleRow(
  run: AgentRun | undefined,
  agent: string,
  attempt: number,
  missing: AgentTimelineStatus,
): AgentTimelineRow {
  const revisionLabel = attempt > 1 ? ` · Revision ${attempt - 1}` : "";
  return {
    id: run?.agentRunId ?? `${agent}-${attempt}`,
    label: `${AGENT_LABELS[agent] ?? agent}${revisionLabel}`,
    purpose: AGENT_PURPOSES[agent] ?? "Execute one bounded workflow role.",
    status: timelineStatus(run, missing),
    agentRun: run,
  };
}

function deterministicStatus(state: InvestigationState, step: string): AgentTimelineStatus {
  if (state.completedSteps.includes(step)) {
    return "completed";
  }
  return state.status === "completed" || state.status === "failed" ? "skipped" : "pending";
}

function singleWriterTimeline(state: InvestigationState): AgentTimelineRow[] {
  const sarStatus: AgentTimelineStatus =
    state.sarStatus === "failed"
      ? "failed"
      : state.completedSteps.includes("sar")
        ? "completed"
        : state.sarStarted
          ? "running"
          : state.status === "completed"
            ? "skipped"
            : "pending";
  const reviewStatus: AgentTimelineStatus =
    state.status === "completed" && state.sarDraftId
      ? "awaiting"
      : state.status === "completed" || state.status === "failed"
        ? "skipped"
        : "pending";
  return [
    {
      id: "rules",
      label: "Rules",
      purpose: "Evaluate deterministic fraud and AML indicators.",
      status: deterministicStatus(state, "rules"),
    },
    {
      id: "risk",
      label: "Risk scored",
      purpose: "Score the transaction and record its model provenance.",
      status: deterministicStatus(state, "scoring"),
    },
    {
      id: "sar",
      label: "SAR drafted",
      purpose: "Draft a report from the scored evidence and grounded regulations.",
      status: sarStatus,
    },
    {
      id: "human-review",
      label: "Awaiting human review",
      purpose: "Keep the report in draft until an authorized reviewer decides.",
      status: reviewStatus,
    },
  ];
}

function groupStatus(children: AgentTimelineRow[]): AgentTimelineStatus {
  const statuses = new Set(children.map((row) => row.status));
  if (statuses.has("running") || statuses.has("revision_requested")) {
    return "running";
  }
  if (statuses.has("failed")) {
    return "failed";
  }
  if (statuses.has("degraded")) {
    return "degraded";
  }
  if ([...statuses].every((status) => status === "completed")) {
    return "completed";
  }
  if ([...statuses].every((status) => status === "skipped")) {
    return "skipped";
  }
  return "pending";
}

export function investigationTimeline(state: InvestigationState): AgentTimelineRow[] {
  if (state.workflowMode !== "multi_agent") {
    return singleWriterTimeline(state);
  }
  const byRoleAttempt = new Map(
    state.agentRuns.map((run) => [`${run.agent}:${run.attempt}`, run] as const),
  );
  const missing: AgentTimelineStatus =
    state.status === "completed" || state.status === "failed" ? "skipped" : "pending";
  const parallel = [
    roleRow(byRoleAttempt.get("evidence_investigator:1"), "evidence_investigator", 1, missing),
    roleRow(byRoleAttempt.get("regulatory_analyst:1"), "regulatory_analyst", 1, missing),
  ];
  const rows: AgentTimelineRow[] = [
    {
      id: "rules",
      label: "Rules",
      purpose: "Evaluate deterministic fraud and AML indicators.",
      status: deterministicStatus(state, "rules"),
    },
    {
      id: "risk",
      label: "Risk scored",
      purpose: "Score the transaction and record its model provenance.",
      status: deterministicStatus(state, "scoring"),
    },
    {
      id: "parallel-investigation",
      label: "Parallel investigation",
      purpose: "Investigate case evidence and regulation independently before synthesis.",
      status: groupStatus(parallel),
      children: parallel,
    },
  ];
  const attemptCount = Math.max(1, state.revisionCount + 1);
  for (let attempt = 1; attempt <= attemptCount; attempt += 1) {
    const writer = byRoleAttempt.get(`sar_writer:${attempt}`);
    rows.push(
      roleRow(writer, "sar_writer", attempt, missing),
      roleRow(
        byRoleAttempt.get(`compliance_reviewer:${attempt}`),
        "compliance_reviewer",
        attempt,
        writer?.status === "failed" ? "skipped" : missing,
      ),
    );
  }
  rows.push({
    id: "human-review",
    label: "Awaiting human review",
    purpose: "Keep the report in draft until an authorized reviewer decides.",
    status:
      state.status === "completed" && state.sarDraftId
        ? "awaiting"
        : state.status === "completed" || state.status === "failed"
          ? "skipped"
          : "pending",
  });
  return rows;
}
