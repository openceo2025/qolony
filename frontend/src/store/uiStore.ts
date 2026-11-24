import { create } from "zustand";
import type { TabKey } from "../types";

interface UiState {
  selectedUserId: string;
  selectedTaskId: string | null;
  wbsOpenState: Record<string, boolean>;
  myTaskShowCompleted: boolean;
  currentTab: TabKey;
  previousTab: TabKey;
  rankingYearMonth: string;
  rankingPeriodMonths: 1 | 3 | 6 | 12;
  splitTemplateIndex: number;
  statusMessage: string;
  statusIsError: boolean;
  setSelectedUserId: (value: string) => void;
  setSelectedTaskId: (value: string | null) => void;
  setWbsOpenState: (value: Record<string, boolean>) => void;
  setMyTaskShowCompleted: (value: boolean) => void;
  setCurrentTab: (value: TabKey) => void;
  setPreviousTab: (value: TabKey) => void;
  setRankingYearMonth: (value: string) => void;
  setRankingPeriodMonths: (value: 1 | 3 | 6 | 12) => void;
  setSplitTemplateIndex: (value: number) => void;
  setStatus: (message: string, isError?: boolean) => void;
}

const defaultYearMonth = new Date().toISOString().slice(0, 7);

export const useUiStore = create<UiState>((set) => ({
  selectedUserId: "",
  selectedTaskId: null,
  wbsOpenState: {},
  myTaskShowCompleted: false,
  currentTab: "wbs",
  previousTab: "wbs",
  rankingYearMonth: defaultYearMonth,
  rankingPeriodMonths: 1,
  splitTemplateIndex: 0,
  statusMessage: "QOLONY ready",
  statusIsError: false,
  setSelectedUserId: (value) => set({ selectedUserId: value }),
  setSelectedTaskId: (value) => set({ selectedTaskId: value }),
  setWbsOpenState: (value) => set({ wbsOpenState: { ...value } }),
  setMyTaskShowCompleted: (value) => set({ myTaskShowCompleted: value }),
  setCurrentTab: (value) => set({ currentTab: value }),
  setPreviousTab: (value) => set({ previousTab: value }),
  setRankingYearMonth: (value) => set({ rankingYearMonth: value }),
  setRankingPeriodMonths: (value) => set({ rankingPeriodMonths: value }),
  setSplitTemplateIndex: (value) => set({ splitTemplateIndex: value }),
  setStatus: (message, isError = false) => set({ statusMessage: message, statusIsError: isError }),
}));

