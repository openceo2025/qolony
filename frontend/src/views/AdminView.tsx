import { useEffect, useMemo, useState } from "react";
import { IquavisAuthoritativeImportPanel } from "../components/IquavisAuthoritativeImportPanel";
import { IquavisForcedImportPanel } from "../components/IquavisForcedImportPanel";
import { formatDisplayText } from "../lib/displayText";
import { useI18n } from "../lib/i18n";
import type {
  InfeasibilityResponse,
  IquavisAuthoritativeImportPreviewResponse,
  IquavisForcedImportPreviewResponse,
  Task,
  User,
} from "../types";

interface AdminViewProps {
  users: User[];
  topManagerId: string;
  selectedUserId: string;
  crashZonePct: number;
  infeasibility: InfeasibilityResponse | undefined;
  reworkTasks: Task[];
  allTasks: Task[];
  rankingYearMonth: string;
  iquavisAuthoritativeImportPreview: IquavisAuthoritativeImportPreviewResponse | null;
  iquavisAuthoritativeImportBusy: boolean;
  onPreviewIquavisAuthoritativeImport: (ignoredReviewItemKeys: string[]) => void;
  onApplyIquavisAuthoritativeImport: (ignoredReviewItemKeys: string[]) => void;
  iquavisForcedImportPreview: IquavisForcedImportPreviewResponse | null;
  iquavisForcedImportBusy: boolean;
  onPreviewIquavisForcedImport: () => void;
  onApplyIquavisForcedImport: () => void;
  onSetEducationRate: (payload: { userId: string; yearMonth: string; rate: number }) => void;
  onSetCrashZone: (value: number) => void;
  onSetReworkLink: (payload: { reworkTaskId: string; preventedByTaskId: string }) => void;
  onFinalizeDailyActuals: (force: boolean) => void;
  onCreateRootTask: (payload: {
    name: string;
    assigneeUserId: string;
    assignedManhour: number;
    endDate: string;
    memo: string;
  }) => void;
  onUpdateRootTaskAssignedManhour: (payload: { taskId: string; assignedManhour: number }) => void;
}

