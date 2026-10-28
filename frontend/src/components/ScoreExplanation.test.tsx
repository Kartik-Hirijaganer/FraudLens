import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScoreExplanation } from "./ScoreExplanation";

describe("ScoreExplanation", () => {
  it("keeps rare-event probability separate from the policy score", () => {
    render(<ScoreExplanation probability={0.001} />);
    expect(screen.getByText("Calibrated model probability: 0.1%")).toBeInTheDocument();
    expect(
      screen.getByText(/policy risk score determines risk bands and alerts/),
    ).toBeInTheDocument();
    expect(screen.getByText(/A match does not establish fraud/)).toBeInTheDocument();
  });

  it("preserves zero probability", () => {
    render(<ScoreExplanation probability={0} />);
    expect(screen.getByText("Calibrated model probability: 0.0%")).toBeInTheDocument();
  });

  it.each([null, undefined])("omits an unavailable probability (%s)", (probability) => {
    render(<ScoreExplanation probability={probability} />);
    expect(screen.queryByText(/Calibrated model probability:/)).not.toBeInTheDocument();
  });
});
