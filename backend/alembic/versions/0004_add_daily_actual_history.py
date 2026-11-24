"""add daily actual history tables

Revision ID: 0004_add_daily_actual_history
Revises: 0003_add_task_memo
Create Date: 2026-02-15 00:00:00

"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
import yaml


# revision identifiers, used by Alembic.
revision: str = "0004_add_daily_actual_history"
down_revision: Union[str, None] = "0003_add_task_memo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _parse_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"invalid date value: {value!r}")


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return datetime.fromisoformat(text[:19])
    raise TypeError(f"invalid datetime value: {value!r}")


def _load_holidays() -> set[date]:
    backend_dir = Path(__file__).resolve().parents[2]
    calendar_path = backend_dir / "config" / "calendar.yaml"
    if not calendar_path.exists():
        return set()

    raw = yaml.safe_load(calendar_path.read_text(encoding="utf-8")) or {}
    result: set[date] = set()
    for item in raw.get("holidays", []):
        try:
            result.add(_parse_date(item))
        except Exception:
            continue
    return result


def _iter_workdays(start_date: date, end_date: date, holidays: set[date]) -> list[date]:
    if end_date < start_date:
        return []
    cursor = start_date
    days: list[date] = []
    while cursor <= end_date:
        if cursor.weekday() < 5 and cursor not in holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def upgrade() -> None:
    op.add_column("Tasks", sa.Column("ActualStartDate", sa.Date(), nullable=True))

    op.create_table(
        "RawWorklogs",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("UserId", sa.String(length=36), nullable=False),
        sa.Column("TaskId", sa.String(length=36), nullable=False),
        sa.Column("SourceTimerId", sa.String(length=36), nullable=True),
        sa.Column("WorkDate", sa.Date(), nullable=False),
        sa.Column("StartAt", sa.DateTime(), nullable=False),
        sa.Column("EndAt", sa.DateTime(), nullable=False),
        sa.Column("DurationSeconds", sa.Float(), nullable=False),
        sa.Column("IsOjt", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("TeacherUserId", sa.String(length=36), nullable=True),
        sa.Column(
            "CreateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["SourceTimerId"], ["TimerSessions.Id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["TeacherUserId"], ["Users.Id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
    )

    op.create_table(
        "DailyTaskActuals",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("ProjectId", sa.String(length=36), nullable=False),
        sa.Column("UserId", sa.String(length=36), nullable=False),
        sa.Column("TaskId", sa.String(length=36), nullable=False),
        sa.Column("WorkDate", sa.Date(), nullable=False),
        sa.Column("TimerMinutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("DistributedMinutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("TotalMinutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "UpdateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["ProjectId"], ["Projects.Id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
        sa.UniqueConstraint("UserId", "TaskId", "WorkDate", name="uq_daily_task_actual"),
    )

    op.create_table(
        "DailyProgressSnapshots",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("ProjectId", sa.String(length=36), nullable=False),
        sa.Column("TaskId", sa.String(length=36), nullable=False),
        sa.Column("SnapshotDate", sa.Date(), nullable=False),
        sa.Column("ProgressRate", sa.Float(), nullable=False),
        sa.Column("PlannedProgressRate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("CumActualMinutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("VActual", sa.Float(), nullable=False, server_default="0"),
        sa.Column("VCritical", sa.Float(), nullable=False, server_default="0"),
        sa.Column("VSafe", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "CreateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["ProjectId"], ["Projects.Id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
        sa.UniqueConstraint("TaskId", "SnapshotDate", name="uq_daily_progress_snapshot"),
    )

    bind = op.get_bind()
    holidays = _load_holidays()

    metadata = sa.MetaData()
    tasks = sa.Table(
        "Tasks",
        metadata,
        sa.Column("Id", sa.String(length=36)),
        sa.Column("ProjectId", sa.String(length=36)),
        sa.Column("AssigneeUserId", sa.String(length=36)),
        sa.Column("Status", sa.String(length=16)),
        sa.Column("EndDate", sa.Date()),
        sa.Column("CompletedAt", sa.DateTime()),
        sa.Column("ActualManHour", sa.Integer()),
        sa.Column("ActualStartDate", sa.Date()),
    )
    timers = sa.Table(
        "TimerSessions",
        metadata,
        sa.Column("Id", sa.String(length=36)),
        sa.Column("UserId", sa.String(length=36)),
        sa.Column("TaskId", sa.String(length=36)),
        sa.Column("StartAt", sa.DateTime()),
        sa.Column("EndAt", sa.DateTime()),
        sa.Column("IsOjt", sa.Boolean()),
        sa.Column("TeacherUserId", sa.String(length=36)),
    )
    raw_worklogs = sa.Table(
        "RawWorklogs",
        metadata,
        sa.Column("Id", sa.String(length=36)),
        sa.Column("UserId", sa.String(length=36)),
        sa.Column("TaskId", sa.String(length=36)),
        sa.Column("SourceTimerId", sa.String(length=36)),
        sa.Column("WorkDate", sa.Date()),
        sa.Column("StartAt", sa.DateTime()),
        sa.Column("EndAt", sa.DateTime()),
        sa.Column("DurationSeconds", sa.Float()),
        sa.Column("IsOjt", sa.Boolean()),
        sa.Column("TeacherUserId", sa.String(length=36)),
    )
    daily_actuals = sa.Table(
        "DailyTaskActuals",
        metadata,
        sa.Column("Id", sa.String(length=36)),
        sa.Column("ProjectId", sa.String(length=36)),
        sa.Column("UserId", sa.String(length=36)),
        sa.Column("TaskId", sa.String(length=36)),
        sa.Column("WorkDate", sa.Date()),
        sa.Column("TimerMinutes", sa.Float()),
        sa.Column("DistributedMinutes", sa.Float()),
        sa.Column("TotalMinutes", sa.Float()),
    )

    # 既存タイマ実績を生ログへ移し、将来の再集計を可能にする。
    timer_rows = bind.execute(sa.select(timers)).mappings().all()
    for row in timer_rows:
        end_at_raw = row.get("EndAt")
        start_at_raw = row.get("StartAt")
        if not start_at_raw or not end_at_raw:
            continue
        start_at = _parse_datetime(start_at_raw)
        end_at = _parse_datetime(end_at_raw)
        duration = max(0.0, (end_at - start_at).total_seconds())
        if duration <= 0:
            continue
        bind.execute(
            raw_worklogs.insert().values(
                Id=str(uuid4()),
                UserId=row["UserId"],
                TaskId=row["TaskId"],
                SourceTimerId=row["Id"],
                WorkDate=start_at.date(),
                StartAt=start_at,
                EndAt=end_at,
                DurationSeconds=duration,
                IsOjt=bool(row.get("IsOjt")),
                TeacherUserId=row.get("TeacherUserId"),
            )
        )

    base_start = date(2026, 2, 10)
    ongoing_cutoff = date(2026, 2, 13)

    # 既存データは実着手日と日次実績を再構成して時系列グラフの点数を確保する。
    task_rows = bind.execute(sa.select(tasks)).mappings().all()
    for row in task_rows:
        task_id = row["Id"]
        project_id = row["ProjectId"]
        user_id = row["AssigneeUserId"]
        status = str(row.get("Status") or "")
        end_date = _parse_date(row["EndDate"])
        completed_at_raw = row.get("CompletedAt")

        completed_date: date | None = None
        if status == "Completion":
            if completed_at_raw:
                completed_date = _parse_datetime(completed_at_raw).date()
            else:
                completed_date = end_date

        if completed_date and completed_date < base_start:
            actual_start = completed_date
            distribution_end = completed_date
        else:
            actual_start = base_start
            distribution_end = completed_date if completed_date else ongoing_cutoff

        if distribution_end < actual_start:
            distribution_end = actual_start

        bind.execute(
            tasks.update()
            .where(tasks.c.Id == task_id)
            .values(ActualStartDate=actual_start)
        )

        total_minutes = float(row.get("ActualManHour") or 0)
        if total_minutes <= 0:
            continue

        workdays = _iter_workdays(actual_start, distribution_end, holidays)
        if not workdays:
            workdays = [distribution_end]

        per_day = total_minutes / float(len(workdays))
        for work_date in workdays:
            bind.execute(
                daily_actuals.insert().values(
                    Id=str(uuid4()),
                    ProjectId=project_id,
                    UserId=user_id,
                    TaskId=task_id,
                    WorkDate=work_date,
                    TimerMinutes=0.0,
                    DistributedMinutes=per_day,
                    TotalMinutes=per_day,
                )
            )


def downgrade() -> None:
    op.drop_table("DailyProgressSnapshots")
    op.drop_table("DailyTaskActuals")
    op.drop_table("RawWorklogs")
    op.drop_column("Tasks", "ActualStartDate")
