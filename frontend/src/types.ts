export type TabKey =
  | "wbs"
  | "gantt"
  | "colony"
  | "my"
  | "today"
  | "split"
  | "detail"
  | "members"
  | "progress"
  | "ranking"
  | "admin";

export interface User {
  Id: string;
  Name: string;
  Nickname: string;
  FullName: string;
  LoginId: string;
  IQuavisUserId: string | null;
  IsTopManager: boolean;
  IsActive: boolean;
  FreeTimeRate: number;
  /** パスワードが設定されているか。値そのものは画面へ送らない（照合は /api/auth/verify）。 */
  HasPassword: boolean;
  CreateDate: string | null;
}

export interface Project {
  Id: string;
  Name: string;
  StartDate: string;
  EndDate: string;
  CrashZonePct: number;
  CreateDate: string | null;
}

export interface SplitTemplateRow {
  task_name: string;
  ratio: number;
}

export interface SplitTemplate {
  title: string;
  rows: SplitTemplateRow[];
}

export interface UiLocale {
  language: string;
  messages: Record<string, unknown>;
  fallback_messages: Record<string, unknown>;
}

export interface MetaResponse {
  users: User[];
  top_manager_id: string;
  project: Project;
  split_templates: SplitTemplate[];
  has_matplotlib: boolean;
  ui: UiLocale;
  /** 任意の iQUAVIS 連携が設定されているか。古いサーバーは返さないので未定義は「使う」として扱う。 */
  iquavis?: { available: boolean };
}

export interface BulkTaskAssigneeRowResult {
  task_id: string;
  assignee_user_id: string;
  success: boolean;
  changed: boolean;
  message: string;
  revision?: string | null;
}

export interface BulkTaskAssigneeResponse {
  status: "ok" | "partial";
  changed: number;
  failed: number;
  results: BulkTaskAssigneeRowResult[];
}

export interface BulkTaskFieldsRowResult {
  task_id: string;
  success: boolean;
  changed: boolean;
  message: string;
  warnings: string[];
  revision?: string | null;
  code?: "task_conflict" | string;
  current?: {
    name: string;
    assigned_manhour: number;
    memo: string;
    end_date: string;
    assignee_user_id: string;
  };
}

export interface BulkTaskFieldsResponse {
  status: "ok" | "partial";
  changed: number;
  failed: number;
  results: BulkTaskFieldsRowResult[];
  parent_revision?: string | null;
}

export interface SyncStatus {
  message: string;
  is_error: boolean;
  recorded_at: string | null;
}

export type IquavisConflictDirection = "qolony_to_iquavis" | "iquavis_to_qolony";

export type IquavisConflictSyncState = "Conflict" | "PendingIquavisSave" | "ConflictHold";

export interface IquavisConflictField {
  field: string;
  baseline: unknown;
  qolony: unknown;
  excel: unknown;
}

export interface IquavisConflictRecord {
  task_id: string;
  iquavis_id: string | null;
  task_name: string;
  sync_state: IquavisConflictSyncState | string;
  message: string;
  recorded_at: string | null;
  workbook_fingerprint: string | null;
  fields: IquavisConflictField[];
}

export interface IquavisConflictListResponse {
  items: IquavisConflictRecord[];
  count: number;
}

export interface IquavisConflictResolveResponse {
  status: string;
  task_id: string;
  direction: IquavisConflictDirection;
  sync_state: "ConflictHold" | "PendingIquavisSave" | "Linked" | string;
  recorded_at: string | null;
}

export interface IquavisConflictHoldResponse {
  status: string;
  task_id: string;
  sync_state: "ConflictHold" | "Conflict" | "Linked" | string;
  recorded_at: string | null;
  local_updates_written?: number;
  remote_updates_applied?: number;
  imported_count?: number;
}

export interface IquavisConflictRefreshResponse {
  status: string;
  conflict_count: number;
  hold_count: number;
  pending_count: number;
  local_updates_written: number;
  remote_updates_applied: number;
  imported_count: number;
}

