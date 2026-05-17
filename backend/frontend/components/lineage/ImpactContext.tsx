"use client";

import React, { createContext, useContext } from "react";

export interface ImpactState {
  clickedNodeId: string | null;
  affectedSteps: Set<string>;
  clickedStepName: string | null;
}

const defaultState: ImpactState = {
  clickedNodeId: null,
  affectedSteps: new Set(),
  clickedStepName: null,
};

const ImpactContext = createContext<ImpactState>(defaultState);

export function ImpactProvider({ value, children }: { value: ImpactState; children: React.ReactNode }) {
  return <ImpactContext.Provider value={value}>{children}</ImpactContext.Provider>;
}

export function useImpactState() {
  return useContext(ImpactContext);
}
