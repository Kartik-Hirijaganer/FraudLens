import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RagPanel } from "./RagPanel";

const CITATION = {
  citation: "31 CFR 1020.320",
  title: "SAR filing requirement",
  source: "FinCEN",
  snippet: "A bank shall file a SAR …",
};

describe("RagPanel", () => {
  it("shows an empty state with the mode label when there are no citations", () => {
    render(<RagPanel citations={[]} mode="lexical" />);
    expect(screen.getByText("Retrieved via keyword fallback")).toBeInTheDocument();
  });

  it("shows a default empty description when no mode is given", () => {
    render(<RagPanel citations={[]} />);
    expect(screen.getByText("Citations appear once retrieval completes.")).toBeInTheDocument();
  });

  it("renders citations and the semantic mode label", () => {
    render(<RagPanel citations={[CITATION]} mode="vector" />);
    expect(screen.getByText("Retrieved via semantic search")).toBeInTheDocument();
    expect(screen.getByText("SAR filing requirement")).toBeInTheDocument();
    expect(screen.getByText("FinCEN")).toBeInTheDocument();
    expect(screen.getByText("31 CFR 1020.320")).toBeInTheDocument();
  });

  it("humanizes an unknown retrieval mode", () => {
    render(<RagPanel citations={[CITATION]} mode="hybrid_mode" />);
    expect(screen.getByText("Hybrid Mode")).toBeInTheDocument();
  });
});
