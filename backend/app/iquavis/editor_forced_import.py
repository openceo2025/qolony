"""xlsm(iQUAVIS)を正としてQOLONYへ強制取込するときの3値比較。

ここは純粋関数だけを置く。I/Oとトランザクションは `sync.py` 側が持つ。

3値の出どころ:

- `baseline` … QOLONYが保存した「前回iQUAVISと合意した値」(`IQuavisTaskBaselines`)。
  xlsmの非表示 `__TASKS_...` シートはiQUAVISから再取得するたびに最新値で上書きされるため、
  合意時点の値はxlsmからは復元できない。QOLONY側に持つ必要がある。
- `qolony`  … QOLONY DBの現在値。
- `iquavis` … xlsmの非表示 `__TASKS_...` シート = iQUAVIS取得/保存成功値。
  表示シートはQOLONYが黄色で書き込んだ下書きを含むため、iQUAVISの正としては使わない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.iquavis.editor_models import IquavisTaskSyncProperties


# iQUAVIS側にもQOLONY側にも同じ意味で存在する共有項目。
# 見積MH(assigned_minutes)、担当者、QOLONY側で行った親の付替えはQOLONYローカルの情報として扱い、
# 値が違っても競合として検出しない。
SHARED_SYNC_FIELDS: tuple[str, ...] = (
    "name",
    "start_date",
    "end_date",
    "actual_minutes",
    "status",
    "memo",
)

# QOLONYが完了状態・実績工数・見積工数・子タスクから算出する項目。
# 取込直後にQOLONYが再計算して上書きするため、比較しても意味がなく、競合にもしない。
QOLONY_DERIVED_FIELDS: tuple[str, ...] = ("progress_rate",)

# 子タスクを持つタスクではQOLONYが子から集計するため、共有項目から外す。
PARENT_DERIVED_FIELDS: tuple[str, ...] = ("actual_minutes", "status")

FIELD_SAME = "same"
FIELD_ADOPT_IQUAVIS = "adopt_iquavis"
FIELD_CONFLICT = "conflict"


@dataclass(frozen=True)
class ForcedFieldDecision:
    field: str
    baseline: Any
    qolony: Any
    iquavis: Any
    outcome: str


@dataclass(frozen=True)
class ForcedTaskDecision:
    task_id: str
    iquavis_id: str
    adopt_fields: tuple[ForcedFieldDecision, ...] = ()
    conflict_fields: tuple[ForcedFieldDecision, ...] = ()

    @property
    def is_conflict(self) -> bool:
        return bool(self.conflict_fields)

    @property
    def has_adoptions(self) -> bool:
        return bool(self.adopt_fields)


def json_value(value: Any) -> Any:
    """UIとAppState JSONで扱える形へ落とす。"""
    if isinstance(value, date):
        return value.isoformat()
    return value


def shared_field_names(*, is_parent: bool) -> tuple[str, ...]:
    """このタスクでiQUAVISとQOLONYが同じ意味を持つ項目を返す。"""
    excluded = set(QOLONY_DERIVED_FIELDS)
    if is_parent:
        excluded.update(PARENT_DERIVED_FIELDS)
    return tuple(name for name in SHARED_SYNC_FIELDS if name not in excluded)


def comparable_field_names(
    *,
    iquavis: IquavisTaskSyncProperties,
    is_parent: bool,
) -> tuple[str, ...]:
    """ブックに列が無い項目を比較対象から外す。"""
    names: list[str] = []
    for name in shared_field_names(is_parent=is_parent):
        if getattr(iquavis, name, None) is None:
            # 列が無いブックでは未対応として扱い、Noneとの疑似差分を作らない。
            continue
        names.append(name)
    return tuple(names)


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def classify_forced_import_task(
    *,
    task_id: str,
    iquavis_id: str,
    qolony: IquavisTaskSyncProperties,
    iquavis: IquavisTaskSyncProperties,
    baseline: IquavisTaskSyncProperties | None,
    is_parent: bool = False,
) -> ForcedTaskDecision:
    """1タスク分の3値比較を行う。

    競合は「baselineから見て両側がそれぞれ別の値へ変わった」場合だけ。
    片側だけの変更と、baselineが無い場合はiQUAVISを正として採用する。
    """
    adopt: list[ForcedFieldDecision] = []
    conflicts: list[ForcedFieldDecision] = []

    for name in comparable_field_names(iquavis=iquavis, is_parent=is_parent):
        qolony_value = _normalized(getattr(qolony, name))
        iquavis_value = _normalized(getattr(iquavis, name))
        baseline_value = _normalized(getattr(baseline, name)) if baseline is not None else None

        if qolony_value == iquavis_value:
            continue

        decision = ForcedFieldDecision(
            field=name,
            baseline=json_value(baseline_value),
            qolony=json_value(qolony_value),
            iquavis=json_value(iquavis_value),
            outcome=FIELD_ADOPT_IQUAVIS,
        )

        if baseline is None or baseline_value is None:
            # 合意記録が無い間はiQUAVISを正とする。ここで魚拓が作られる。
            adopt.append(decision)
        elif qolony_value == baseline_value:
            # iQUAVIS側だけが変わった。
            adopt.append(decision)
        elif iquavis_value == baseline_value:
            # QOLONY側だけが変わった。強制取込ではiQUAVISで上書きする。
            adopt.append(decision)
        else:
            conflicts.append(
                ForcedFieldDecision(
                    field=name,
                    baseline=decision.baseline,
                    qolony=decision.qolony,
                    iquavis=decision.iquavis,
                    outcome=FIELD_CONFLICT,
                )
            )

    if conflicts:
        # 競合が1項目でもあるタスクは部分適用せず、まるごと運用者の採択に回す。
        return ForcedTaskDecision(
            task_id=task_id,
            iquavis_id=iquavis_id,
            adopt_fields=(),
            conflict_fields=tuple(conflicts),
        )
    return ForcedTaskDecision(
        task_id=task_id,
        iquavis_id=iquavis_id,
        adopt_fields=tuple(adopt),
        conflict_fields=(),
    )
