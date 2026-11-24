import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../lib/i18n";
import type { MemberDayConfig, MemberView } from "../types";

export interface MemberProfilePayload {
  default_commit_rate: number;
  condition_icon: string;
  formation_role: string;
  formation_position: string;
  formation_x: number;
  formation_y: number;
  display_order: number;
  title_tags: string[];
  special_abilities: string[];
}

export interface MemberDayConfigPayload {
  work_date: string;
  is_vacation: boolean;
  leave_type: string;
  commit_rate_override: number | null;
  note: string;
}

interface MembersViewProps {
  members: MemberView[];
  selectedUserId: string;
  isTopManager: boolean;
  onUpdateProfile: (userId: string, payload: MemberProfilePayload) => void;
  onUpdateIdentity: (userId: string, payload: { nickname: string; is_active: boolean }) => void;
  onUpdateDayConfig: (userId: string, payload: MemberDayConfigPayload) => void;
}

type LeaveChoice = "none" | "full" | "am" | "pm" | "custom";

interface DayDraft {
  commitPercent: string;
  leaveChoice: LeaveChoice;
  customLeave: string;
  note: string;
}

const CONDITION_VALUES = ["great", "good", "normal", "tired", "bad"] as const;
const ROLE_OPTIONS = ["", "承認", "審査", "チームリーダー", "設計", "トレーサー"] as const;

function formatLocalDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentSunday(): Date {
  const today = new Date();
  const sunday = new Date(today);
  sunday.setDate(today.getDate() - today.getDay());
  return sunday;
}

function buildTwoWeekDays(): string[] {
  const start = currentSunday();
  return Array.from({ length: 14 }, (_, index) => {
    const item = new Date(start);
    item.setDate(start.getDate() + index);
    return formatLocalDate(item);
  });
}

function clampPercent(value: string, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, Math.min(100, parsed));
}

function conditionShortLabel(value: string): string {
  const index = CONDITION_VALUES.findIndex((item) => item === value);
  return ["😄", "🙂", "😐", "😟", "😵"][index] ?? "😐";
}

function leaveTypeToChoice(config: MemberDayConfig | undefined): LeaveChoice {
  const leaveType = config?.LeaveType?.trim() ?? "";
  if (config?.IsVacation || leaveType === "全休") return "full";
  if (leaveType === "AM半休") return "am";
  if (leaveType === "PM半休") return "pm";
  if (leaveType) return "custom";
  return "none";
}

function leaveTypeForDraft(draft: DayDraft): string {
  if (draft.leaveChoice === "full") return "全休";
  if (draft.leaveChoice === "am") return "AM半休";
  if (draft.leaveChoice === "pm") return "PM半休";
  if (draft.leaveChoice === "custom") return draft.customLeave.trim();
  return "";
}

function draftForConfig(config: MemberDayConfig | undefined): DayDraft {
  const choice = leaveTypeToChoice(config);
  return {
    commitPercent:
      config?.CommitRateOverride === null || config?.CommitRateOverride === undefined
        ? ""
        : String(Math.round(config.CommitRateOverride * 100)),
    leaveChoice: choice,
    customLeave: choice === "custom" ? config?.LeaveType ?? "" : "",
    note: config?.Note ?? "",
  };
}

function roleLabelKey(role: string): string {
  if (role === "承認") return "members.role_approval";
  if (role === "審査") return "members.role_review";
  if (role === "チームリーダー") return "members.role_leader";
  if (role === "設計") return "members.role_design";
  if (role === "トレーサー") return "members.role_tracer";
  return "members.role_none";
}

function conditionLabelKey(value: string): string {
  return `members.condition_${value}`;
}

function formatMonthDay(value: string): string {
  const parsed = new Date(`${value}T00:00:00`);
  return `${parsed.getMonth() + 1}/${parsed.getDate()}`;
}

function isWeekendDate(date: Date): boolean {
  const day = date.getDay();
  return day === 0 || day === 6;
}

