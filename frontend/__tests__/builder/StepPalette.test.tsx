import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StepPalette } from "@/components/pipeline-builder/StepPalette";

describe("StepPalette", () => {
  it("adds a step when a card is clicked", () => {
    const onDragStart = vi.fn();
    const onAddStep = vi.fn();

    render(<StepPalette onDragStart={onDragStart} onAddStep={onAddStep} />);

    fireEvent.click(screen.getByTestId("step-card-load"));

    expect(onAddStep).toHaveBeenCalledWith("load");
  });
});
