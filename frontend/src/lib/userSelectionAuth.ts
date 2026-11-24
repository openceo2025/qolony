import type { User } from "../types";

export const SESSION_UNLOCKED_USER_IDS_KEY = "qolony.unlocked_user_ids";

export function shouldRequireSelectionPassword(user: Pick<User, "IsTopManager" | "HasPassword">): boolean {
  return user.IsTopManager && Boolean(user.HasPassword);
}

export function parseUnlockedUserIds(raw: string | null): Record<string, true> {
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return {};
    }
    return parsed.reduce<Record<string, true>>((acc, value) => {
      if (typeof value === "string" && value.trim()) {
        acc[value] = true;
      }
      return acc;
    }, {});
  } catch {
    return {};
  }
}

export function serializeUnlockedUserIds(unlockedUserIds: Record<string, true>): string {
  return JSON.stringify(Object.keys(unlockedUserIds).sort());
}
