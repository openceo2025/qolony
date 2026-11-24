import { useEffect, useMemo, useState } from "react";
import {
  canApplyIquavisAuthoritativeImport,
  formatIquavisDifferenceFields,
  getIquavisSnapshotDifferenceCount,
  getIquavisUnknownStatusCount,
  IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE,
  IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE,
  normalizeIquavisStatusBreakdown,
} from "../lib/iquavisAuthoritativeImport";
import type { IquavisAuthoritativeImportPreviewResponse } from "../types";

interface IquavisAuthoritativeImportPanelProps {
  isTopManager: boolean;
  preview: IquavisAuthoritativeImportPreviewResponse | null;
  busy: boolean;
  onPreview: (ignoredReviewItemKeys: string[]) => void;
  onApply: (ignoredReviewItemKeys: string[]) => void;
}

function displayCount(value: number | undefined): number | string {
  return value ?? "N/A";
}

function toSortKey(kind: string): number {
  const rank: Record<string, number> = {
    unknown_status: 0,
    invalid_schedule: 1,
    missing_baseline: 2,
    duplicate_remote_record: 3,
    duplicate_local_link: 4,
    "structure:task": 5,
    "structure:task_domain": 6,
    "structure:qolony_task": 7,
    "structure:unknown": 8,
  };
  return rank[kind] ?? 99;
}

