import type { DragEvent } from "react";
import { STEP_CATEGORY_LABELS, STEP_DEFINITIONS, STEP_TYPES, type StepCategory, type VisualStepType } from "@/lib/stepDefinitions";

interface StepPaletteProps {
  onDragStart: (event: DragEvent, stepType: VisualStepType) => void;
}

const CATEGORY_ORDER: StepCategory[] = ["io", "transform", "quality", "reshape", "advanced"];

export function StepPalette({ onDragStart }: StepPaletteProps) {
  return (
    <aside className="w-72 shrink-0 border-r bg-card/40 p-3">
      <h3 className="mb-3 text-sm font-semibold">Step Palette</h3>

      <div className="space-y-4">
        {CATEGORY_ORDER.map((category) => {
          const categorySteps = STEP_TYPES.filter(
            (stepType) => STEP_DEFINITIONS[stepType].category === category,
          );
          if (categorySteps.length === 0) {
            return null;
          }

          return (
            <section key={category} className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
                      onDragStart={(event) => onDragStart(event, stepType)}
                      className="w-full rounded-md border bg-background px-2.5 py-2 text-left hover:bg-muted"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-0.5">
                          <p className="text-sm font-medium leading-none">
                            <span className="mr-1.5">{definition.icon}</span>
                            {definition.label}
                          </p>
                          <p className="text-[11px] text-muted-foreground">{definition.description}</p>
                        </div>
                        {!definition.backendSupported && (
                          <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1 py-0.5 text-[10px] uppercase text-amber-700">
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
