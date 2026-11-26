from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "Users"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    Name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    Nickname: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    FullName: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    LoginId: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    IQuavisUserId: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    IsTopManager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 巣の役職。president(社長) / manager(課長) / chief(係長) / member(一般)。権限は domain/colony.py。
    ColonyRole: Mapped[str] = mapped_column(String(16), nullable=False, default="member", server_default="member")
    FreeTimeRate: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class MemberProfile(Base):
    __tablename__ = "MemberProfiles"

    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), primary_key=True
    )
    DefaultCommitRate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    ConditionIcon: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    FormationRole: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    FormationPosition: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    FormationX: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    FormationY: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    DisplayOrder: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    TitleTagsJson: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    SpecialAbilitiesJson: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    UpdateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MemberDayConfig(Base):
    __tablename__ = "MemberDayConfigs"
    __table_args__ = (UniqueConstraint("UserId", "WorkDate", name="uq_member_day_config"),)

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    WorkDate: Mapped[date] = mapped_column(Date, nullable=False)
    IsVacation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    LeaveType: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    CommitRateOverride: Mapped[float | None] = mapped_column(Float, nullable=True)
    Note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    UpdateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Project(Base):
    __tablename__ = "Projects"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    Name: Mapped[str] = mapped_column(String(128), nullable=False)
    StartDate: Mapped[date] = mapped_column(Date, nullable=False)
    EndDate: Mapped[date] = mapped_column(Date, nullable=False)
    CrashZonePct: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class Task(Base):
    __tablename__ = "Tasks"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ProjectId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Projects.Id", ondelete="CASCADE"), nullable=False
    )
    ParentTaskId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="SET NULL"), nullable=True
    )
    Name: Mapped[str] = mapped_column(String(1024), nullable=False)
    Type: Mapped[str] = mapped_column(String(32), nullable=False, default="Task")
    AssigneeUserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="RESTRICT"), nullable=False
    )
    CreatorUserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="RESTRICT"), nullable=False
    )
    StartDate: Mapped[date] = mapped_column(Date, nullable=False)
    EndDate: Mapped[date] = mapped_column(Date, nullable=False)
    AssignedManHour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ActualManHour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ProgressRate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    Status: Mapped[str] = mapped_column(String(16), nullable=False, default="NotStart")
    Memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    PlannedDeliverablesJson: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    OutputDeliverablesJson: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    IsRework: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    PreventedByTaskId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="SET NULL"), nullable=True
    )
    CompletedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    TodayOverride: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    TodaySortOrder: Mapped[int | None] = mapped_column(Integer, nullable=True)
    WbsSortOrder: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ChildDependencyMode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="Serial",
        server_default="Serial",
    )
    IQuavisTaskId: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    IQuavisProjectId: Mapped[str | None] = mapped_column(String(36), nullable=True)
    IQuavisLastPulledAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    IQuavisLastPushedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    # 巣の外で置いたお菓子の大きさ（0=かけら〜3=板チョコ）。置いた人の役職で上限が決まる。iQUAVISへは送らない。
    ColonySize: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 同時編集が競合したとき「誰の変更とぶつかったか」を相手に伝えるために持つ。
    UpdatedByUserId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="SET NULL"), nullable=True
    )
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    UpdateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped["Task | None"] = relationship(
        "Task",
        remote_side=[Id],
        foreign_keys=[ParentTaskId],
        backref="children",
    )


class TimerSession(Base):
    __tablename__ = "TimerSessions"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), nullable=False
    )
    StartAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    EndAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    Status: Mapped[str] = mapped_column(String(16), nullable=False, default="Running")
    IsOjt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    TeacherUserId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="SET NULL"), nullable=True
    )
    LastHeartbeatAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    LastInteractionAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    AutoStopReason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RawWorklog(Base):
    __tablename__ = "RawWorklogs"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), nullable=False
    )
    SourceTimerId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("TimerSessions.Id", ondelete="SET NULL"), nullable=True
    )
    WorkDate: Mapped[date] = mapped_column(Date, nullable=False)
    StartAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    EndAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    DurationSeconds: Mapped[float] = mapped_column(Float, nullable=False)
    IsOjt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    TeacherUserId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="SET NULL"), nullable=True
    )
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class DailyTaskActual(Base):
    __tablename__ = "DailyTaskActuals"
    __table_args__ = (UniqueConstraint("UserId", "TaskId", "WorkDate", name="uq_daily_task_actual"),)

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ProjectId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Projects.Id", ondelete="CASCADE"), nullable=False
    )
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), nullable=False
    )
    WorkDate: Mapped[date] = mapped_column(Date, nullable=False)
    TimerMinutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    DistributedMinutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    TotalMinutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    UpdateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DailyProgressSnapshot(Base):
    __tablename__ = "DailyProgressSnapshots"
    __table_args__ = (UniqueConstraint("TaskId", "SnapshotDate", name="uq_daily_progress_snapshot"),)

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ProjectId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Projects.Id", ondelete="CASCADE"), nullable=False
    )
    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), nullable=False
    )
    SnapshotDate: Mapped[date] = mapped_column(Date, nullable=False)
    ProgressRate: Mapped[float] = mapped_column(Float, nullable=False)
    PlannedProgressRate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    CumActualMinutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    VActual: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    VAverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    VCritical: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    VSafe: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class DailyAttendance(Base):
    __tablename__ = "DailyAttendances"
    __table_args__ = (UniqueConstraint("UserId", "WorkDate", name="uq_daily_attendance"),)

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    WorkDate: Mapped[date] = mapped_column(Date, nullable=False)
    StartAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    EndAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    WorkMinutes: Mapped[int] = mapped_column(Integer, nullable=False)


