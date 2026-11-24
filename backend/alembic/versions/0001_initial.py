"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-06 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "AppState",
        sa.Column("Key", sa.String(length=128), nullable=False),
        sa.Column("Value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("Key"),
    )

    op.create_table(
        "Projects",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("Name", sa.String(length=128), nullable=False),
        sa.Column("StartDate", sa.Date(), nullable=False),
        sa.Column("EndDate", sa.Date(), nullable=False),
        sa.Column("CrashZonePct", sa.Float(), nullable=False),
        sa.Column(
            "CreateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("Id"),
    )

    op.create_table(
        "Users",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("Name", sa.String(length=128), nullable=False),
        sa.Column("LoginId", sa.String(length=256), nullable=False),
        sa.Column("IsTopManager", sa.Boolean(), nullable=False),
        sa.Column("FreeTimeRate", sa.Float(), nullable=False),
        sa.Column(
            "CreateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("Id"),
        sa.UniqueConstraint("Name"),
        sa.UniqueConstraint("LoginId"),
    )

    op.create_table(
        "Tasks",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("ProjectId", sa.String(length=36), nullable=False),
        sa.Column("ParentTaskId", sa.String(length=36), nullable=True),
        sa.Column("Name", sa.String(length=1024), nullable=False),
        sa.Column("Type", sa.String(length=32), nullable=False),
        sa.Column("AssigneeUserId", sa.String(length=36), nullable=False),
        sa.Column("CreatorUserId", sa.String(length=36), nullable=False),
        sa.Column("StartDate", sa.Date(), nullable=False),
        sa.Column("EndDate", sa.Date(), nullable=False),
        sa.Column("AssignedManHour", sa.Integer(), nullable=False),
        sa.Column("ActualManHour", sa.Integer(), nullable=False),
        sa.Column("ProgressRate", sa.Float(), nullable=False),
        sa.Column("Status", sa.String(length=16), nullable=False),
        sa.Column("PlannedDeliverablesJson", sa.Text(), nullable=False),
        sa.Column("OutputDeliverablesJson", sa.Text(), nullable=False),
        sa.Column("IsRework", sa.Boolean(), nullable=False),
        sa.Column("PreventedByTaskId", sa.String(length=36), nullable=True),
        sa.Column("CompletedAt", sa.DateTime(), nullable=True),
        sa.Column(
            "CreateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "UpdateDate",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["AssigneeUserId"], ["Users.Id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["CreatorUserId"], ["Users.Id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ParentTaskId"], ["Tasks.Id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["PreventedByTaskId"], ["Tasks.Id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ProjectId"], ["Projects.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
    )

    op.create_table(
        "TimerSessions",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("UserId", sa.String(length=36), nullable=False),
        sa.Column("TaskId", sa.String(length=36), nullable=False),
        sa.Column("StartAt", sa.DateTime(), nullable=False),
        sa.Column("EndAt", sa.DateTime(), nullable=True),
        sa.Column("Status", sa.String(length=16), nullable=False),
        sa.Column("IsOjt", sa.Boolean(), nullable=False),
        sa.Column("TeacherUserId", sa.String(length=36), nullable=True),
        sa.Column("LastHeartbeatAt", sa.DateTime(), nullable=False),
        sa.Column("LastInteractionAt", sa.DateTime(), nullable=False),
        sa.Column("AutoStopReason", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["TeacherUserId"], ["Users.Id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
    )

    op.create_table(
        "DailyAttendances",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("UserId", sa.String(length=36), nullable=False),
        sa.Column("WorkDate", sa.Date(), nullable=False),
        sa.Column("StartAt", sa.DateTime(), nullable=False),
        sa.Column("EndAt", sa.DateTime(), nullable=False),
        sa.Column("WorkMinutes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
        sa.UniqueConstraint("UserId", "WorkDate", name="uq_daily_attendance"),
    )

    op.create_table(
        "MonthlyEducationRates",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("UserId", sa.String(length=36), nullable=False),
        sa.Column("YearMonth", sa.String(length=7), nullable=False),
        sa.Column("EducationRateMonthly", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
        sa.UniqueConstraint("UserId", "YearMonth", name="uq_education_rate"),
    )

    op.create_table(
        "ProgressRateHistories",
        sa.Column("Id", sa.String(length=36), nullable=False),
        sa.Column("ProjectId", sa.String(length=36), nullable=False),
        sa.Column("RecordedAt", sa.DateTime(), nullable=False),
        sa.Column("ProgressRate", sa.Float(), nullable=False),
        sa.Column("Reason", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["ProjectId"], ["Projects.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("Id"),
    )


def downgrade() -> None:
    op.drop_table("ProgressRateHistories")
    op.drop_table("MonthlyEducationRates")
    op.drop_table("DailyAttendances")
    op.drop_table("TimerSessions")
    op.drop_table("Tasks")
    op.drop_table("Users")
    op.drop_table("Projects")
    op.drop_table("AppState")
