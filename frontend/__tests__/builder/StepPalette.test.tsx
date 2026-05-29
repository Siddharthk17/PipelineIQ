import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StepPalette } from "@/components/pipeline-builder/StepPalette";
import { STEP_DEFINITIONS, STEP_CATEGORY_LABELS, STEP_TYPES } from "@/lib/stepDefinitions";

describe("StepPalette", () => {
  it("adds a step when a card is clicked", () => {
    const onDragStart = vi.fn();
    const onAddStep = vi.fn();

    render(<StepPalette onDragStart={onDragStart} onAddStep={onAddStep} />);

    fireEvent.click(screen.getByTestId("palette-item-load"));

    expect(onAddStep).toHaveBeenCalledWith("load");
  });

  it("renders all step type cards", () => {
    const onDragStart = vi.fn();
    render(<StepPalette onDragStart={onDragStart} />);

    for (const stepType of STEP_TYPES) {
      expect(screen.getByTestId(`palette-item-${stepType}`)).toBeInTheDocument();
    }
  });

  it("renders category labels", () => {
    const onDragStart = vi.fn();
    render(<StepPalette onDragStart={onDragStart} />);

    const categoryLabels = Object.values(STEP_CATEGORY_LABELS);
    for (const label of categoryLabels) {
      const elements = screen.getAllByText(label);
      expect(elements.length).toBeGreaterThanOrEqual(1);
    }
  });

  it("renders palette header", () => {
    const onDragStart = vi.fn();
    render(<StepPalette onDragStart={onDragStart} />);

    expect(screen.getByTestId("step-palette")).toBeInTheDocument();
    expect(screen.getByText("Step Palette")).toBeInTheDocument();
  });

  it("shows step descriptions", () => {
    const onDragStart = vi.fn();
    render(<StepPalette onDragStart={onDragStart} />);

    expect(screen.getByText(STEP_DEFINITIONS.load.description)).toBeInTheDocument();
  });

  it("shows Visual badge for unsupported steps", () => {
    const onDragStart = vi.fn();
    render(<StepPalette onDragStart={onDragStart} />);

    const visualBadges = screen.getAllByText("Visual");
    expect(visualBadges.length).toBeGreaterThanOrEqual(1);
  });
});
