import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { agentRun } from "../test/factories";
import { initialInvestigationState } from "../lib/investigation";
import { AgentTimeline } from "./AgentTimeline";

describe("AgentTimeline", () => {
  it("renders the four-row flag-off path and exactly one polite live region", () => {
    render(<AgentTimeline state={initialInvestigationState()} />);

    expect(screen.getByText("Single-writer")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(document.querySelectorAll('[aria-live="polite"]')).toHaveLength(1);
  });

  it("renders the agent fork as a nested ordered list and exposes provenance", async () => {
    const evidence = agentRun();
    const regulatory = agentRun({
      agentRunId: "agent-run-2",
      agent: "regulatory_analyst",
      toolCalls: [{ name: "regulation_search", status: "completed" }],
    });
    const state = {
      ...initialInvestigationState(),
      status: "completed" as const,
      completedSteps: ["rules", "scoring", "sar"],
      workflowMode: "multi_agent" as const,
      graphVersion: "agents-v1",
      sarDraftId: "sar-1",
      agentRuns: [evidence, regulatory],
      recorded: true,
    };
    render(<AgentTimeline state={state} title="How this SAR was produced" />);

    const nested = screen.getByRole("list", { name: "Parallel agent executions" });
    expect(within(nested).getByText("Evidence investigator")).toBeInTheDocument();
    expect(screen.getByText("4-agent review")).toBeInTheDocument();
    expect(screen.getByText("Recorded")).toBeInTheDocument();

    const evidenceButton = screen.getByRole("button", { name: /Evidence investigator/ });
    await userEvent.click(evidenceButton);
    const evidencePanel = document.getElementById(
      evidenceButton.getAttribute("aria-controls") ?? "missing",
    );
    expect(evidencePanel).not.toBeNull();
    expect(within(evidencePanel!).getByText("Evidence consumed")).toBeInTheDocument();
    expect(within(evidencePanel!).getByText("Rule Hits")).toBeInTheDocument();
    expect(within(evidencePanel!).getByText("agents-v1")).toBeInTheDocument();
    expect(within(evidencePanel!).getByText("25 ms")).toBeInTheDocument();
  });

  it("separates degraded and failed attempts by glyph, wording, content, and consequence", async () => {
    const degraded = agentRun({
      status: "degraded",
      errorCode: "provider_timeout",
      result: { summary: "Partial evidence" },
    });
    const failed = agentRun({
      agentRunId: "agent-run-2",
      agent: "sar_writer",
      status: "failed",
      errorCode: "writer_schema_invalid",
      result: null,
      toolCalls: [],
    });
    const state = {
      ...initialInvestigationState(),
      workflowMode: "multi_agent" as const,
      agentRuns: [degraded, failed],
    };
    render(<AgentTimeline state={state} />);

    expect(screen.getByRole("button", { name: /Evidence investigator/ })).toHaveTextContent("!");
    expect(screen.getByRole("button", { name: /SAR writer/ })).toHaveTextContent("×");
    expect(screen.getByText("Provider Timeout")).toBeInTheDocument();

    const degradedButton = screen.getByRole("button", { name: /Evidence investigator/ });
    await userEvent.click(degradedButton);
    const degradedPanel = document.getElementById(
      degradedButton.getAttribute("aria-controls") ?? "missing",
    );
    expect(within(degradedPanel!).getByText(/usable partial result/i)).toBeInTheDocument();
    expect(within(degradedPanel!).getByText(/Partial evidence/)).toBeInTheDocument();

    const failedButton = screen.getByRole("button", { name: /SAR writer/ });
    await userEvent.click(failedButton);
    const failedPanel = document.getElementById(
      failedButton.getAttribute("aria-controls") ?? "missing",
    );
    expect(within(failedPanel!).getByText("Writer Schema Invalid")).toBeInTheDocument();
    expect(within(failedPanel!).queryByText(/Structured result/)).not.toBeInTheDocument();
  });
});
