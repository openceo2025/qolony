import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { UiLocale } from "../types";

type MessageDict = Record<string, unknown>;

export interface Translator {
  language: string;
  t: (key: string, fallback?: string, vars?: Record<string, string | number>) => string;
}

const DEFAULT_TRANSLATOR: Translator = {
  language: "en",
  t: (key, fallback, vars) => interpolateText(fallback ?? key, vars),
};

const I18nContext = createContext<Translator>(DEFAULT_TRANSLATOR);

function getNestedMessage(source: MessageDict, key: string): unknown {
  return key.split(".").reduce<unknown>((current, segment) => {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    return (current as MessageDict)[segment];
  }, source);
}

function interpolateText(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, name: string) => {
    if (!(name in vars)) return `{${name}}`;
    return String(vars[name]);
  });
}

export function createTranslator(locale?: UiLocale): Translator {
  const messages = (locale?.messages ?? {}) as MessageDict;
  const fallbackMessages = (locale?.fallback_messages ?? {}) as MessageDict;
  const language = locale?.language ?? "en";

  return {
    language,
    t: (key: string, fallback?: string, vars?: Record<string, string | number>) => {
      const localized = getNestedMessage(messages, key);
      if (typeof localized === "string") {
        return interpolateText(localized, vars);
      }

      const fallbackLocalized = getNestedMessage(fallbackMessages, key);
      if (typeof fallbackLocalized === "string") {
        return interpolateText(fallbackLocalized, vars);
      }

      return interpolateText(fallback ?? key, vars);
    },
  };
}

interface I18nProviderProps {
  value: Translator;
  children: ReactNode;
}

export function I18nProvider({ value, children }: I18nProviderProps): JSX.Element {
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Translator {
  return useContext(I18nContext);
}