export function AdminView({
  users,
  topManagerId,
  selectedUserId,
  crashZonePct,
  infeasibility,
  reworkTasks,
  allTasks,
  rankingYearMonth,
  iquavisAuthoritativeImportPreview,
  iquavisAuthoritativeImportBusy,
  onPreviewIquavisAuthoritativeImport,
  onApplyIquavisAuthoritativeImport,
  iquavisForcedImportPreview,
  iquavisForcedImportBusy,
  onPreviewIquavisForcedImport,
  onApplyIquavisForcedImport,
  onSetEducationRate,
  onSetCrashZone,
  onSetReworkLink,
  onFinalizeDailyActuals,
  onCreateRootTask,
  onUpdateRootTaskAssignedManhour,
}: AdminViewProps): JSX.Element {
  const { t } = useI18n();
  const isTop = selectedUserId === topManagerId;
  const topUser = users.find((u) => u.Id === topManagerId);
  const currentUser = users.find((u) => u.Id === selectedUserId);

  const [educationUserId, setEducationUserId] = useState(users[0]?.Id ?? "");
  const [educationYm, setEducationYm] = useState(rankingYearMonth);
  const [educationRate, setEducationRate] = useState("0.10");
  const [crashZoneInput, setCrashZoneInput] = useState(String(crashZonePct));
  const [reworkTaskId, setReworkTaskId] = useState("");
  const [preventedByTaskId, setPreventedByTaskId] = useState("");
  const [createName, setCreateName] = useState("");
  const [createAssigneeUserId, setCreateAssigneeUserId] = useState(users[0]?.Id ?? "");
  const [createAssignedManhour, setCreateAssignedManhour] = useState("240");
  const [createEndDate, setCreateEndDate] = useState(new Date().toISOString().slice(0, 10));
  const [createMemo, setCreateMemo] = useState("");
  const [estimateRootTaskId, setEstimateRootTaskId] = useState("");
  const [estimateAssignedManhour, setEstimateAssignedManhour] = useState("");

  const rootTasks = useMemo(() => allTasks.filter((task) => task.ParentTaskId === null), [allTasks]);
  const selectedEstimateTask = useMemo(
    () => rootTasks.find((task) => task.Id === estimateRootTaskId) ?? null,
    [estimateRootTaskId, rootTasks]
  );
  useEffect(() => {
    if (users.length === 0) {
      setCreateAssigneeUserId("");
      return;
    }
    if (!users.some((u) => u.Id === createAssigneeUserId)) {
      setCreateAssigneeUserId(users[0].Id);
    }
  }, [createAssigneeUserId, users]);

  useEffect(() => {
    if (rootTasks.length === 0) {
      setEstimateRootTaskId("");
      setEstimateAssignedManhour("");
      return;
    }
    if (!rootTasks.some((task) => task.Id === estimateRootTaskId)) {
      setEstimateRootTaskId(rootTasks[0].Id);
      setEstimateAssignedManhour(String(rootTasks[0].AssignedManHour));
    }
  }, [estimateRootTaskId, rootTasks]);

  useEffect(() => {
    if (selectedEstimateTask) {
      setEstimateAssignedManhour(String(selectedEstimateTask.AssignedManHour));
    }
  }, [selectedEstimateTask?.AssignedManHour, selectedEstimateTask?.Id]);

  const preventedCandidates = useMemo(
    () => allTasks.filter((task) => !task.IsRework && task.Id !== reworkTaskId),
    [allTasks, reworkTaskId]
  );

  return (
    <div className="view-stack">
      <div className="panel">
        <div className="subtext">
          {t("admin.top_manager", "Admin: {name}", { name: topUser?.Name ?? "-" })} /{" "}
          {t("admin.current_user", "Current User: {name}", { name: currentUser?.Name ?? "-" })}
        </div>
      </div>

      <IquavisForcedImportPanel
        isTopManager={isTop}
        preview={iquavisForcedImportPreview}
        busy={iquavisForcedImportBusy}
        onPreview={onPreviewIquavisForcedImport}
        onApply={onApplyIquavisForcedImport}
      />

      <IquavisAuthoritativeImportPanel
        isTopManager={isTop}
        preview={iquavisAuthoritativeImportPreview}
        busy={iquavisAuthoritativeImportBusy}
        onPreview={onPreviewIquavisAuthoritativeImport}
        onApply={onApplyIquavisAuthoritativeImport}
      />

      <div className="panel">
        <div className="panel-title">{t("admin.title_education_rate", "Education Rate")}</div>
        <div className="toolbar-row">
          <select value={educationUserId} onChange={(e) => setEducationUserId(e.currentTarget.value)} disabled={!isTop}>
            {users.map((user) => (
              <option key={user.Id} value={user.Id}>
                {user.Name}
              </option>
            ))}
          </select>
          <input value={educationYm} onChange={(e) => setEducationYm(e.currentTarget.value)} disabled={!isTop} />
          <input value={educationRate} onChange={(e) => setEducationRate(e.currentTarget.value)} disabled={!isTop} />
          <button
            className="btn primary"
            type="button"
            disabled={!isTop}
            onClick={() =>
              onSetEducationRate({
                userId: educationUserId,
                yearMonth: educationYm,
                rate: Number(educationRate),
              })
            }
          >
            {t("common.save", "Save")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_crash_zone", "Crash Zone")}</div>
        <div className="toolbar-row">
          <input value={crashZoneInput} onChange={(e) => setCrashZoneInput(e.currentTarget.value)} disabled={!isTop} />
          <button className="btn primary" type="button" disabled={!isTop} onClick={() => onSetCrashZone(Number(crashZoneInput))}>
            {t("common.update", "Update")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_db_ops", "DB Operations")}</div>
        <div className="toolbar-row">
          <button
            className="btn primary"
            type="button"
            disabled={!isTop}
            onClick={() => {
              if (!window.confirm(t("admin.finalize_daily_actuals_confirm", "Force finalize the previous workday actual MH?"))) return;
              onFinalizeDailyActuals(true);
            }}
          >
            {t("admin.button_finalize_daily_actuals", "Finalize Daily Actuals")}
          </button>
          <span className="subtext">{t("admin.finalize_daily_actuals_once", "Once per day")}</span>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_create_root_task", "Create Root Task")}</div>
        <div className="toolbar-row">
          <input
            className="grow"
            value={createName}
            onChange={(e) => setCreateName(e.currentTarget.value)}
            placeholder={t("admin.root_task_name_placeholder", "Task name")}
            disabled={!isTop}
          />
          <select value={createAssigneeUserId} onChange={(e) => setCreateAssigneeUserId(e.currentTarget.value)} disabled={!isTop}>
            {users.map((user) => (
              <option key={user.Id} value={user.Id}>
                {user.Name}
              </option>
            ))}
          </select>
        </div>
        <div className="toolbar-row">
          <input
            value={createAssignedManhour}
            onChange={(e) => setCreateAssignedManhour(e.currentTarget.value)}
            placeholder={t("admin.root_task_mh_placeholder", "MH (min)")}
            disabled={!isTop}
          />
          <input type="date" value={createEndDate} onChange={(e) => setCreateEndDate(e.currentTarget.value)} disabled={!isTop} />
        </div>
        <div className="toolbar-row">
          <input
            className="grow"
            value={createMemo}
            onChange={(e) => setCreateMemo(e.currentTarget.value)}
            placeholder={t("admin.root_task_memo_placeholder", "Memo")}
            disabled={!isTop}
          />
          <button
            className="btn primary"
            type="button"
            disabled={!isTop || !createName.trim() || !createAssigneeUserId || !createEndDate}
            onClick={() =>
              onCreateRootTask({
                name: createName,
                assigneeUserId: createAssigneeUserId,
                assignedManhour: Number(createAssignedManhour),
                endDate: createEndDate,
                memo: createMemo,
              })
            }
          >
            {t("common.create", "Create")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_root_task_estimate", "Top Task Estimate")}</div>
        <div className="toolbar-row">
          <select
            className="grow"
            value={estimateRootTaskId}
            onChange={(e) => {
              const nextTaskId = e.currentTarget.value;
              const nextTask = rootTasks.find((task) => task.Id === nextTaskId);
              setEstimateRootTaskId(nextTaskId);
              setEstimateAssignedManhour(String(nextTask?.AssignedManHour ?? ""));
            }}
            disabled={!isTop || rootTasks.length === 0}
          >
            <option value="">{t("admin.select_root_task", "Select top task")}</option>
            {rootTasks.map((task) => (
              <option key={task.Id} value={task.Id}>
                {formatDisplayText(task.Name)}
              </option>
            ))}
          </select>
          <input
            value={estimateAssignedManhour}
            onChange={(e) => setEstimateAssignedManhour(e.currentTarget.value)}
            placeholder={t("admin.root_task_mh_placeholder", "MH (min)")}
            disabled={!isTop || rootTasks.length === 0}
          />
          <button
            className="btn primary"
            type="button"
            disabled={!isTop || !estimateRootTaskId || !Number.isInteger(Number(estimateAssignedManhour)) || Number(estimateAssignedManhour) <= 0}
            onClick={() =>
              onUpdateRootTaskAssignedManhour({
                taskId: estimateRootTaskId,
                assignedManhour: Number(estimateAssignedManhour),
              })
            }
          >
            {t("common.update", "Update")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_rework_link", "Rework Link")}</div>
        <div className="toolbar-row">
          <select value={reworkTaskId} onChange={(e) => setReworkTaskId(e.currentTarget.value)} disabled={!isTop}>
            <option value="">{t("admin.select_rework_task", "Select rework task")}</option>
            {reworkTasks.map((task) => (
              <option key={task.Id} value={task.Id}>
                {formatDisplayText(task.Name)}
              </option>
            ))}
          </select>
          <select value={preventedByTaskId} onChange={(e) => setPreventedByTaskId(e.currentTarget.value)} disabled={!isTop}>
            <option value="">{t("admin.select_prevented_by", "Select prevented_by")}</option>
            {preventedCandidates.map((task) => (
              <option key={task.Id} value={task.Id}>
                {formatDisplayText(task.Name)}
              </option>
            ))}
          </select>
          <button
            className="btn primary"
            type="button"
            disabled={!isTop || !reworkTaskId || !preventedByTaskId}
            onClick={() => onSetReworkLink({ reworkTaskId, preventedByTaskId })}
          >
            {t("admin.button_link", "Link")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("admin.title_infeasibility", "Infeasibility")}</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("admin.column_target", "Target")}</th>
                <th>{t("admin.column_status", "Status")}</th>
                <th>{t("admin.column_gap", "Gap")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(infeasibility ?? {}).map(([key, value]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{value.IsInfeasible ? t("admin.status_infeasible", "Infeasible") : t("admin.status_feasible", "OK")}</td>
                  <td>{value.GapMinutes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