export interface IquavisAuthoritativeImportPreviewResponse {
  ready: boolean;
  workbook_fingerprint: string;
  workbook_name: string;
  scanned_task_count: number;
  /** iQUAVIS IDが空の行は初期整合の対象外として除外されます。 */
  ignored_blank_record_count?: number;
  existing_task_count: number;
  new_task_count: number;
  /** iQUAVIS側だけにあり、QOLONYへ新しく取り込むタスクドメイン。旧APIでは未返却。 */
  new_domain_count?: number;
  changed_task_count: number;
  changed_field_count: number;
  /** QOLONY側だけにあるローカル専用タスク。初期整合では削除しない。 */
  local_only_task_count?: number;
  /** 同期対象外のため、初期整合で触らないタスク。 */
  excluded_task_count?: number;
  /** iQUAVIS側と異なっていても維持するQOLONY独自の親指定。 */
  preserved_local_parent_count?: number;
  /** 同じiQUAVIS IDのExcel表示値と取得履歴の差。IDの存在差ではない。 */
  excel_snapshot_difference_count?: number;
  excel_snapshot_difference_details?: IquavisAuthoritativeSnapshotDifferenceDetail[];
  /** 前回取得・保存成功値がなく、安全な自動判定ができないタスク。 */
  missing_baseline_task_count?: number;
  missing_baseline_details?: IquavisAuthoritativeTaskDetail[];
  /** 互換用。新APIではexcel_snapshot_difference_countを使用する。 */
  dirty_excel_task_count: number;
  status_breakdown?: Record<string, number> | IquavisAuthoritativeStatusBreakdownItem[];
  unknown_status_count?: number;
  unknown_status_details?: IquavisAuthoritativeUnknownStatusDetail[];
  /** Excel内で同じiQUAVIS IDが複数行に現れる致命的な識別子重複。 */
  duplicate_iquavis_id_count?: number;
  /** 複数のQOLONYタスクが同じiQUAVIS IDへリンクしている致命的な重複。 */
  duplicate_local_link_count?: number;
  duplicate_local_link_ids?: string[];
  /** 互換用。新APIではunknown_status_countを使用する。 */
  unsupported_status_count: number;
  structure_error_count?: number;
  structure_error_details?: IquavisAuthoritativeStructureError[];
  review_items?: IquavisAuthoritativeReviewItem[];
  blockers: string[];
  warnings: string[];
}

/** 選択タスク配下の担当者を一括変更した結果。 */
export interface TaskSubtreeAssigneeResult {
  status: string;
  root_task_id: string;
  root_task_name: string;
  assignee_user_id: string;
  assignee_name: string;
  /** 対象になったタスク数（選択タスク＋配下すべて）。 */
  scanned: number;
  changed: number;
  /** 既に指定担当者だったタスク。 */
  unchanged: number;
  /** 完了タスクは誰が実行しても書き換えない。 */
  skipped_completed: number;
  skipped_aborted: number;
  /** 一般ユーザーが実行したとき、自分が担当ではないため触れなかったタスク。 */
  skipped_not_owned: number;
  is_top_manager: boolean;
}

/** サーバー側の現在値。QOLONY利用者どうしの同時編集が競合したときに返る。 */
export interface TaskEditConflictServerValues {
  Name: string;
  Status: string;
  ProgressRate: number;
  StartDate: string;
  EndDate: string;
  AssignedManHour: number;
  ActualManHour: number;
  Memo: string;
  AssigneeUserId: string | null;
  AssigneeName: string | null;
  TodayOverride: boolean | null;
}

/** 409 task_conflict のときにサーバーが添える、相手の変更内容。 */
export interface TaskEditConflictDetail {
  task_id: string;
  task_name?: string;
  current_revision?: string;
  updated_by_user_id?: string | null;
  updated_by_user_name?: string | null;
  updated_at?: string | null;
  server?: TaskEditConflictServerValues;
}

