import { useRef, type DragEvent } from "react";
import { STEP_CATEGORY_LABELS, STEP_DEFINITIONS, STEP_TYPES, type StepCategory, type VisualStepType } from "@/lib/stepDefinitions";

interface StepPaletteProps {
  onDragStart: (event: DragEvent, stepType: VisualStepType) => void;
  onAddStep?: (stepType: VisualStepType) => void;
}

const CATEGORY_ORDER: StepCategory[] = ["io", "transform", "quality", "reshape", "advanced"];

export function StepPalette({ onDragStart, onAddStep }: StepPaletteProps) {
  const suppressClickRef = useRef(false);

  const handleCardClick = (stepType: VisualStepType) => {
    if (!onAddStep) {
      return;
    }
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    onAddStep(stepType);
  };

  return (
    <aside
      className="flex h-full w-64 shrink-0 flex-col border-r bg-[var(--bg-surface)] p-3 text-[var(--text-primary)]"
      style={{ borderColor: "var(--widget-border)" }}
      data-testid="step-palette"
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">Step Palette</h3>

      <div className="flex-1 space-y-4 overflow-y-auto pr-1">
        {CATEGORY_ORDER.map((category) => {
          const categorySteps = STEP_TYPES.filter(
            (stepType) => STEP_DEFINITIONS[stepType].category === category,
          );
          if (categorySteps.length === 0) {
            return null;
          }

          return (
            <section key={category} className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                {STEP_CATEGORY_LABELS[category]}
              </p>

              <div className="space-y-1.5">
                {categorySteps.map((stepType) => {
                  const definition = STEP_DEFINITIONS[stepType];
                  return (
                    <button
                      key={stepType}
                      type="button"
                      draggable
                      onDragStart={(event) => {
                        suppressClickRef.current = true;
                        onDragStart(event, stepType);
                      }}
                      onClick={() => handleCardClick(stepType)}
                      data-testid={`step-card-${stepType}`}
                      className="w-full rounded-md border bg-[var(--bg-elevated)] px-2.5 py-2 text-left transition-colors hover:bg-[var(--interactive-hover)]"
                      style={{ borderColor: "var(--widget-border)" }}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-0.5">
                          <p className="text-sm font-medium leading-none text-[var(--text-primary)]">
                            <span className="mr-1.5">{definition.icon}</span>
                            {definition.label}
                          </p>
                          <p className="text-[11px] text-[var(--text-secondary)]">{definition.description}</p>
                        </div>
                        {!definition.backendSupported && (
                          <span
                            className="rounded border px-1 py-0.5 text-[10px] font-semibold uppercase"
                            style={{
                              borderColor: "color-mix(in srgb, var(--accent-warning) 45%, transparent)",
                              color: "var(--accent-warning)",
                              backgroundColor: "color-mix(in srgb, var(--accent-warning) 14%, transparent)",
                            }}
                          >
                            Visual
                          </span>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </aside>
  );
}
