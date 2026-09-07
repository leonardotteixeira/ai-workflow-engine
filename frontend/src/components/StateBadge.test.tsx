import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StateBadge } from "./StateBadge";

describe("StateBadge", () => {
  it("renders the given state", () => {
    render(<StateBadge state="WAITING" />);
    expect(screen.getByText("WAITING")).toBeInTheDocument();
  });

  it.each(["PENDING", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED"] as const)(
    "applies a class for state %s",
    (state) => {
      render(<StateBadge state={state} />);
      expect(screen.getByText(state)).toHaveClass(state);
    },
  );
});