/** xlsm(iQUAVIS)を正としてQOLONYへ強制取込するときの事前確認結果。 */
export interface IquavisForcedImportPreviewResponse {
  workbook_name: string;
  workbook_fingerprint: string;
  /** 同期対象として評価したタスク数（新規取込予定を含む）。 */
  scanned_task_count: number;
  /** iQUAVIS側だけにあり、QOLONYへ新規取込するタスク。 */
  new_task_count: number;
  /** iQUAVIS値で上書きするタスク。 */
  updated_task_count: number;
  /** 既に一致しているタスク。 */
  unchanged_task_count: number;
  /** 両側が別々に変更されたため取込対象から外すタスク。 */
  conflict_task_count: number;
  /** QOLONY側だけにあるタスク。削除しない。 */
  local_only_task_count: number;
  /** 同期対象外に指定されているため評価しなかった行。 */
  excluded_record_count: number;
  /** QOLONY側にまだ合意記録（魚拓）が無いタスク。初回はiQUAVISを正とする。 */
  missing_qolony_baseline_count: number;
  /** xlsmの非表示シートに取得成功値が無い行。 */
  missing_workbook_baseline_count: number;
  duplicate_local_link_count: number;
  adopted_field_counts: Record<string, number>;
  conflict_field_counts: Record<string, number>;
  conflict_details: IquavisForcedImportConflictDetail[];
  confirmation: string;
}

export interface IquavisForcedImportConflictDetail {
  iquavis_id: string;
  task_id: string;
  fields: {
    field: string;
    baseline: string | number | null;
    qolony: string | number | null;
    iquavis: string | number | null;
  }[];
}

export interface IquavisForcedImportApplyResponse {
  status: string;
  imported_task_count: number;
  imported_domain_count: number;
  updated_task_count: number;
  conflict_task_count: number;
  baseline_task_count: number;
  cleared_state_count: number;
  local_only_task_count: number;
  backup_path: string;
  workbook_fingerprint: string;
}

export interface IquavisAuthoritativeStructureError {
  iquavis_id: string;
  kind: string;
  reason: string;
}

export interface IquavisAuthoritativeSnapshotDifferenceDetail {
  iquavis_id: string;
  task_name?: string;
  fields?: string[];
}

export interface IquavisAuthoritativeTaskDetail {
  iquavis_id: string;
  task_name?: string;
}

export interface IquavisAuthoritativeReviewItem {
  iquavis_id: string;
  task_name?: string | null;
  kind: string;
  reason: string;
  review_item_key?: string | null;
}

export interface IquavisAuthoritativeStatusBreakdownItem {
  status: string;
  count: number;
}

export interface IquavisAuthoritativeUnknownStatusDetail {
  iquavis_id: string;
  task_name?: string;
  status: string;
}

export interface IquavisAuthoritativeImportApplyResponse {
  status: string;
  imported_task_count: number;
  imported_domain_count: number;
  updated_task_count: number;
  cleared_state_count: number;
  derived_progress_mismatch_count: number;
  backup_path?: string;
  warnings: string[];
}

export type ChildDependencyMode = "Serial" | "Parallel";
export interface Deliverable {
  title: string;
  url: string;
}

export interface Task {
  Id: string;
  ParentTaskId: string | null;
  Name: string;
  AssigneeUserId: string;
  AssigneeName: string;
  CreatorUserId: string;
  StartDate: string;
  EndDate: string;
  CreateDate: string;
  Revision: string;
  AssignedManHour: number;
  ActualManHour: number;
  ChildAssignedManHour: number;
  EstimateBalanceManHour: number;
  EstimateSignal: "None" | "Weak" | "Medium" | "Strong" | string;
  EstimateSignalMessage: string | null;
  ProgressRate: number;
  Signal: "Blue" | "Green" | "Yellow" | "Red" | string;
  Status: "NotStart" | "DuringStart" | "Completion" | "Abort" | string;
  Type: string;
  IsRework: boolean;
  PreventedByTaskId: string | null;
  Memo: string;
  TodayOverride: boolean | null;
  TodaySortOrder: number | null;
  WbsSortOrder: number | null;
  ChildDependencyMode: ChildDependencyMode | string;
  IQuavisTaskId: string | null;
  IQuavisProjectId: string | null;
  IQuavisSyncState: "Unlinked" | "Linked" | "Conflict" | "ConflictHold" | "PendingIquavisSave" | "Excluded" | string;
  IQuavisSyncExcluded: boolean;
  IQuavisConflictMessage: string | null;
  PlannedDeliverables: Deliverable[];
  OutputDeliverables: Deliverable[];
}