class MonthlyEducationRate(Base):
    __tablename__ = "MonthlyEducationRates"
    __table_args__ = (UniqueConstraint("UserId", "YearMonth", name="uq_education_rate"),)

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), nullable=False
    )
    YearMonth: Mapped[str] = mapped_column(String(7), nullable=False)
    EducationRateMonthly: Mapped[float] = mapped_column(Float, nullable=False)


class ProgressRateHistory(Base):
    __tablename__ = "ProgressRateHistories"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ProjectId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Projects.Id", ondelete="CASCADE"), nullable=False
    )
    RecordedAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    ProgressRate: Mapped[float] = mapped_column(Float, nullable=False)
    Reason: Mapped[str] = mapped_column(String(32), nullable=False)


class AppState(Base):
    __tablename__ = "AppState"

    Key: Mapped[str] = mapped_column(String(128), primary_key=True)
    Value: Mapped[str] = mapped_column(Text, nullable=False)


class IQuavisTaskBaseline(Base):
    """QOLONYとiQUAVISが最後に一致した共有項目の値（魚拓）。

    3値比較（baseline / QOLONY現在値 / iQUAVIS現在値）の基準になる。
    xlsmの非表示 `__TASKS_...` シートはiQUAVISから再取得するたびに最新値で上書きされるため、
    「前回どの値で合意したか」はxlsmから復元できない。QOLONY側で持たないと、
    iQUAVIS側の変更とQOLONY側の変更を区別できず、片側だけの変更まで競合や保存待ちになる。
    """

    __tablename__ = "IQuavisTaskBaselines"

    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), primary_key=True
    )
    IQuavisTaskId: Mapped[str] = mapped_column(String(64), nullable=False)
    Name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    StartDate: Mapped[date] = mapped_column(Date, nullable=False)
    EndDate: Mapped[date] = mapped_column(Date, nullable=False)
    ActualManHour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    Status: Mapped[str] = mapped_column(String(32), nullable=False, default="NotStart")
    # 進捗率列を持たないブックでは未取得のままにするため null 許容にする。
    ProgressRate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    Memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    CapturedAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)


class IQuavisSyncJob(Base):
    __tablename__ = "IQuavisSyncJobs"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    TaskId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Tasks.Id", ondelete="CASCADE"), nullable=False
    )
    IQuavisTaskId: Mapped[str] = mapped_column(String(36), nullable=False)
    JobType: Mapped[str] = mapped_column(String(32), nullable=False)
    PayloadJson: Mapped[str] = mapped_column(Text, nullable=False)
    Status: Mapped[str] = mapped_column(String(16), nullable=False, default="Pending")
    Attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    NextRunAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    LastStartedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    LastFinishedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    LastHttpStatus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    LastError: Mapped[str | None] = mapped_column(Text, nullable=True)
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    UpdateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ColonyTeam(Base):
    """巣の中の部屋 = チーム。親をたどると社長室（親なし）に着く。"""

    __tablename__ = "ColonyTeams"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    Name: Mapped[str] = mapped_column(String(128), nullable=False)
    ParentTeamId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ColonyTeams.Id", ondelete="SET NULL"), nullable=True
    )
    # 部屋の主。課長か係長だけがなれる。
    LeaderUserId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="SET NULL"), nullable=True
    )
    Emblem: Mapped[str] = mapped_column(String(32), nullable=False, default="leaf")
    DisplayOrder: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class ColonyTeamMember(Base):
    """チームへの所属。1人が複数のチームを掛け持ちできる。"""

    __tablename__ = "ColonyTeamMembers"

    TeamId: Mapped[str] = mapped_column(
        String(36), ForeignKey("ColonyTeams.Id", ondelete="CASCADE"), primary_key=True
    )
    UserId: Mapped[str] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="CASCADE"), primary_key=True
    )
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


PolicyText = Text


class ColonyPolicy(Base):
    """社長が地面にチョークで書く方針。見えるだけで、システム上の効力は持たせない。"""

    __tablename__ = "ColonyPolicies"

    Id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # text = 文字、line = 手書きの線
    Kind: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    Text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    X: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    Y: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    Size: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    # 属性名 Text が型の Text を隠すので、ここは型を別名で参照する。
    PointsJson: Mapped[str] = mapped_column(PolicyText, nullable=False, default="[]")
    AuthorUserId: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("Users.Id", ondelete="SET NULL"), nullable=True
    )
    CreateDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
