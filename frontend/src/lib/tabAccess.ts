import type { TabKey, User } from "../types";

export function canAccessAdminTab(
  selectedUser: User | undefined,
  unlockedUserIds: Record<string, true>
): boolean {
  if (!selectedUser) return false;
  if (!selectedUser.IsTopManager) return false;
  if (!selectedUser.HasPassword) return true;
  return unlockedUserIds[selectedUser.Id] === true;
}

export function canAccessTab(
  tab: TabKey,
  selectedUser: User | undefined,
  unlockedUserIds: Record<string, true>
): boolean {
  if (tab !== "admin") return true;
  return canAccessAdminTab(selectedUser, unlockedUserIds);
}