export interface ActiveTimer {
  TimerId: string;
  TaskId: string;
  TaskName: string;
  StartedAt: string;
  IsOjt: boolean;
  TeacherUserId: string | null;
}

export interface DailyBudget {
  RegularMinutes: number;
  OvertimeMinutes: number;
  FreeMinutes: number;
  BaseAvailableMinutes: number;
  EducationBudgetMinutes: number;
  WorkBudgetMinutes: number;
}

export interface TodaySummary {
  WorkMinutes: number;
  Budget: DailyBudget;
  ActualWorkMinutes: number;
  OjtMinutes: number;
  RemainingWorkMinutes: number;
  ActiveTimer: ActiveTimer | null;
  Alerts: string[];
}

export interface MemberDayConfig {
  UserId: string;
  WorkDate: string;
  IsVacation: boolean;
  LeaveType: string;
  CommitRateOverride: number | null;
  Note: string;
}

export interface MemberProfile {
  UserId: string;
  DefaultCommitRate: number;
  ConditionIcon: string;
  FormationRole: string;
  FormationPosition: string;
  FormationX: number;
  FormationY: number;
  DisplayOrder: number;
  TitleTags: string[];
  SpecialAbilities: string[];
}

export interface MemberView {
  UserId: string;
  Name: string;
  Nickname: string;
  FullName: string;
  LoginId: string;
  IQuavisUserId: string | null;
  IsTopManager: boolean;
  IsActive: boolean;
  FreeTimeRate: number;
  Profile: MemberProfile;
  DayConfigs: MemberDayConfig[];
}

export interface ProgressSnapshot {
  RecordedAt: string;
  ProgressRate: number;
}

export interface SpeedResult {
  VActual: number;
  VCritical: number;
  VSafe: number;
  Signal: "Blue" | "Yellow" | "Red" | string;
}

export interface ProgressDashboard {
  ProgressRate: number;
  Speed: SpeedResult;
  History: ProgressSnapshot[];
  ProjectEndDate: string;
  CrashZonePct: number;
}

export interface TaskProgressPoint {
  Date: string;
  ProgressRate: number;
  PlannedProgressRate: number;
  ActualCumMinutes: number;
}

export interface TaskSpeedPoint {
  Date: string;
  VActual: number;
  VAverage: number;
  VCritical: number;
  VSafe: number;
}

export interface TaskProgressDashboard {
  TaskId: string;
  TaskName: string;
  Signal: "Blue" | "Yellow" | "Red" | string;
  CrashZonePct: number;
  AssignedTotalMinutes: number;
  ProgressPoints: TaskProgressPoint[];
  SpeedPoints: TaskSpeedPoint[];
}

export interface RankItem {
  Rank: number;
  UserId: string;
  UserName: string;
  Value: number;
  IsPublished: boolean;
  IsSelf: boolean;
}

export interface Rankings {
  OutputVolume: RankItem[];
  WorkSpeed: RankItem[];
  EducationEfficiency: RankItem[];
  TeachingTime: RankItem[];
}

export interface RankingHistoryPoint {
  Date: string;
  Value: number;
}

export interface RankingHistorySeries {
  UserId: string;
  UserName: string;
  IsSelf: boolean;
  Points: RankingHistoryPoint[];
}

export interface RankingHistoryResponse {
  OutputVolume: RankingHistorySeries[];
  WorkSpeed: RankingHistorySeries[];
  EducationEfficiency: RankingHistorySeries[];
  TeachingTime: RankingHistorySeries[];
}

export interface InfeasibilityCause {
  TaskId: string;
  TaskName: string;
  DueDate: string;
  RemainingAssignedManHour: number;
}

export interface InfeasibilityItem {
  IsInfeasible: boolean;
  GapMinutes: number;
  Causes: InfeasibilityCause[];
}

export type InfeasibilityResponse = Record<string, InfeasibilityItem>;

export interface SplitRowInput {
  taskName: string;
  mhRatio: string;
  memo: string;
  dueDate: string;
}

export interface SplitChildPayload {
  ExistingTaskId?: string;
  Name: string;
  AssignedManHour: number;
  ActualManHour: number;
  EndDate: string;
  Memo: string;
  AssigneeUserId: string;
}