export function MembersView({
  members,
  selectedUserId,
  isTopManager,
  onUpdateProfile,
  onUpdateIdentity,
  onUpdateDayConfig,
}: MembersViewProps): JSX.Element {
  const { t, language } = useI18n();
  const [selectedMemberId, setSelectedMemberId] = useState(selectedUserId);
  const [defaultCommitPercent, setDefaultCommitPercent] = useState("100");
  const [conditionIcon, setConditionIcon] = useState("normal");
  const [formationRole, setFormationRole] = useState("");
  const [nickname, setNickname] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [dayDrafts, setDayDrafts] = useState<Record<string, DayDraft>>({});
  const twoWeekDays = useMemo(() => buildTwoWeekDays(), []);
  const today = useMemo(() => formatLocalDate(new Date()), []);
  const daySaveTimersRef = useRef<Record<string, number>>({});
  const profileSaveTimerRef = useRef<number | null>(null);

  const selectedMember = useMemo(
    () => members.find((member) => member.UserId === selectedMemberId) ?? members[0] ?? null,
    [members, selectedMemberId]
  );

  useEffect(() => {
    if (!selectedUserId) return;
    if (members.some((member) => member.UserId === selectedMemberId)) return;
    setSelectedMemberId(selectedUserId);
  }, [members, selectedMemberId, selectedUserId]);

  useEffect(() => {
    if (!selectedMember) return;
    for (const timerId of Object.values(daySaveTimersRef.current)) {
      window.clearTimeout(timerId);
    }
    daySaveTimersRef.current = {};
    if (profileSaveTimerRef.current !== null) {
      window.clearTimeout(profileSaveTimerRef.current);
      profileSaveTimerRef.current = null;
    }
    const profile = selectedMember.Profile;
    setDefaultCommitPercent(String(Math.round(profile.DefaultCommitRate * 100)));
    setConditionIcon(profile.ConditionIcon || "normal");
    setFormationRole(profile.FormationRole);
    setNickname(selectedMember.Nickname || selectedMember.Name);
    setIsActive(Boolean(selectedMember.IsActive));

    const nextDrafts: Record<string, DayDraft> = {};
    for (const day of twoWeekDays) {
      const config = selectedMember.DayConfigs.find((item) => item.WorkDate === day);
      nextDrafts[day] = draftForConfig(config);
    }
    setDayDrafts(nextDrafts);
  }, [selectedMember, twoWeekDays]);

  const canEditProfile = Boolean(selectedMember) && (isTopManager || selectedMember?.UserId === selectedUserId);
  const canEditIdentity = Boolean(selectedMember) && isTopManager;

  useEffect(() => {
    return () => {
      for (const timerId of Object.values(daySaveTimersRef.current)) {
        window.clearTimeout(timerId);
      }
      if (profileSaveTimerRef.current !== null) {
        window.clearTimeout(profileSaveTimerRef.current);
      }
    };
  }, []);

  const saveProfile = (
    overrides: Partial<{
      defaultCommitPercent: string;
      conditionIcon: string;
      formationRole: string;
    }> = {},
    delayMs = 600
  ): void => {
    if (!selectedMember || !canEditProfile) return;
    if (profileSaveTimerRef.current !== null) {
      window.clearTimeout(profileSaveTimerRef.current);
    }
    const nextDefaultCommitPercent = overrides.defaultCommitPercent ?? defaultCommitPercent;
    const nextConditionIcon = overrides.conditionIcon ?? conditionIcon;
    const nextFormationRole = overrides.formationRole ?? formationRole;
    profileSaveTimerRef.current = window.setTimeout(() => {
      onUpdateProfile(selectedMember.UserId, {
        default_commit_rate:
          clampPercent(nextDefaultCommitPercent, selectedMember.Profile.DefaultCommitRate * 100) / 100,
        condition_icon: nextConditionIcon,
        formation_role: nextFormationRole,
        formation_position: "",
        formation_x: selectedMember.Profile.FormationX,
        formation_y: selectedMember.Profile.FormationY,
        display_order: selectedMember.Profile.DisplayOrder,
        title_tags: [],
        special_abilities: [],
      });
    }, delayMs);
  };

  const saveIdentity = (
    overrides: Partial<{
      nickname: string;
      isActive: boolean;
    }> = {},
    delayMs = 300
  ): void => {
    if (!selectedMember || !canEditIdentity) return;
    if (profileSaveTimerRef.current !== null) {
      window.clearTimeout(profileSaveTimerRef.current);
    }
    const nextNickname = overrides.nickname ?? nickname;
    const nextIsActive = overrides.isActive ?? isActive;
    profileSaveTimerRef.current = window.setTimeout(() => {
      onUpdateIdentity(selectedMember.UserId, {
        nickname: nextNickname,
        is_active: nextIsActive,
      });
    }, delayMs);
  };

  const scheduleDaySave = (day: string, draft: DayDraft, delayMs: number): void => {
    if (!selectedMember || !isTopManager) return;
    const existingTimer = daySaveTimersRef.current[day];
    if (existingTimer !== undefined) {
      window.clearTimeout(existingTimer);
    }
    daySaveTimersRef.current[day] = window.setTimeout(() => {
      const leaveType = leaveTypeForDraft(draft);
      const commitText = draft.commitPercent.trim();
      onUpdateDayConfig(selectedMember.UserId, {
        work_date: day,
        is_vacation: leaveType === "全休",
        leave_type: leaveType,
        commit_rate_override: commitText ? clampPercent(commitText, 0) / 100 : null,
        note: draft.note,
      });
      delete daySaveTimersRef.current[day];
    }, delayMs);
  };

  const updateDayDraft = (day: string, patch: Partial<DayDraft>, delayMs = 600): void => {
    const nextDraft = {
      ...draftForConfig(undefined),
      ...(dayDrafts[day] ?? {}),
      ...patch,
    };
    setDayDrafts((current) => ({
      ...current,
      [day]: nextDraft,
    }));
    scheduleDaySave(day, nextDraft, delayMs);
  };

  const weekdayFormatter = useMemo(
    () => new Intl.DateTimeFormat(language === "ja" ? "ja-JP" : "en-US", { weekday: "short" }),
    [language]
  );

  if (members.length === 0) {
    return <div className="empty">{t("members.empty", "No members registered")}</div>;
  }

  return (
    <div className="members-two-pane">
      <section className="panel members-list-panel">
        <div className="panel-title">{t("members.list_title", "Members")}</div>
        <div className="members-list">
          {members.map((member) => {
            const profile = member.Profile;
            return (
              <button
                key={member.UserId}
                type="button"
                className={`members-list-row${member.UserId === selectedMember?.UserId ? " active" : ""}`}
                onClick={() => setSelectedMemberId(member.UserId)}
              >
                <span className={`members-condition condition-${profile.ConditionIcon}`}>
                  {conditionShortLabel(profile.ConditionIcon)}
                </span>
                <span className="members-list-main">
                  <span className="members-list-name">{member.Nickname || member.Name}</span>
                  <span className="members-list-meta">
                    {member.IsActive ? t("members.active", "Active") : t("members.inactive", "Inactive")} / {member.LoginId || "-"}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="panel members-status-panel">
        <div className="panel-title">{t("members.status_title", "Status")}</div>
        {selectedMember ? (
          <>
            <div className="members-status-grid">
              <label>
                <span>{t("members.nickname", "Nickname")}</span>
                <input
                  value={nickname}
                  onChange={(e) => {
                    const value = e.currentTarget.value;
                    setNickname(value);
                    saveIdentity({ nickname: value });
                  }}
                  disabled={!canEditIdentity}
                />
              </label>
              <label>
                <span>{t("members.active_state", "Active")}</span>
                <select
                  value={isActive ? "1" : "0"}
                  onChange={(e) => {
                    const value = e.currentTarget.value === "1";
                    setIsActive(value);
                    saveIdentity({ isActive: value }, 80);
                  }}
                  disabled={!canEditIdentity}
                >
                  <option value="1">{t("members.active", "Active")}</option>
                  <option value="0">{t("members.inactive", "Inactive")}</option>
                </select>
              </label>
              <label>
                <span>{t("members.login_id", "Login ID")}</span>
                <input value={selectedMember.LoginId || ""} disabled />
              </label>
              <label>
                <span>{t("members.iquavis_user_id", "iQUAVIS User ID")}</span>
                <input value={selectedMember.IQuavisUserId || ""} disabled />
              </label>
            </div>

            <div className="members-calendar-title">{t("members.daily_title", "This Week / Next Week")}</div>
            <div className="members-day-list">
              <div className="members-day-header">
                <span>{t("members.column_day", "Day")}</span>
                <span>{t("members.column_commit", "Commit")}</span>
                <span>{t("members.column_leave", "Leave")}</span>
                <span>{t("members.column_note", "Note")}</span>
              </div>
              {twoWeekDays.map((day) => {
                const draft = dayDrafts[day] ?? draftForConfig(undefined);
                const parsedDay = new Date(`${day}T00:00:00`);
                const isWeekend = isWeekendDate(parsedDay);
                return (
                  <div
                    key={day}
                    className={`members-day-row${day === today ? " today" : ""}${day < today ? " past" : ""}${isWeekend ? " weekend" : ""}`}
                  >
                    <div className="members-day-label">
                      <span className="members-day-weekday">{weekdayFormatter.format(parsedDay)}</span>
                      <span className="members-day-date">{formatMonthDay(day)}</span>
                    </div>
                    <div className="members-input-unit">
                      <input
                        value={draft.commitPercent}
                        onChange={(e) => updateDayDraft(day, { commitPercent: e.currentTarget.value })}
                        disabled={!isTopManager}
                        inputMode="numeric"
                        placeholder={t("members.commit_blank", "Default")}
                      />
                      <span>%</span>
                    </div>
                    <div className="members-leave-cell">
                      <select
                        value={draft.leaveChoice}
                        onChange={(e) =>
                          updateDayDraft(day, {
                            leaveChoice: e.currentTarget.value as LeaveChoice,
                            customLeave: e.currentTarget.value === "custom" ? draft.customLeave : "",
                          }, 120)
                        }
                        disabled={!isTopManager}
                      >
                        <option value="none">{t("members.leave_none", "Normal")}</option>
                        <option value="full">{t("members.leave_full", "Full Day")}</option>
                        <option value="am">{t("members.leave_am", "AM Half")}</option>
                        <option value="pm">{t("members.leave_pm", "PM Half")}</option>
                        <option value="custom">{t("members.leave_custom", "Custom")}</option>
                      </select>
                      {draft.leaveChoice === "custom" ? (
                        <input
                          value={draft.customLeave}
                          onChange={(e) => updateDayDraft(day, { customLeave: e.currentTarget.value })}
                          disabled={!isTopManager}
                          placeholder={t("members.custom_leave_placeholder", "Leave text")}
                        />
                      ) : null}
                    </div>
                    <input
                      value={draft.note}
                      onChange={(e) => updateDayDraft(day, { note: e.currentTarget.value })}
                      disabled={!isTopManager}
                      placeholder={t("members.note_placeholder", "Note")}
                    />
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}
