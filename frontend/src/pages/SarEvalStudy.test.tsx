import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sarEvalStudy } from "../test/factories";
import { ADR_019_HREF, SarEvalStudy } from "./SarEvalStudy";

describe("SarEvalStudy", () => {
  it("renders the synthetic disclosure, ADR, and sign-derived finding without a backend call", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<SarEvalStudy data={sarEvalStudy()} />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Multi-agent SAR drafting study" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Public synthetic offline study — not live tenant data."),
    ).toBeInTheDocument();
    expect(screen.getByText(/makes no backend or provider call/)).toBeInTheDocument();
    expect(screen.getByTestId("study-finding")).toHaveTextContent(
      /Multi-agent drafting improved the paired quality result/,
    );
    const adr = screen.getByRole("link", { name: /ADR-019/ });
    expect(adr).toHaveAttribute("href", ADR_019_HREF);
    expect(adr).toHaveAttribute("target", "_blank");
    expect(adr).toHaveAttribute("rel", "noreferrer");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("renders separate judge-scored and programmatic comparison tables", () => {
    render(<SarEvalStudy data={sarEvalStudy()} />);

    for (const [label, value, interval] of [
      ["Completeness paired delta", "+10.0 pp", "[+4.0 pp, +16.0 pp]"],
      ["Unsupported-claims paired delta", "-0.25", "[-0.40, -0.10]"],
      ["Citation-precision paired delta", "+2.0 pp", "[-1.0 pp, +5.0 pp]"],
      ["Cost paired delta", "+$0.0300", "[+$0.0200, +$0.0400]"],
    ]) {
      const tile = screen.getByText(label).closest("dl");
      expect(tile).not.toBeNull();
      expect(within(tile!).getByText(value)).toBeInTheDocument();
      expect(within(tile!).getByText((content) => content.includes(interval))).toBeInTheDocument();
    }

    const quality = screen.getByRole("table", { name: "Judge-scored quality comparison" });
    expect(within(quality).getByText("FinCEN narrative completeness")).toBeInTheDocument();
    expect(within(quality).getByText("Unsupported claims")).toBeInTheDocument();
    expect(within(quality).getAllByText("Excludes zero")).toHaveLength(2);
    expect(within(quality).getByText("+10.0 pp")).toBeInTheDocument();
    expect(within(quality).getByText("-0.25")).toBeInTheDocument();

    const programmatic = screen.getByRole("table", { name: "Programmatic evaluation metrics" });
    expect(within(programmatic).getByText("Citation precision")).toBeInTheDocument();
    expect(within(programmatic).getByText("Citation recall")).toBeInTheDocument();
    expect(within(programmatic).getByText("Fabricated citations")).toBeInTheDocument();
    expect(within(programmatic).getByText("Cost per narrative")).toBeInTheDocument();
    expect(within(programmatic).getByText("Latency per narrative")).toBeInTheDocument();
    expect(within(programmatic).getByText("Model calls per narrative")).toBeInTheDocument();
    expect(within(programmatic).getAllByText("Includes zero")).toHaveLength(2);
    expect(screen.getByText(/Latency is the persisted investigation/)).toBeInTheDocument();
    expect(
      screen.getByText(/programmatic provenance attached to each API result/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/come from recorded executions/)).not.toBeInTheDocument();
  });

  it("renders every paired scenario and every required arm measurement accessibly", () => {
    const data = sarEvalStudy();
    render(<SarEvalStudy data={data} />);

    const table = screen.getByRole("table", { name: "Per-scenario paired results" });
    expect(within(table).getAllByRole("row")).toHaveLength(data.scenarios.length + 1);
    for (const scenario of data.scenarios) {
      expect(within(table).getByText(scenario.scenarioId)).toBeInTheDocument();
    }

    const first = data.scenarios[0];
    const row = within(table).getByText(first.scenarioId).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row!).getByText("Structuring")).toBeInTheDocument();
    expect(within(row!).getByText("Clean")).toBeInTheDocument();
    expect(
      within(row!).getByRole("group", {
        name: `${first.scenarioId} completeness and unsupported claims`,
      }),
    ).toHaveTextContent(/Single 4\/5 · 1 unsupportedMulti 5\/5 · 0 unsupported/);
    expect(
      within(row!).getByRole("group", { name: `${first.scenarioId} citation metrics` }),
    ).toHaveTextContent(/Single 90.0% \/ 80.0% \/ 0Multi 90.0% \/ 80.0% \/ 0/);
    expect(
      within(row!).getByRole("group", {
        name: `${first.scenarioId} cost and persisted run duration`,
      }),
    ).toHaveTextContent(/Single \$0.02 · 2.0 sMulti \$0.05 · 3.2 s/);
    expect(
      within(row!).getByRole("group", { name: `${first.scenarioId} model calls` }),
    ).toHaveTextContent(/Single 1Multi 4/);
    expect(
      within(row!).getByRole("group", { name: `${first.scenarioId} judge agreement` }),
    ).toHaveTextContent(/Single 80.0%Multi 90.0%/);
  });

  it("publishes judge agreement and the complete reproducibility protocol", async () => {
    const user = userEvent.setup();
    const data = sarEvalStudy();
    render(<SarEvalStudy data={data} />);

    const stability = screen.getByRole("heading", { name: "Judge stability" }).parentElement
      ?.parentElement;
    expect(stability).not.toBeNull();
    expect(within(stability!).getAllByText("80.0%")).toHaveLength(4);
    expect(within(stability!).getAllByText("90.0%")).toHaveLength(4);
    expect(stability).toHaveTextContent(/Overall agreement is the mean/);
    const agreementTable = within(stability!).getByRole("table", {
      name: "Judge inter-sample agreement by measure",
    });
    expect(within(agreementTable).getByText("FinCEN element pass/fail")).toBeInTheDocument();
    expect(within(agreementTable).getByText("Unsupported-claim count")).toBeInTheDocument();
    expect(within(agreementTable).getByText("Unsupported-claim spans")).toBeInTheDocument();
    expect(screen.getByText(data.runId)).toBeInTheDocument();
    expect(screen.getByText("10,000")).toBeInTheDocument();
    expect(screen.getByText(data.judge.modelId)).toBeInTheDocument();
    expect(screen.getByText(`Family: ${data.judge.modelFamily}`)).toBeInTheDocument();
    expect(screen.getByText("Blind; A/B order randomized per scenario")).toBeInTheDocument();
    expect(screen.getByText(data.reportSha256)).toBeInTheDocument();
    const variants = screen.getByRole("group", { name: "Scenario variants" });
    expect(within(variants).getByText("Clean")).toBeInTheDocument();
    expect(within(variants).getByText("Thin evidence")).toBeInTheDocument();
    expect(within(variants).getByText("Conflicting evidence")).toBeInTheDocument();
    expect(within(variants).getByText("Citation bait")).toBeInTheDocument();

    const provenance = screen.getByRole("button", { name: "Multi-agent provenance" });
    expect(provenance).toHaveAttribute("aria-expanded", "false");
    await user.click(provenance);
    expect(provenance).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("agents-v1")).toBeInTheDocument();
    expect(screen.getByText(data.armProvenance[1].modelIds.join(", "))).toBeInTheDocument();
    expect(screen.getByText(data.armProvenance[1].promptHashes[0])).toBeInTheDocument();
    expect(
      within(provenance.parentElement!).getByText(
        `${data.armProvenance[1].writerModelId} (${data.armProvenance[1].writerModelFamily})`,
      ),
    ).toBeInTheDocument();
  });

  it("states plainly when multi-agent quality loses", () => {
    const data = sarEvalStudy();
    const completeness = data.summary.deltas.find(
      (candidate) => candidate.metric === "completenessRate",
    );
    const unsupported = data.summary.deltas.find(
      (candidate) => candidate.metric === "unsupportedClaims",
    );
    expect(completeness).toBeDefined();
    expect(unsupported).toBeDefined();
    Object.assign(completeness!, { pointEstimate: -0.1, ciLower: -0.16, ciUpper: -0.04 });
    Object.assign(unsupported!, { pointEstimate: 0.25, ciLower: 0.1, ciUpper: 0.4 });

    render(<SarEvalStudy data={data} />);
    expect(screen.getByTestId("study-finding")).toHaveTextContent(
      /underperformed the single writer/,
    );
    expect(screen.getByTestId("study-finding")).toHaveTextContent(/lowered.*increased/);
  });

  it("reports mixed and tied quality results without favorable assumptions", () => {
    const mixed = sarEvalStudy();
    const mixedUnsupported = mixed.summary.deltas.find(
      (candidate) => candidate.metric === "unsupportedClaims",
    );
    expect(mixedUnsupported).toBeDefined();
    Object.assign(mixedUnsupported!, { pointEstimate: 0.25, ciLower: 0.1, ciUpper: 0.4 });
    const first = render(<SarEvalStudy data={mixed} />);
    expect(screen.getByTestId("study-finding")).toHaveTextContent(/mixed quality result/);
    first.unmount();

    const tied = sarEvalStudy();
    for (const metric of ["completenessRate", "unsupportedClaims"] as const) {
      const delta = tied.summary.deltas.find((candidate) => candidate.metric === metric);
      expect(delta).toBeDefined();
      Object.assign(delta!, { pointEstimate: 0, ciLower: -0.1, ciUpper: 0.1, significant: false });
    }
    render(<SarEvalStudy data={tied} />);
    expect(screen.getByTestId("study-finding")).toHaveTextContent(/tied the single writer/);
  });
});
