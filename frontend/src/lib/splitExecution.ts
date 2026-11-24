import type { TabKey } from "../types";

interface SplitExecutionOptions {
  execute: () => Promise<unknown>;
  invalidateAll: () => Promise<unknown>;
  showResult: (message: string) => void;
  setCurrentTab: (tab: TabKey) => void;
  previousTab: TabKey;
  successMessage: string;
  conflictRecoveryMessage?: string;
}

export interface SplitExecutionResult {
  saved: boolean;
  conflict: boolean;
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function extractWarnings(result: unknown): string[] {
  if (!result || typeof result !== "object" || !("warnings" in result)) return [];
  const warnings = (result as { warnings?: unknown }).warnings;
  return Array.isArray(warnings)
    ? warnings.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

export async function executeSplitWithPopup(options: SplitExecutionOptions): Promise<SplitExecutionResult> {
  try {
    const result = await options.execute();
    await options.invalidateAll();
    const warnings = extractWarnings(result);
    options.showResult([options.successMessage, ...warnings].join("\n\n"));
    options.setCurrentTab(options.previousTab);
    return { saved: true, conflict: false };
  } catch (error) {
    await options.invalidateAll();
    const conflict = !!error && typeof error === "object" && "status" in error
      && (error as { status?: unknown }).status === 409;
    const messages = [toErrorMessage(error)];
    if (conflict && options.conflictRecoveryMessage) {
      messages.push(options.conflictRecoveryMessage);
    }
    options.showResult(messages.join("\n\n"));
    return { saved: false, conflict };
  }
}
