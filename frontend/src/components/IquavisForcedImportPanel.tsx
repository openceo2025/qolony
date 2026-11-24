import {
  canApplyIquavisForcedImport,
  formatForcedImportFieldCounts,
  formatForcedImportPreviewSummary,
  getForcedImportFieldLabel,
  IQUAVIS_FORCED_IMPORT_BASELINE_NOTE,
  IQUAVIS_FORCED_IMPORT_GUIDANCE,
} from "../lib/iquavisForcedImport";
import type { IquavisForcedImportPreviewResponse } from "../types";

interface IquavisForcedImportPanelProps {
  isTopManager: boolean;
  preview: IquavisForcedImportPreviewResponse | null;
  busy: boolean;
  onPreview: () => void;
  onApply: () => void;
}

function formatValue(value: string | number | null): string {
  if (value === null || value === undefined || value === "") return "(未設定)";
  return String(value);
}

export function IquavisForcedImportPanel({
  isTopManager,
  preview,
  busy,
  onPreview,
  onApply,
}: IquavisForcedImportPanelProps): JSX.Element {
  const applicable = canApplyIquavisForcedImport(preview);

  return (
    <section className="panel iquavis-forced-import-panel" aria-labelledby="iquavis-forced-import-title">
      <div className="iquavis-authoritative-import-heading">
        <div>
          <div className="panel-title" id="iquavis-forced-import-title">
            xlsm(iQUAVIS)→QOLONY 強制同期
          </div>
          <div className="subtext">{IQUAVIS_FORCED_IMPORT_GUIDANCE}</div>
        </div>
        {preview ? (
          <span className={`iquavis-authoritative-import-state ${applicable ? "ready" : "blocked"}`} role="status">
            {applicable ? "実行可能" : "要確認"}
          </span>
        ) : null}
      </div>

      <ol className="iquavis-authoritative-import-steps">
        <li>Excelで対象プロジェクトを最新取得し、ブックを保存してExcelを完全に閉じます。</li>
        <li>「1. 事前確認」で、上書き件数と競合件数を確認します。</li>
        <li>「2. iQUAVISを正としてQOLONYへ取込」を実行します。競合はWBSなど各画面で採択します。</li>
      </ol>

      <div className="toolbar-row iquavis-authoritative-import-actions">
        <button className="btn" type="button" disabled={!isTopManager || busy} onClick={onPreview}>
          {busy ? "処理中..." : "1. 事前確認"}
        </button>
        <button
          className="btn primary"
          type="button"
          disabled={!isTopManager || busy || !applicable}
          onClick={onApply}
        >
          2. iQUAVISを正としてQOLONYへ取込
        </button>
      </div>

      {!isTopManager ? <div className="subtext">トップマネージャー以外は操作できません。</div> : null}

      {preview ? (
        <div className="iquavis-authoritative-import-summary" aria-live="polite">
          <div className="iquavis-forced-import-headline">{formatForcedImportPreviewSummary(preview)}</div>

          <dl className="iquavis-authoritative-import-metrics">
            <div><dt>ブック名</dt><dd>{preview.workbook_name || "N/A"}</dd></div>
            <div><dt>評価件数</dt><dd>{preview.scanned_task_count}</dd></div>
            <div><dt>iQUAVIS新規</dt><dd>{preview.new_task_count}</dd></div>
            <div><dt>iQUAVIS値で上書き</dt><dd>{preview.updated_task_count}</dd></div>
            <div><dt>変更なし</dt><dd>{preview.unchanged_task_count}</dd></div>
            <div><dt>競合として保留</dt><dd>{preview.conflict_task_count}</dd></div>
            <div><dt>QOLONY専有（保持）</dt><dd>{preview.local_only_task_count}</dd></div>
            <div><dt>同期対象外（除外）</dt><dd>{preview.excluded_record_count}</dd></div>
            <div><dt>合意記録なし</dt><dd>{preview.missing_qolony_baseline_count}</dd></div>
            <div><dt>取得成功値なし</dt><dd>{preview.missing_workbook_baseline_count}</dd></div>
            <div><dt>重複ローカルリンク</dt><dd>{preview.duplicate_local_link_count}</dd></div>
          </dl>

          <div className="iquavis-authoritative-import-category-notes">
            <p><strong>上書き対象の項目:</strong> {formatForcedImportFieldCounts(preview.adopted_field_counts)}</p>
            <p><strong>競合の項目:</strong> {formatForcedImportFieldCounts(preview.conflict_field_counts)}</p>
          </div>

          {preview.missing_qolony_baseline_count > 0 ? (
            <div className="iquavis-authoritative-import-safety-note">
              <strong>合意記録（魚拓）について</strong>
              <p>{IQUAVIS_FORCED_IMPORT_BASELINE_NOTE}</p>
            </div>
          ) : null}

          {preview.conflict_details.length > 0 ? (
            <details className="iquavis-authoritative-import-details" open>
              <summary>競合として保留するタスク: {preview.conflict_task_count} 件</summary>
              <p>これらはこの取込では変更しません。WBS・Today・ガントなどでタスクを選び、採択してください。</p>
              <ul>
                {preview.conflict_details.map((detail) => (
                  <li key={detail.task_id}>
                    <code>{detail.iquavis_id}</code>
                    <ul>
                      {detail.fields.map((item) => (
                        <li key={item.field}>
                          {getForcedImportFieldLabel(item.field)}: 基準 {formatValue(item.baseline)} / QOLONY{" "}
                          {formatValue(item.qolony)} / iQUAVIS {formatValue(item.iquavis)}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          <div className="iquavis-authoritative-import-fingerprint">
            ブック指紋: <code>{preview.workbook_fingerprint || "N/A"}</code>
          </div>
        </div>
      ) : (
        <div className="subtext">事前確認を実行すると、上書き件数と競合件数を確認できます。</div>
      )}
    </section>
  );
}