export function IquavisAuthoritativeImportPanel({
  isTopManager,
  preview,
  busy,
  onPreview,
  onApply,
}: IquavisAuthoritativeImportPanelProps): JSX.Element {
  const [safetyAcknowledged, setSafetyAcknowledged] = useState(false);
  const [ignoredReviewItemKeys, setIgnoredReviewItemKeys] = useState<string[]>([]);

  useEffect(() => {
    setSafetyAcknowledged(false);
  }, [preview]);

  const reviewItems = useMemo(() => {
    const sourceItems = (preview?.review_items ??
      preview?.structure_error_details?.map((error) => ({
        kind: `structure:${error.kind}`,
        iquavis_id: error.iquavis_id,
        task_name: null,
        reason: error.reason,
      })) ??
      []);
    return sourceItems
      .map((item) => ({
        ...item,
        iquavis_id: String(item.iquavis_id ?? "").trim(),
        review_item_key: "review_item_key" in item ? item.review_item_key : null,
      }))
      .map((item) => ({
        ...item,
        review_item_key:
          item.review_item_key ??
          (item.iquavis_id ? `id:${String(item.iquavis_id).trim()}` : null),
      }))
      .filter((item) => item.review_item_key && item.review_item_key.length > 0)
      .sort((left, right) => {
        const kindSort = toSortKey(left.kind) - toSortKey(right.kind);
        if (kindSort !== 0) return kindSort;
        return left.iquavis_id.localeCompare(right.iquavis_id);
      });
  }, [preview]);

  const availableIgnoredItemKeys = useMemo(
    () => new Set(reviewItems.map((item) => item.review_item_key).filter((item): item is string => Boolean(item))),
    [reviewItems]
  );
  useEffect(() => {
    if (!preview?.review_items && !preview?.structure_error_details) {
      setIgnoredReviewItemKeys([]);
      return;
    }
    setIgnoredReviewItemKeys((current) =>
      current.filter((itemKey) => availableIgnoredItemKeys.has(itemKey))
    );
  }, [availableIgnoredItemKeys, preview?.review_items, preview?.structure_error_details]);

  const toggleIgnored = (reviewItemKey: string, checked: boolean) => {
    setIgnoredReviewItemKeys((current) =>
      checked ? [...new Set([...current, reviewItemKey])] : current.filter((value) => value !== reviewItemKey)
    );
  };

  const statusBreakdown = useMemo(
    () => (preview ? normalizeIquavisStatusBreakdown(preview) : []),
    [preview]
  );
  const applicable = canApplyIquavisAuthoritativeImport(preview);
  const snapshotDifferenceCount = preview ? getIquavisSnapshotDifferenceCount(preview) : 0;
  const unknownStatusCount = preview ? getIquavisUnknownStatusCount(preview) : 0;
  const missingBaselineCount = preview?.missing_baseline_task_count;
  const legacyDifferenceCount = preview ? preview.excel_snapshot_difference_count === undefined : false;

  const structureErrorCount = preview?.review_items?.length ?? reviewItems.length;

  return (
    <section
      className="panel iquavis-authoritative-import-panel"
      aria-labelledby="iquavis-authoritative-import-title"
    >
      <div className="iquavis-authoritative-import-heading">
        <div>
          <div className="panel-title" id="iquavis-authoritative-import-title">
            iQUAVIS→QOLONY 初回整合
          </div>
          <div className="subtext">
            「Excel上の表示値」ではなく、iQUAVIS最新値を正としてQOLONYへ反映します。
            競合がある場合は確認対象を除外して事前確認を再実行してください。
          </div>
        </div>
        {preview ? (
          <span
            className={`iquavis-authoritative-import-state ${applicable ? "ready" : "blocked"}`}
            role="status"
          >
            {applicable ? "実行可能" : "保留中"}
          </span>
        ) : null}
      </div>

      <ol className="iquavis-authoritative-import-steps">
        <li>iQUAVISブックを開いたまま、最新タスク情報を取得します。</li>
        <li>1で事前確認を実行し、QOLONY側へ反映可能な状態を確認します。</li>
        <li>問題があれば除外対象を選び、再度事前確認してから適用します。</li>
      </ol>

      <div className="toolbar-row iquavis-authoritative-import-actions">
        <button
          className="btn"
          type="button"
          disabled={!isTopManager || busy}
          onClick={() => onPreview(ignoredReviewItemKeys)}
        >
          {busy ? "確認中..." : "1. 事前確認"}
        </button>
        <button
          className="btn primary"
          type="button"
          disabled={!isTopManager || busy || !applicable || !safetyAcknowledged}
          onClick={() => onApply(ignoredReviewItemKeys)}
        >
          2. iQUAVIS最新値をQOLONYへ反映
        </button>
      </div>

      {!isTopManager ? (
        <div className="subtext">トップマネージャー以外は操作できません。</div>
      ) : null}

      {preview ? (
        <div className="iquavis-authoritative-import-summary" aria-live="polite">
          <dl className="iquavis-authoritative-import-metrics">
            <div><dt>ブック名</dt><dd>{preview.workbook_name || "N/A"}</dd></div>
            <div><dt>スキャン件数</dt><dd>{preview.scanned_task_count}</dd></div>
            <div><dt>ID空行</dt><dd>{displayCount(preview.ignored_blank_record_count)}</dd></div>
            <div><dt>iQUAVIS新規</dt><dd>{preview.new_task_count}</dd></div>
            <div><dt>iQUAVISドメイン新規</dt><dd>{displayCount(preview.new_domain_count)}</dd></div>
            <div><dt>QOLONY専有のみ</dt><dd>{displayCount(preview.local_only_task_count)}</dd></div>
            <div><dt>変更対象</dt><dd>{preview.changed_task_count}</dd></div>
            <div><dt>変更フィールド</dt><dd>{preview.changed_field_count}</dd></div>
            <div><dt>除外対象</dt><dd>{displayCount(preview.excluded_task_count)}</dd></div>
            <div><dt>ローカル親を維持</dt><dd>{displayCount(preview.preserved_local_parent_count)}</dd></div>
            <div>
              <dt>{legacyDifferenceCount ? "Excel差分(旧項目)" : "Excel表示値差分"}</dt>
              <dd>{snapshotDifferenceCount}</dd>
            </div>
            <div><dt>前回保存値なし</dt><dd>{displayCount(missingBaselineCount)}</dd></div>
            <div><dt>未知ステータス</dt><dd>{unknownStatusCount}</dd></div>
            <div><dt>重複iQUAVIS ID</dt><dd>{displayCount(preview.duplicate_iquavis_id_count)}</dd></div>
            <div><dt>重複ローカルリンク</dt><dd>{displayCount(preview.duplicate_local_link_count)}</dd></div>
            <div><dt>構造エラー</dt><dd>{structureErrorCount}</dd></div>
          </dl>

          <div className="iquavis-authoritative-import-category-notes">
            <p><strong>iQUAVIS新規:</strong> QOLONYに存在しないため、対象がなければ新規追加します。</p>
            <p><strong>QOLONY専有:</strong> iQUAVIS側に無いタスクは消さず残します。</p>
            <p><strong>変更:</strong> iQUAVIS最新値で差分があるものだけ更新対象になります。</p>
          </div>

          <div className="iquavis-authoritative-import-safety-note">
            <strong>実行の注意</strong>
            <p>{IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE}</p>
          </div>

          {statusBreakdown.length > 0 ? (
            <details className="iquavis-authoritative-import-details">
              <summary>iQUAVISステータス内訳: {statusBreakdown.reduce((sum, item) => sum + item.count, 0)} 件</summary>
              <div className="iquavis-authoritative-status-breakdown">
                {statusBreakdown.map((item) => (
                  <span key={item.status}><strong>{item.status}</strong> {item.count}件</span>
                ))}
              </div>
            </details>
          ) : null}

          {snapshotDifferenceCount > 0 ? (
            <details className="iquavis-authoritative-import-details">
              <summary>Excel表示値差分: {snapshotDifferenceCount} 件</summary>
              <p>{IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE}</p>
              {preview.excel_snapshot_difference_details?.length ? (
                <ul>
                  {preview.excel_snapshot_difference_details.map((item) => (
                    <li key={item.iquavis_id}>
                      <code>{item.iquavis_id}</code> {item.task_name || "未設定"} -{" "}
                      {formatIquavisDifferenceFields(item.fields)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>詳細はサーバー側で省略されたためIDのみ表示します。</p>
              )}
            </details>
          ) : null}

          {(missingBaselineCount ?? 0) > 0 ? (
            <details className="iquavis-authoritative-import-details">
              <summary>保存値なし: {missingBaselineCount} 件</summary>
              <p>前回保存値がないため、初回整合の対象外として止まる可能性があります。</p>
              {preview.missing_baseline_details?.length ? (
                <ul>
                  {preview.missing_baseline_details.map((item) => (
                    <li key={item.iquavis_id}><code>{item.iquavis_id}</code> {item.task_name || "未設定"}</li>
                  ))}
                </ul>
              ) : null}
            </details>
          ) : null}

          {unknownStatusCount > 0 ? (
            <details className="iquavis-authoritative-import-details" open>
              <summary>未知ステータス: {unknownStatusCount} 件</summary>
              <p>未知ステータスはまず原因を確認してください。</p>
              {preview.unknown_status_details?.length ? (
                <ul>
                  {preview.unknown_status_details.map((item) => (
                    <li key={item.iquavis_id}>
                      <code>{item.iquavis_id}</code> {item.task_name || "未設定"} - {item.status || "未設定"}
                    </li>
                  ))}
                </ul>
              ) : null}
            </details>
          ) : null}

          {reviewItems.length > 0 ? (
            <details className="iquavis-authoritative-import-structure-errors" open>
              <summary>構造エラー（個別に除外して再確認可能）: {structureErrorCount} 件</summary>
              <div className="iquavis-authoritative-import-structure-list">
                {reviewItems.map((error) => (
                  <label
                    key={`${error.review_item_key}:${error.kind}:${error.iquavis_id}`}
                    className="iquavis-authoritative-import-structure-item"
                  >
                    <input
                      type="checkbox"
                      checked={ignoredReviewItemKeys.includes(error.review_item_key ?? "")}
                      onChange={(event) =>
                        error.review_item_key
                          ? toggleIgnored(error.review_item_key, event.currentTarget.checked)
                          : undefined
                      }
                    />
                    <span>
                      <code>{error.iquavis_id || "(IDなし)"}</code> [{error.kind}] {error.reason}
                    </span>
                  </label>
                ))}
              </div>
              <p>チェックした項目は当該事前確認で除外し、QOLONY側を更新しません。</p>
            </details>
          ) : null}

          <div className="iquavis-authoritative-import-fingerprint">
            ブック指紋: <code>{preview.workbook_fingerprint || "N/A"}</code>
          </div>

          {preview.blockers.length > 0 ? (
            <div className="iquavis-authoritative-import-notices blocked" role="alert">
              <strong>事前確認で停止中:</strong>
              <ul>
                {preview.blockers.map((blocker, index) => <li key={`${index}-${blocker}`}>{blocker}</li>)}
              </ul>
            </div>
          ) : null}
          {preview.warnings.length > 0 ? (
            <div className="iquavis-authoritative-import-notices warning">
              <strong>注意:</strong>
              <ul>
                {preview.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
              </ul>
            </div>
          ) : null}

          {applicable ? (
            <div className="iquavis-authoritative-import-final-actions">
              <label className="iquavis-authoritative-import-acknowledgement">
                <input
                  type="checkbox"
                  checked={safetyAcknowledged}
                  onChange={(event) => setSafetyAcknowledged(event.currentTarget.checked)}
                />
                <span>上記内容を確認し、QOLONYへ反映して問題ないことを確認しました。</span>
              </label>
            </div>
          ) : (
            <div className="subtext">事前確認で停止中です。除外対象を選んで再確認してください。</div>
          )}
        </div>
      ) : (
        <div className="subtext">事前確認を実行してから、差分詳細と適用対象を確認します。</div>
      )}
    </section>
  );
}
