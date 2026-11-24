from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN
from math import isfinite
from pathlib import Path
from typing import Callable

import flet as ft

from app.tms_core.config import Settings, SplitTemplate
from app.tms_core.domain.speed import calculate_v_critical, calculate_v_safe
from app.tms_core.services.core import TaskView, TmsService, UserInputError

logger = logging.getLogger(__name__)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except Exception:
    HAS_MATPLOTLIB = False


@dataclass
class SplitGridRow:
    task_name: str = ""
    mh_ratio: str = ""
    memo: str = ""
    due_date: str = ""


@dataclass(frozen=True)
class WbsSection:
    level: int
    parent_task_id: str | None
    active_task_id: str | None
    tasks: list[TaskView]


@dataclass(frozen=True)
class WbsRenderState:
    sections: list[WbsSection]
    visible_task_ids: list[str]
    active_task_id: str | None


def build_wbs_render_state(
    tasks: list[TaskView],
    active_task_id: str | None,
    expanded_task_ids: set[str],
) -> WbsRenderState:
    task_by_id = {task.Id: task for task in tasks}
    normalized_active = active_task_id if active_task_id in task_by_id else None
    normalized_expanded = {task_id for task_id in expanded_task_ids if task_id in task_by_id}

    children: dict[str | None, list[TaskView]] = {}
    for task in tasks:
        children.setdefault(task.ParentTaskId, []).append(task)

    for parent_id in children:
        children[parent_id] = sorted(children[parent_id], key=lambda t: (t.EndDate, t.Name))

    if not normalized_active:
        root_tasks = children.get(None, [])
        return WbsRenderState(
            sections=[
                WbsSection(
                    level=1,
                    parent_task_id=None,
                    active_task_id=None,
                    tasks=root_tasks,
                )
            ],
            visible_task_ids=[task.Id for task in root_tasks],
            active_task_id=None,
        )

    # ルートからアクティブまでの経路を固定する。
    # 非アクティブ階層は直系親のみ表示し、アクティブ階層のみ兄弟を表示する。
    chain_ids: list[str] = []
    walker = task_by_id.get(normalized_active)
    while walker is not None:
        chain_ids.append(walker.Id)
        if not walker.ParentTaskId:
            break
        walker = task_by_id.get(walker.ParentTaskId)
    chain_ids.reverse()

    sections: list[WbsSection] = []
    visible_task_ids: list[str] = []

    for level, chain_task_id in enumerate(chain_ids, start=1):
        chain_task = task_by_id[chain_task_id]
        if chain_task_id == normalized_active:
            section_tasks = children.get(chain_task.ParentTaskId, [])
        else:
            section_tasks = [chain_task]
        sections.append(
            WbsSection(
                level=level,
                parent_task_id=chain_task.ParentTaskId,
                active_task_id=chain_task_id,
                tasks=section_tasks,
            )
        )
        visible_task_ids.extend(task.Id for task in section_tasks)

    if normalized_active in normalized_expanded and children.get(normalized_active):
        child_level = len(chain_ids) + 1
        child_tasks = children.get(normalized_active, [])
        sections.append(
            WbsSection(
                level=child_level,
                parent_task_id=normalized_active,
                active_task_id=None,
                tasks=child_tasks,
            )
        )
        visible_task_ids.extend(task.Id for task in child_tasks)

    return WbsRenderState(
        sections=sections,
        visible_task_ids=visible_task_ids,
        active_task_id=normalized_active,
    )


def resolve_wbs_click_active_task_id(
    tasks: list[TaskView],
    clicked_task_id: str,
    current_active_task_id: str | None = None,
) -> str | None:
    task_by_id = {task.Id: task for task in tasks}
    if clicked_task_id not in task_by_id:
        return None

    # すでに下位タスクがアクティブな状態で祖先をクリックした場合は、
    # 上位階層へ戻れるようにその祖先自身をアクティブ化する。
    if current_active_task_id in task_by_id:
        parent_by_id = {task.Id: task.ParentTaskId for task in tasks}
        cursor = parent_by_id.get(current_active_task_id)
        while cursor:
            if cursor == clicked_task_id:
                return clicked_task_id
            cursor = parent_by_id.get(cursor)

    # 子を持つタスクがクリックされた場合は、子の先頭を強制アクティブにする。
    children = [task for task in tasks if task.ParentTaskId == clicked_task_id]
    if children:
        first_child = sorted(children, key=lambda t: (t.EndDate, t.Name))[0]
        return first_child.Id
    return clicked_task_id


class TmsFletApp:
    def __init__(self, page: ft.Page, service: TmsService, settings: Settings):
        self.page = page
        self.service = service
        self.settings = settings

        self.users = self.service.list_users()
        self.user_by_id = {u.Id: u for u in self.users}
        self.selected_user_id = self.users[0].Id if self.users else ""
        self.selected_task_id: str | None = None
        self.wbs_expanded_task_ids: set[str] = set()
        self.wbs_visible_task_ids: list[str] = []
        self.today_visible_task_ids: list[str] = []
        self.split_rows: list[SplitGridRow] = [SplitGridRow() for _ in range(256)]
        self.split_fields: list[tuple[ft.TextField, ft.TextField, ft.TextField, ft.TextField]] = []
        self.split_active_cell: tuple[int, int] = (0, 0)
        self.split_parent_task_id: str | None = None
        self.split_return_tab_index: int = 0
        self.split_bulk_update = False
        self.progress_chart_cache_key: tuple | None = None
        self.progress_chart_cache_png: bytes | None = None

        self.current_year_month = date.today().strftime("%Y-%m")

        self._setup_page()
        self._build_controls()
        self._refresh_scope("current")

        self.page.run_task(self._center_window_once)
        self.page.run_task(self._background_loop)

    def _setup_page(self) -> None:
        self.page.title = "QOLONY"
        icon_path = Path(__file__).resolve().parents[3] / "assets" / "qolony.ico"
        if icon_path.exists():
            self.page.window.icon = str(icon_path)
        self.page.window.width = self.settings.app.mobile_window_width
        self.page.window.height = self.settings.app.mobile_window_height
        self.page.window.resizable = True
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary="#00A693",
                secondary="#FFB703",
                surface="#F8FFF9",
                error="#D90429",
            ),
            font_family="M PLUS 1p",
        )
        self.page.bgcolor = "#E7FFF0"
        self.page.on_keyboard_event = self._on_keyboard_event

    async def _center_window_once(self) -> None:
        await asyncio.sleep(0)
        await self.page.window.center()

    def _snack(self, message: str, error: bool = False) -> None:
        self.page.snack_bar = ft.SnackBar(
            ft.Text(message, color=ft.Colors.WHITE),
            bgcolor="#D90429" if error else "#0B6E4F",
            open=True,
        )
        self.page.update()

    def _run_action(self, action: Callable[[], None], refresh_scope: str = "current") -> None:
        try:
            action()
            self._refresh_scope(refresh_scope)
        except UserInputError as exc:
            self._snack(str(exc), error=True)
        except Exception:
            logger.exception("unexpected error")
            self._snack("システムエラーが発生しました。ログを確認してください", error=True)

    def _refresh_tab_by_index(self, index: int) -> None:
        if index == 0:
            self._refresh_tasks()
        elif index == 1:
            self._refresh_my_tasks()
        elif index == 2:
            self._refresh_today()
        elif index == 3:
            self._refresh_split()
        elif index == 4:
            self._refresh_detail()
        elif index == 5:
            self._refresh_progress()
        elif index == 6:
            self._refresh_ranking()
        elif index == 7:
            self._refresh_admin()

    def _refresh_scope(self, scope: str) -> None:
        if scope == "all":
            self._refresh_today()
            self._refresh_tasks()
            self._refresh_my_tasks()
            self._refresh_split()
            self._refresh_detail()
            self._refresh_progress()
            self._refresh_ranking()
            self._refresh_admin()
            self.page.update()
            return

        if scope == "today":
            self._refresh_today()
            self.page.update()
            return
        if scope == "split":
            self._refresh_split()
            self.page.update()
            return
        if scope == "wbs":
            self._refresh_tasks()
            self.page.update()
            return
        if scope == "mytask":
            self._refresh_my_tasks()
            self.page.update()
            return
        if scope == "detail":
            self._refresh_detail()
            self.page.update()
            return
        if scope == "progress":
            self._refresh_progress()
            self.page.update()
            return
        if scope == "ranking":
            self._refresh_ranking()
            self.page.update()
            return
        if scope == "admin":
            self._refresh_admin()
            self.page.update()
            return

        self._refresh_tab_by_index(self.current_tab_index)
        self.page.update()

    def _build_controls(self) -> None:
        self.user_select = ft.Dropdown(
            options=[ft.dropdown.Option(key=u.Id, text=u.Name) for u in self.users],
            value=self.selected_user_id,
            on_select=lambda e: self._run_action(self._on_user_change),
            dense=True,
            width=110,
            text_size=11,
            color=ft.Colors.WHITE,
            border_color="#0EA5A4",
            focused_border_color=ft.Colors.WHITE,
            bgcolor="#0EA5A4",
            filled=True,
            fill_color="#0EA5A4",
            hover_color="#0EA5A4",
            text_style=ft.TextStyle(color=ft.Colors.WHITE),
        )

        self.today_ojt_switch = ft.Switch(label="OJT受講モード", value=False)
        self.today_teacher_select = ft.Dropdown(label="Teacher", options=[], dense=True)

        self.today_target_task_text = ft.Text(size=13, color="#355070")
        self.today_active_timer_text = ft.Text(size=15, weight=ft.FontWeight.BOLD)
        self.today_start_button = ft.ElevatedButton("Start", on_click=lambda e: self._run_action(self._on_start_timer), bgcolor="#0BA360", color=ft.Colors.WHITE)
        self.today_pause_button = ft.ElevatedButton("Pause", on_click=lambda e: self._run_action(self._on_pause_timer), bgcolor="#F4A261", color=ft.Colors.WHITE)
        self.today_done_button = ft.ElevatedButton("Done", on_click=lambda e: self._run_action(self._on_done_timer), bgcolor="#E63946", color=ft.Colors.WHITE)
        self.today_task_list = ft.ListView(spacing=8, expand=True)

        self.wbs_title = ft.GestureDetector(
            content=ft.Text("WBS", size=14, weight=ft.FontWeight.BOLD, color="#0B3D3A"),
            on_tap=lambda e: self._run_action(self._reset_wbs_view),
        )
        self.wbs_hint = ft.Text(
            "クリック: 展開 / ダブルクリック: 詳細 / 右クリック: Split / ↑↓: 移動 / →: 展開 / ←: 1階層戻る",
            size=11,
            color="#4D6A63",
        )
        self.wbs_list = ft.ListView(spacing=8, expand=True)
        self.my_task_list = ft.ListView(spacing=8, expand=True)

        self.detail_title = ft.Text("タスク詳細", size=18, weight=ft.FontWeight.BOLD)
        self.detail_info = ft.Text(size=13)
        self.detail_planned = ft.Column(spacing=4)
        self.detail_outputs = ft.Column(spacing=4)

        self.output_title_input = ft.TextField(label="成果物タイトル", dense=True)
        self.output_url_input = ft.TextField(label="成果物URL", dense=True)
        self.output_add_button = ft.ElevatedButton("成果物追加", on_click=lambda e: self._run_action(self._on_add_output))

        self.complete_button = ft.ElevatedButton("完了にする", on_click=lambda e: self._run_action(self._on_complete_task), bgcolor="#2A9D8F", color=ft.Colors.WHITE)
        self.reopen_button = ft.OutlinedButton("完了解除", on_click=lambda e: self._run_action(self._on_reopen_task))
        self.detail_memo_input = ft.TextField(
            label="メモ",
            multiline=True,
            min_lines=3,
            max_lines=6,
        )
        self.detail_memo_save_button = ft.ElevatedButton(
            "メモ保存",
            on_click=lambda e: self._run_action(self._on_save_task_memo),
        )
        self.detail_open_split_button = ft.OutlinedButton(
            "Split viewへ",
            on_click=lambda e: self._run_action(self._open_split_from_active),
        )

        self.split_title_text = ft.Text(size=13, color="#355070")
        self.split_template_select = ft.Dropdown(
            width=160,
            dense=True,
            options=[
                ft.dropdown.Option(str(index), template.title)
                for index, template in enumerate(self.settings.split_templates)
            ],
            value="0" if self.settings.split_templates else None,
        )
        self.split_apply_template_button = ft.ElevatedButton(
            "テンプレート適用",
            on_click=lambda e: self._run_action(self._on_split_apply_template, refresh_scope="split"),
            bgcolor="#1D4ED8",
            color=ft.Colors.WHITE,
        )
        self.split_equal_button = ft.OutlinedButton(
            "MH当分割",
            on_click=lambda e: self._run_action(self._on_split_equalize, refresh_scope="split"),
        )
        self.split_execute_button = ft.ElevatedButton(
            "分割実行",
            on_click=lambda e: self._run_action(self._on_split_execute, refresh_scope="all"),
            bgcolor="#7C3AED",
            color=ft.Colors.WHITE,
        )
        self.split_status_text = ft.Text(size=12, color="#4B5563")
        self.split_grid_list = ft.ListView(spacing=4, expand=True)
        self._build_split_grid_controls()

        self.progress_mode_select = ft.Dropdown(
            label="表示",
            value="speed",
            options=[
                ft.dropdown.Option("speed", "速度グラフ"),
                ft.dropdown.Option("progress", "進捗率グラフ"),
            ],
            on_select=lambda e: self._run_action(lambda: None),
            dense=True,
        )
        self.progress_signal = ft.Text(size=16, weight=ft.FontWeight.BOLD)
        self.progress_stats = ft.Text(size=13)
        self.progress_chart_area = ft.Container(expand=True)

        self.ranking_metric = ft.Dropdown(
            label="指標",
            value="THR",
            options=[
                ft.dropdown.Option("THR", "THR"),
                ft.dropdown.Option("ExecRatio", "ExecRatio"),
                ft.dropdown.Option("EstRatio", "EstRatio"),
                ft.dropdown.Option("TeachingMinutes", "TeachingMinutes"),
            ],
            on_select=lambda e: self._run_action(lambda: None),
            dense=True,
        )
        self.ranking_year_month = ft.TextField(label="対象月(YYYY-MM)", value=self.current_year_month, dense=True)
        self.ranking_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("順位")),
                ft.DataColumn(ft.Text("名前")),
                ft.DataColumn(ft.Text("値")),
                ft.DataColumn(ft.Text("公開")),
            ],
            rows=[],
            heading_row_color="#D3F8E2",
            data_row_min_height=34,
        )

        self.admin_message = ft.Text(size=13)
        self.admin_education_user = ft.Dropdown(label="教育比率ユーザー", options=[ft.dropdown.Option(u.Id, u.Name) for u in self.users], dense=True)
        self.admin_education_month = ft.TextField(label="対象月(YYYY-MM)", value=self.current_year_month, dense=True)
        self.admin_education_rate = ft.TextField(label="教育比率(0.0-1.0)", value="0.10", dense=True)
        self.admin_education_save = ft.ElevatedButton("教育比率保存", on_click=lambda e: self._run_action(self._on_save_education_rate))

        self.admin_crash_zone = ft.TextField(label="CrashZonePct", value=str(self.settings.app.crash_zone_pct), dense=True)
        self.admin_crash_save = ft.ElevatedButton("CrashZone更新", on_click=lambda e: self._run_action(self._on_save_crash_zone))

        self.admin_create_name = ft.TextField(label="新規タスク名", dense=True)
        self.admin_create_assignee = ft.Dropdown(label="担当者", options=[ft.dropdown.Option(u.Id, u.Name) for u in self.users], dense=True)
        self.admin_create_minutes = ft.TextField(label="工数(分)", dense=True, keyboard_type=ft.KeyboardType.NUMBER)
        self.admin_create_due = ft.TextField(label="期限(YYYY-MM-DD)", value=str(date.today()), dense=True)
        self.admin_create_is_rework = ft.Checkbox(label="Reworkタスク", value=False)
        self.admin_create_button = ft.ElevatedButton("タスク作成", on_click=lambda e: self._run_action(self._on_create_task))

        self.admin_rework_task = ft.Dropdown(label="Reworkタスク", options=[], dense=True)
        self.admin_prevented_by = ft.Dropdown(label="prevented_by", options=[], dense=True)
        self.admin_link_button = ft.ElevatedButton("手戻り紐付け", on_click=lambda e: self._run_action(self._on_set_prevented_by))

        self.admin_infeasible = ft.Column(spacing=6)

        self.current_tab_index = 0
        self.header_titles = ["WBS", "My task", "Today", "Split", "Task Detail", "Progress", "Ranking", "Admin"]
        self.header_title = ft.Text(
            self.header_titles[0],
            size=18,
            weight=ft.FontWeight.W_700,
            color=ft.Colors.WHITE,
        )
        self.header_title_gesture = ft.GestureDetector(
            content=ft.Container(
                content=self.header_title,
                padding=ft.padding.symmetric(horizontal=6, vertical=4),
                border_radius=8,
                bgcolor="transparent",
            ),
            on_tap=lambda e: self._run_action(self._goto_next_tab),
            on_secondary_tap=lambda e: self._run_action(self._goto_prev_tab),
        )
        self.tab_views = [
            self._build_tasks_tab(),
            self._build_my_tasks_tab(),
            self._build_today_tab(),
            self._build_split_tab(),
            self._build_detail_tab(),
            self._build_progress_tab(),
            self._build_ranking_tab(),
            self._build_admin_tab(),
        ]
        self.content_area = ft.Container(content=self.tab_views[0], expand=True)
        self.nav_bar = ft.NavigationBar(
            selected_index=0,
            on_change=lambda e: self._run_action(self._on_nav_change),
            bgcolor="#E6F7EF",
            indicator_color="#B8F2D6",
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_HIDE,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.LIST_ALT, label="WBS"),
                ft.NavigationBarDestination(icon=ft.Icons.CHECKLIST, label="My task"),
                ft.NavigationBarDestination(icon=ft.Icons.TODAY, label="Today"),
                ft.NavigationBarDestination(icon=ft.Icons.CALL_SPLIT, label="Split"),
                ft.NavigationBarDestination(icon=ft.Icons.DETAILS, label="Detail"),
                ft.NavigationBarDestination(icon=ft.Icons.INSIGHTS, label="Progress"),
                ft.NavigationBarDestination(icon=ft.Icons.LEADERBOARD, label="Ranking"),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label="Admin"),
            ],
        )

        root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            [
                                self.header_title_gesture,
                                ft.Container(expand=True),
                                self.user_select,
                            ]
                        ),
                        padding=ft.padding.symmetric(horizontal=16, vertical=14),
                        gradient=ft.LinearGradient(["#0F766E", "#0EA5A4"]),
                        border_radius=ft.border_radius.only(bottom_left=20, bottom_right=20),
                    ),
                    self.content_area,
                    self.nav_bar,
                ],
                spacing=4,
                expand=True,
            ),
            expand=True,
        )

        self.page.add(root)
        self._update_header_title()

    def _card(self, title: str, content: ft.Control) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color="#0B3D3A"),
                    content,
                ],
                spacing=8,
            ),
            padding=12,
            bgcolor="#F7FFFC",
            border=ft.border.all(1, "#B7E4D7"),
            border_radius=14,
        )

    def _build_today_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    self._card(
                        "タイマ",
                        ft.Column(
                            [
                                self.today_target_task_text,
                                ft.Row([self.today_ojt_switch, ft.Container(expand=True)]),
                                self.today_teacher_select,
                                ft.Row([self.today_start_button, self.today_pause_button, self.today_done_button], spacing=8),
                                self.today_active_timer_text,
                            ]
                        ),
                    ),
                    self._card("Today tasks", self.today_task_list),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_tasks_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Column(
                            [
                                self.wbs_title,
                                self.wbs_hint,
                                self.wbs_list,
                            ],
                            spacing=8,
                            expand=True,
                        ),
                        padding=12,
                        bgcolor="#F7FFFC",
                        border=ft.border.all(1, "#B7E4D7"),
                        border_radius=14,
                        expand=True,
                    ),
                ],
                expand=True,
            ),
        )

    def _build_my_tasks_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text("My task", size=14, weight=ft.FontWeight.BOLD, color="#0B3D3A"),
                                self.my_task_list,
                            ],
                            spacing=8,
                            expand=True,
                        ),
                        padding=12,
                        bgcolor="#F7FFFC",
                        border=ft.border.all(1, "#B7E4D7"),
                        border_radius=14,
                        expand=True,
                    ),
                ],
                expand=True,
            ),
        )

    def _build_split_tab(self) -> ft.Control:
        header = ft.Row(
            [
                self.split_template_select,
                self.split_apply_template_button,
                self.split_equal_button,
                self.split_execute_button,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        table_header = ft.Container(
            padding=ft.padding.symmetric(horizontal=8, vertical=6),
            bgcolor="#E8F6F0",
            border=ft.border.all(1, "#CDE4DB"),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Container(content=ft.Text("#", size=11, weight=ft.FontWeight.BOLD), width=32),
                    ft.Container(content=ft.Text("タスク名", size=11, weight=ft.FontWeight.BOLD), width=120),
                    ft.Container(content=ft.Text("MH割合", size=11, weight=ft.FontWeight.BOLD), width=70),
                    ft.Container(content=ft.Text("メモ", size=11, weight=ft.FontWeight.BOLD), width=120),
                    ft.Container(content=ft.Text("期日", size=11, weight=ft.FontWeight.BOLD), width=96),
                ],
                spacing=6,
            ),
        )

        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    self.split_title_text,
                    header,
                    self.split_status_text,
                    table_header,
                    ft.Container(
                        content=self.split_grid_list,
                        expand=True,
                        border=ft.border.all(1, "#D5E8DF"),
                        border_radius=10,
                        padding=8,
                        bgcolor="#FFFFFF",
                    ),
                ],
                spacing=8,
                expand=True,
            ),
        )

    def _build_detail_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    self.detail_title,
                    self.detail_info,
                    self._card("予定成果物", self.detail_planned),
                    self._card(
                        "成果物登録",
                        ft.Column(
                            [
                                self.output_title_input,
                                self.output_url_input,
                                self.output_add_button,
                                self.detail_outputs,
                            ]
                        ),
                    ),
                    ft.Row([self.complete_button, self.reopen_button]),
                    self._card(
                        "メモ",
                        ft.Column(
                            [
                                self.detail_memo_input,
                                ft.Row([self.detail_memo_save_button, self.detail_open_split_button]),
                            ]
                        ),
                    ),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_progress_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    ft.Row([self.progress_mode_select]),
                    self.progress_signal,
                    self.progress_stats,
                    ft.Container(content=self.progress_chart_area, expand=True),
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _build_ranking_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    ft.Row([self.ranking_metric, self.ranking_year_month], spacing=10),
                    ft.Container(content=self.ranking_table, expand=True),
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _build_admin_tab(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                [
                    self.admin_message,
                    self._card(
                        "教育比率設定",
                        ft.Column(
                            [
                                self.admin_education_user,
                                self.admin_education_month,
                                self.admin_education_rate,
                                self.admin_education_save,
                            ]
                        ),
                    ),
                    self._card(
                        "CrashZone設定",
                        ft.Column([self.admin_crash_zone, self.admin_crash_save]),
                    ),
                    self._card(
                        "タスク作成",
                        ft.Column(
                            [
                                self.admin_create_name,
                                self.admin_create_assignee,
                                self.admin_create_minutes,
                                self.admin_create_due,
                                self.admin_create_is_rework,
                                self.admin_create_button,
                            ]
                        ),
                    ),
                    self._card(
                        "手戻り紐付け",
                        ft.Column(
                            [
                                self.admin_rework_task,
                                self.admin_prevented_by,
                                self.admin_link_button,
                            ]
                        ),
                    ),
                    self._card("不成立一覧", self.admin_infeasible),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _on_user_change(self) -> None:
        self.selected_user_id = self.user_select.value or ""
        self.selected_task_id = None
        self.split_parent_task_id = None

    def _on_nav_change(self) -> None:
        if self.nav_bar.selected_index is None:
            return
        self._goto_tab(int(self.nav_bar.selected_index))

    def _goto_tab(self, index: int) -> None:
        if index < 0 or index >= len(self.tab_views):
            return
        if index == 3 and self.current_tab_index != 3:
            self.split_return_tab_index = self.current_tab_index
        self.current_tab_index = index
        self.content_area.content = self.tab_views[self.current_tab_index]
        self.nav_bar.selected_index = self.current_tab_index
        self._update_header_title()

    def _goto_prev_tab(self) -> None:
        if self.current_tab_index <= 0:
            self._goto_tab(0)
            return
        self._goto_tab(self.current_tab_index - 1)

    def _goto_next_tab(self) -> None:
        if self.current_tab_index >= len(self.tab_views) - 1:
            self._goto_tab(len(self.tab_views) - 1)
            return
        self._goto_tab(self.current_tab_index + 1)

    def _update_header_title(self) -> None:
        if 0 <= self.current_tab_index < len(self.header_titles):
            self.header_title.value = self.header_titles[self.current_tab_index]
        else:
            self.header_title.value = "View"

    def _on_keyboard_event(self, event: ft.KeyboardEvent) -> None:
        if self.current_tab_index != 0:
            return
        normalized = (event.key or "").lower().replace(" ", "").replace("_", "")
        if normalized in {"arrowup", "uparrow", "up"}:
            self._run_action(lambda: self._move_active_task(-1))
        elif normalized in {"arrowdown", "downarrow", "down"}:
            self._run_action(lambda: self._move_active_task(1))
        elif normalized in {"arrowright", "rightarrow", "right"}:
            self._run_action(self._expand_active_task)
        elif normalized in {"arrowleft", "leftarrow", "left"}:
            self._run_action(self._collapse_to_parent)

    def _move_active_task(self, direction: int) -> None:
        if not self.wbs_visible_task_ids:
            return
        if self.selected_task_id not in self.wbs_visible_task_ids:
            next_index = 0 if direction > 0 else len(self.wbs_visible_task_ids) - 1
            self.selected_task_id = self.wbs_visible_task_ids[next_index]
            return

        current_index = self.wbs_visible_task_ids.index(self.selected_task_id)
        target_index = max(0, min(len(self.wbs_visible_task_ids) - 1, current_index + direction))
        self.selected_task_id = self.wbs_visible_task_ids[target_index]

    def _expand_active_task(self) -> None:
        if not self.selected_task_id:
            return
        task = self.service.get_task(self.selected_task_id)
        child_exists = any(t.ParentTaskId == task.Id for t in self.service.list_tasks())
        if not child_exists:
            return
        self.wbs_expanded_task_ids.add(task.Id)

    def _collapse_to_parent(self) -> None:
        if not self.selected_task_id:
            return
        task = self.service.get_task(self.selected_task_id)
        # 左矢印は現在ノードの展開を閉じて1階層上へ戻る。
        self.wbs_expanded_task_ids.discard(task.Id)
        if task.ParentTaskId:
            self.selected_task_id = task.ParentTaskId
            return
        self._reset_wbs_view()

    def _reset_wbs_view(self) -> None:
        self.selected_task_id = None
        self.wbs_expanded_task_ids.clear()

    def _activate_task_by_click(self, task_id: str) -> None:
        tasks = self.service.list_tasks()
        next_active_id = resolve_wbs_click_active_task_id(
            tasks,
            task_id,
            self.selected_task_id,
        )
        if not next_active_id:
            return
        self.selected_task_id = next_active_id
        self.wbs_expanded_task_ids.add(next_active_id)

    def _open_task_detail(self, task_id: str) -> None:
        self.selected_task_id = task_id
        self._goto_tab(4)

    def _open_split_view(self, task_id: str) -> None:
        self.selected_task_id = task_id
        if self.current_tab_index != 3:
            self.split_return_tab_index = self.current_tab_index
        self._goto_tab(3)

    def _open_split_from_active(self) -> None:
        if not self.selected_task_id:
            raise UserInputError("アクティブタスクがありません")
        self._open_split_view(self.selected_task_id)

    def _on_start_timer(self) -> None:
        task_id = self.selected_task_id
        if not task_id:
            raise UserInputError("Todayタスクを選択してください")
        if task_id not in self.today_visible_task_ids:
            raise UserInputError("Todayビューのタスクをアクティブ化してください")
        self.service.start_timer(
            user_id=self.selected_user_id,
            task_id=task_id,
            is_ojt=self.today_ojt_switch.value,
            teacher_user_id=self.today_teacher_select.value,
        )

    def _on_pause_timer(self) -> None:
        self.service.pause_timer(self.selected_user_id)

    def _on_done_timer(self) -> None:
        self.service.stop_timer(self.selected_user_id)

    def _on_toggle_today(self, task_id: str, enabled: bool) -> None:
        self.service.set_today_toggle(self.selected_user_id, task_id, enabled)

    def _move_today_task(self, task_id: str, direction: int) -> None:
        ordered = [task.Id for task in self.service.list_today_tasks(self.selected_user_id)]
        if task_id not in ordered:
            return
        current_index = ordered.index(task_id)
        target_index = current_index + direction
        if target_index < 0 or target_index >= len(ordered):
            return
        ordered[current_index], ordered[target_index] = ordered[target_index], ordered[current_index]
        self.service.reorder_today_tasks(self.selected_user_id, ordered)

    def _activate_flat_task(self, task_id: str) -> None:
        self.selected_task_id = task_id

    def _on_add_output(self) -> None:
        if not self.selected_task_id:
            raise UserInputError("タスクを選択してください")
        self.service.add_output_deliverable(
            task_id=self.selected_task_id,
            title=self.output_title_input.value or "",
            url=self.output_url_input.value or "",
        )
        self.output_title_input.value = ""
        self.output_url_input.value = ""

    def _on_complete_task(self) -> None:
        if not self.selected_task_id:
            raise UserInputError("タスクを選択してください")
        self.service.complete_task(self.selected_task_id)

    def _on_reopen_task(self) -> None:
        if not self.selected_task_id:
            raise UserInputError("タスクを選択してください")
        self.service.reopen_task(self.selected_task_id)

    def _on_save_task_memo(self) -> None:
        if not self.selected_task_id:
            raise UserInputError("タスクを選択してください")
        self.service.set_task_memo(self.selected_task_id, self.detail_memo_input.value or "")

    def _build_split_grid_controls(self) -> None:
        self.split_fields = []
        self.split_grid_list.controls = []

        for row_index in range(256):
            task_name = ft.TextField(
                value="",
                dense=True,
                width=120,
                on_focus=lambda e, r=row_index: self._on_split_cell_focus(r, 0),
                on_change=lambda e, r=row_index: self._on_split_cell_change(r, 0, e.control.value or ""),
            )
            mh_ratio = ft.TextField(
                value="",
                dense=True,
                width=70,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_focus=lambda e, r=row_index: self._on_split_cell_focus(r, 1),
                on_change=lambda e, r=row_index: self._on_split_cell_change(r, 1, e.control.value or ""),
            )
            memo = ft.TextField(
                value="",
                dense=True,
                width=120,
                on_focus=lambda e, r=row_index: self._on_split_cell_focus(r, 2),
                on_change=lambda e, r=row_index: self._on_split_cell_change(r, 2, e.control.value or ""),
            )
            due_date = ft.TextField(
                value="",
                dense=True,
                width=96,
                on_focus=lambda e, r=row_index: self._on_split_cell_focus(r, 3),
                on_change=lambda e, r=row_index: self._on_split_cell_change(r, 3, e.control.value or ""),
            )

            self.split_fields.append((task_name, mh_ratio, memo, due_date))
            self.split_grid_list.controls.append(
                ft.Row(
                    [
                        ft.Container(content=ft.Text(str(row_index + 1), size=11), width=32),
                        task_name,
                        mh_ratio,
                        memo,
                        due_date,
                    ],
                    spacing=6,
                )
            )

    def _on_split_cell_focus(self, row_index: int, col_index: int) -> None:
        self.split_active_cell = (row_index, col_index)

    def _split_set_cell_value(self, row_index: int, col_index: int, value: str) -> None:
        row = self.split_rows[row_index]
        if col_index == 0:
            row.task_name = value
        elif col_index == 1:
            row.mh_ratio = value
        elif col_index == 2:
            row.memo = value
        elif col_index == 3:
            row.due_date = value

    def _sync_split_field_values(self) -> None:
        self.split_bulk_update = True
        try:
            for row_index, row in enumerate(self.split_rows):
                task_name, mh_ratio, memo, due_date = self.split_fields[row_index]
                task_name.value = row.task_name
                mh_ratio.value = row.mh_ratio
                memo.value = row.memo
                due_date.value = row.due_date
        finally:
            self.split_bulk_update = False

    def _on_split_cell_change(self, row_index: int, col_index: int, value: str) -> None:
        if self.split_bulk_update:
            return

        if "\t" in value or "\n" in value:
            matrix = [
                line.split("\t")
                for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                if line != ""
            ]
            if not matrix:
                return
            max_rows = len(self.split_rows)
            max_cols = 4
            if row_index + len(matrix) > max_rows:
                self._snack("貼り付け行数が上限(256)を超えています", error=True)
                return
            if col_index + max(len(row) for row in matrix) > max_cols:
                self._snack("貼り付け列数が上限(4列)を超えています", error=True)
                return

            for r_offset, row_values in enumerate(matrix):
                for c_offset, cell in enumerate(row_values):
                    self._split_set_cell_value(row_index + r_offset, col_index + c_offset, cell.strip())
            self._sync_split_field_values()
            self.page.update()
            return

        self._split_set_cell_value(row_index, col_index, value.strip())

    def _get_active_split_parent(self) -> TaskView:
        if not self.selected_task_id:
            raise UserInputError("アクティブタスクがありません")
        return self.service.get_task(self.selected_task_id)

    def _default_split_due_date(self) -> str:
        if not self.selected_task_id:
            return ""
        try:
            task = self.service.get_task(self.selected_task_id)
        except UserInputError:
            return ""
        return task.EndDate.isoformat()

    def _reset_split_rows_for_active_task(self) -> None:
        default_due = self._default_split_due_date()
        self.split_rows = [SplitGridRow(due_date=default_due) for _ in range(256)]
        self._sync_split_field_values()

    def _split_used_rows(self) -> list[tuple[int, SplitGridRow]]:
        used_indices = [idx for idx, row in enumerate(self.split_rows) if row.task_name.strip()]
        if not used_indices:
            raise UserInputError("タスク名が入力されていません")

        last = max(used_indices)
        for idx in range(last + 1):
            if not self.split_rows[idx].task_name.strip():
                raise UserInputError("タスク名に空行があります。連続する行で入力してください")

        return [(idx, self.split_rows[idx]) for idx in range(last + 1)]

    def _format_ratio_value(self, ratio: Decimal) -> str:
        text = f"{ratio:.2f}"
        if text.endswith("00"):
            return str(int(ratio))
        if text.endswith("0"):
            return text[:-1]
        return text

    def _on_split_apply_template(self) -> None:
        parent = self._get_active_split_parent()
        if self.split_template_select.value is None:
            raise UserInputError("テンプレートを選択してください")
        template_index = int(self.split_template_select.value)
        templates: list[SplitTemplate] = self.settings.split_templates
        if template_index < 0 or template_index >= len(templates):
            raise UserInputError("テンプレートが不正です")

        template = templates[template_index]
        start_row = self.split_active_cell[0]
        if start_row + len(template.rows) > len(self.split_rows):
            raise UserInputError("テンプレート適用で行上限(256)を超えます")

        due_text = parent.EndDate.isoformat()
        for offset, row in enumerate(template.rows):
            idx = start_row + offset
            self.split_rows[idx].task_name = row.task_name
            self.split_rows[idx].mh_ratio = self._format_ratio_value(Decimal(str(row.ratio)))
            self.split_rows[idx].memo = ""
            self.split_rows[idx].due_date = due_text

        self._sync_split_field_values()

    def _on_split_equalize(self) -> None:
        used_rows = self._split_used_rows()
        if len(used_rows) < 2:
            raise UserInputError("分割は2行以上で入力してください")

        total_hundredths = 10000
        base = total_hundredths // len(used_rows)
        remain = total_hundredths % len(used_rows)

        for order, (row_index, row) in enumerate(used_rows):
            hundredths = base + (1 if order < remain else 0)
            ratio = Decimal(hundredths) / Decimal(100)
            row.mh_ratio = self._format_ratio_value(ratio)
            if not row.due_date:
                row.due_date = self._default_split_due_date()
            self.split_rows[row_index] = row

        self._sync_split_field_values()

    def _on_split_execute(self) -> None:
        parent = self._get_active_split_parent()
        used_rows = self._split_used_rows()
        if len(used_rows) < 2:
            raise UserInputError("分割は2行以上で入力してください")

        ratios: list[Decimal] = []
        for _, row in used_rows:
            ratio_text = row.mh_ratio.strip()
            if not ratio_text:
                raise UserInputError("MH割合を入力してください")
            if "%" in ratio_text:
                raise UserInputError("MH割合は数値のみ入力してください")
            try:
                ratio = Decimal(ratio_text)
            except Exception as exc:
                raise UserInputError("MH割合は数値で入力してください") from exc
            if ratio < 0:
                raise UserInputError("MH割合は0以上で入力してください")
            ratios.append(ratio)

        if sum(ratios) != Decimal("100"):
            raise UserInputError("MH割合の合計を100にしてください")

        parent_units = parent.AssignedManHour // 30
        raw_units = [Decimal(parent_units) * (ratio / Decimal("100")) for ratio in ratios]
        floor_units = [int(unit.to_integral_value(rounding=ROUND_DOWN)) for unit in raw_units]
        remain_units = parent_units - sum(floor_units)
        fractions = [
            (raw_units[idx] - Decimal(floor_units[idx]), idx)
            for idx in range(len(raw_units))
        ]
        fractions.sort(key=lambda item: item[0], reverse=True)
        for _, idx in fractions[:remain_units]:
            floor_units[idx] += 1

        children: list[dict[str, str | int]] = []
        today = date.today()
        for order, (row_index, row) in enumerate(used_rows):
            due_text = row.due_date.strip() or parent.EndDate.isoformat()
            try:
                due_date = date.fromisoformat(due_text)
            except ValueError as exc:
                raise UserInputError("期日はYYYY-MM-DD形式で入力してください") from exc
            if due_date < today or due_date > parent.EndDate:
                raise UserInputError("期日は本日〜親タスク期限の範囲で入力してください")

            children.append(
                {
                    "Name": row.task_name.strip(),
                    "AssignedManHour": floor_units[order] * 30,
                    "Memo": row.memo.strip(),
                    "EndDate": due_date.isoformat(),
                }
            )

        source_index = self.split_return_tab_index
        self.service.split_task(
            parent_task_id=parent.Id,
            operator_user_id=self.selected_user_id,
            children=children,
        )
        self._reset_split_rows_for_active_task()
        self._goto_tab(source_index)

    def _on_save_education_rate(self) -> None:
        user_id = self.admin_education_user.value
        ym = (self.admin_education_month.value or "").strip()
        try:
            rate = float(self.admin_education_rate.value or "0")
        except ValueError as exc:
            raise UserInputError("教育比率は数値で入力してください") from exc
        if not user_id:
            raise UserInputError("教育比率ユーザーを選択してください")
        self.service.set_monthly_education_rate(user_id, ym, rate)

    def _on_save_crash_zone(self) -> None:
        try:
            value = float(self.admin_crash_zone.value or "0")
        except ValueError as exc:
            raise UserInputError("CrashZonePctは数値で入力してください") from exc
        self.service.set_crash_zone_pct(value)

    def _on_create_task(self) -> None:
        name = (self.admin_create_name.value or "").strip()
        assignee = self.admin_create_assignee.value
        due_text = (self.admin_create_due.value or "").strip()
        minutes_text = self.admin_create_minutes.value or "0"
        if not assignee:
            raise UserInputError("担当者を選択してください")
        try:
            due_date = date.fromisoformat(due_text)
        except ValueError as exc:
            raise UserInputError("期限はYYYY-MM-DDで入力してください") from exc
        try:
            minutes = int(minutes_text)
        except ValueError as exc:
            raise UserInputError("工数は整数で入力してください") from exc

        self.service.create_task(
            name=name,
            assignee_user_id=assignee,
            creator_user_id=self.selected_user_id,
            assigned_manhour=minutes,
            end_date=due_date,
            parent_task_id=None,
            planned_deliverables=[],
            is_rework=self.admin_create_is_rework.value,
        )

        self.admin_create_name.value = ""
        self.admin_create_minutes.value = ""

    def _on_set_prevented_by(self) -> None:
        rework_task_id = self.admin_rework_task.value
        prevented_by = self.admin_prevented_by.value
        if not rework_task_id or not prevented_by:
            raise UserInputError("reworkタスクとprevented_byを選択してください")
        self.service.set_rework_prevented_by(rework_task_id, prevented_by)

    def _task_color(self, task_status: str, end_date: date) -> str:
        if task_status == "Completion":
            return "#3A86FF"
        if end_date < date.today():
            return "#E63946"
        return "#FFB703"

    def _build_wbs_row(
        self,
        task: TaskView,
        *,
        is_active: bool,
        has_children: bool,
        is_selected_user_task: bool,
        has_selected_user_descendant: bool,
        show_today_toggle: bool = False,
        today_enabled: bool = False,
        on_today_toggle: Callable[[bool], None] | None = None,
        show_order_buttons: bool = False,
        on_move_up: Callable[[], None] | None = None,
        on_move_down: Callable[[], None] | None = None,
        on_tap: Callable[[], None] | None = None,
        on_double_tap: Callable[[], None] | None = None,
        on_secondary_tap: Callable[[], None] | None = None,
    ) -> ft.Control:
        marker_color = self._task_color(task.Status, task.EndDate)
        if is_selected_user_task:
            active_bg = "#FFF8D6" if is_active else "#FFFDF0"
        elif has_selected_user_descendant:
            active_bg = "#FFFFFF"
        else:
            active_bg = "#C7CDD3" if is_active else "#D6DBDF"
        active_border = "#2A9D8F" if is_active else "#CBEBD8"
        expand_icon = "▶" if has_children else "・"

        trailing_controls: list[ft.Control] = []
        if show_today_toggle:
            trailing_controls.append(
                ft.Switch(
                    value=today_enabled,
                    scale=0.8,
                    tooltip="today",
                    on_change=(
                        (lambda e, handler=on_today_toggle: handler(bool(e.control.value)))
                        if on_today_toggle is not None
                        else None
                    ),
                )
            )

        if show_order_buttons:
            trailing_controls.append(
                ft.Column(
                    [
                        ft.IconButton(
                            icon=ft.Icons.KEYBOARD_ARROW_UP,
                            tooltip="上へ",
                            icon_size=16,
                            on_click=((lambda e, handler=on_move_up: handler()) if on_move_up else None),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                            tooltip="下へ",
                            icon_size=16,
                            on_click=((lambda e, handler=on_move_down: handler()) if on_move_down else None),
                        ),
                    ],
                    spacing=0,
                    tight=True,
                )
            )

        row_body = ft.Container(
            bgcolor=active_bg,
            border=ft.border.all(2 if is_active else 1, active_border),
            border_radius=10,
            padding=ft.padding.symmetric(horizontal=8, vertical=6),
            content=ft.Row(
                [
                    ft.Container(width=6, height=36, bgcolor=marker_color, border_radius=6),
                    ft.Text(expand_icon, size=11, color="#3A5A40"),
                    ft.Column(
                        [
                            ft.Text(task.Name, size=13, weight=ft.FontWeight.W_600),
                            ft.Text(
                                f"{task.AssigneeName} | 期限:{task.EndDate} | 見込:{task.AssignedManHour}分 実績:{task.ActualManHour}分 | {task.ProgressRate:.1f}%",
                                size=11,
                                color="#355070",
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    *trailing_controls,
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        return ft.GestureDetector(
            key=f"wbs-row-{task.Id}",
            content=row_body,
            on_tap=(lambda e, handler=on_tap: handler()) if on_tap else None,
            on_double_tap=(lambda e, handler=on_double_tap: handler()) if on_double_tap else None,
            on_secondary_tap=(lambda e, handler=on_secondary_tap: handler()) if on_secondary_tap else None,
        )

    def _build_wbs_content(self) -> list[ft.Control]:
        tasks = self.service.list_tasks()
        if not tasks:
            self.wbs_visible_task_ids = []
            return [ft.Text("タスクがありません")]

        render = build_wbs_render_state(
            tasks=tasks,
            active_task_id=self.selected_task_id,
            expanded_task_ids=self.wbs_expanded_task_ids,
        )
        self.selected_task_id = render.active_task_id
        self.wbs_visible_task_ids = render.visible_task_ids

        task_by_id = {task.Id: task for task in tasks}
        children_by_parent: dict[str | None, list[TaskView]] = {}
        for task in tasks:
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)
        children_by_parent.setdefault(None, [])
        parent_ids = {task.ParentTaskId for task in tasks if task.ParentTaskId}
        leaf_ids = {task.Id for task in tasks if task.Id not in parent_ids}

        # 各タスクについて「配下(自身除く)に選択ユーザー担当が存在するか」をメモ化する。
        descendant_match_cache: dict[str, bool] = {}

        def has_selected_user_descendant(task_id: str) -> bool:
            cached = descendant_match_cache.get(task_id)
            if cached is not None:
                return cached
            for child in children_by_parent.get(task_id, []):
                if child.AssigneeUserId == self.selected_user_id or has_selected_user_descendant(child.Id):
                    descendant_match_cache[task_id] = True
                    return True
            descendant_match_cache[task_id] = False
            return False

        if not render.sections:
            return [ft.Text("表示対象のタスクがありません")]

        flat_controls: list[ft.Control] = []
        for section in render.sections:
            parent_name = "(ルート)"
            if section.parent_task_id is not None:
                parent_task = task_by_id.get(section.parent_task_id)
                parent_name = parent_task.Name if parent_task else "(不明)"

            active_name = "-"
            if section.active_task_id is not None:
                active_task = task_by_id.get(section.active_task_id)
                active_name = active_task.Name if active_task else "(不明)"
            rows = [
                self._build_wbs_row(
                    task,
                    is_active=(task.Id == self.selected_task_id),
                    has_children=bool(children_by_parent.get(task.Id)),
                    is_selected_user_task=(task.AssigneeUserId == self.selected_user_id),
                    has_selected_user_descendant=has_selected_user_descendant(task.Id),
                    show_today_toggle=(
                        task.AssigneeUserId == self.selected_user_id
                        and task.Id in leaf_ids
                        and task.Status != "Completion"
                    ),
                    today_enabled=self.service.is_today_on(task),
                    on_today_toggle=(
                        lambda enabled, task_id=task.Id: self._run_action(
                            lambda: self._on_toggle_today(task_id, enabled),
                            refresh_scope="all",
                        )
                    ),
                    on_tap=(
                        lambda task_id=task.Id: self._run_action(
                            lambda: self._activate_task_by_click(task_id)
                        )
                    ),
                    on_double_tap=(
                        lambda task_id=task.Id: self._run_action(
                            lambda: self._open_task_detail(task_id)
                        )
                    ),
                    on_secondary_tap=(
                        lambda task_id=task.Id: self._run_action(
                            lambda: self._open_split_view(task_id),
                            refresh_scope="split",
                        )
                    ),
                )
                for task in section.tasks
            ]
            if not rows:
                rows = [ft.Text("タスクなし", size=12, color="#6B7280")]

            flat_controls.append(
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                    bgcolor="#EEF9F4",
                    border=ft.border.all(1, "#CCE3DE"),
                    border_radius=10,
                    content=ft.Text(
                        f"{section.level}段目 | 親:{parent_name} | Active:{active_name}",
                        size=12,
                        color="#355070",
                        weight=ft.FontWeight.W_600,
                    ),
                )
            )
            flat_controls.extend(rows)

        return flat_controls

    def _build_my_task_content(self) -> list[ft.Control]:
        if not self.selected_user_id:
            return [ft.Text("ユーザー未選択", color="#6B7280")]

        all_tasks = self.service.list_tasks()
        my_tasks = self.service.list_my_tasks(self.selected_user_id)
        if not my_tasks:
            return [ft.Text("自分担当の未完了タスクがありません", color="#6B7280")]

        children_by_parent: dict[str | None, list[TaskView]] = {}
        for task in all_tasks:
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)
        parent_ids = {task.ParentTaskId for task in all_tasks if task.ParentTaskId}
        leaf_ids = {task.Id for task in all_tasks if task.Id not in parent_ids}

        return [
            self._build_wbs_row(
                task,
                is_active=(task.Id == self.selected_task_id),
                has_children=bool(children_by_parent.get(task.Id)),
                is_selected_user_task=True,
                has_selected_user_descendant=False,
                show_today_toggle=(task.Id in leaf_ids and task.Status != "Completion"),
                today_enabled=self.service.is_today_on(task),
                on_today_toggle=(
                    lambda enabled, task_id=task.Id: self._run_action(
                        lambda: self._on_toggle_today(task_id, enabled),
                        refresh_scope="all",
                    )
                ),
                on_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._activate_flat_task(task_id),
                        refresh_scope="current",
                    )
                ),
                on_double_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._open_task_detail(task_id)
                    )
                ),
                on_secondary_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._open_split_view(task_id),
                        refresh_scope="split",
                    )
                ),
            )
            for task in my_tasks
        ]

    def _build_today_task_content(self) -> list[ft.Control]:
        if not self.selected_user_id:
            self.today_visible_task_ids = []
            return [ft.Text("ユーザー未選択", color="#6B7280")]

        all_tasks = self.service.list_tasks()
        today_tasks = self.service.list_today_tasks(self.selected_user_id)
        self.today_visible_task_ids = [task.Id for task in today_tasks]
        if not today_tasks:
            return [ft.Text("today対象タスクがありません", color="#6B7280")]

        children_by_parent: dict[str | None, list[TaskView]] = {}
        for task in all_tasks:
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)

        return [
            self._build_wbs_row(
                task,
                is_active=(task.Id == self.selected_task_id),
                has_children=bool(children_by_parent.get(task.Id)),
                is_selected_user_task=True,
                has_selected_user_descendant=False,
                show_today_toggle=True,
                today_enabled=self.service.is_today_on(task),
                on_today_toggle=(
                    lambda enabled, task_id=task.Id: self._run_action(
                        lambda: self._on_toggle_today(task_id, enabled),
                        refresh_scope="all",
                    )
                ),
                show_order_buttons=True,
                on_move_up=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._move_today_task(task_id, -1),
                        refresh_scope="today",
                    )
                ),
                on_move_down=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._move_today_task(task_id, 1),
                        refresh_scope="today",
                    )
                ),
                on_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._activate_flat_task(task_id),
                        refresh_scope="today",
                    )
                ),
                on_double_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._open_task_detail(task_id)
                    )
                ),
                on_secondary_tap=(
                    lambda task_id=task.Id: self._run_action(
                        lambda: self._open_split_view(task_id),
                        refresh_scope="split",
                    )
                ),
            )
            for task in today_tasks
        ]

    def _refresh_today(self) -> None:
        if not self.selected_user_id:
            return

        teachers = [u for u in self.users if u.Id != self.selected_user_id]
        self.today_teacher_select.options = [ft.dropdown.Option(t.Id, t.Name) for t in teachers]
        if self.today_teacher_select.value not in [t.Id for t in teachers]:
            self.today_teacher_select.value = teachers[0].Id if teachers else None

        self.today_task_list.controls = self._build_today_task_content()

        selected_task_name = "-"
        if self.selected_task_id and self.selected_task_id in self.today_visible_task_ids:
            task = self.service.get_task(self.selected_task_id)
            selected_task_name = task.Name
        self.today_target_task_text.value = f"アクティブタスク: {selected_task_name}"

        summary = self.service.get_today_summary(self.selected_user_id)
        if summary.ActiveTimer:
            elapsed = datetime.now() - summary.ActiveTimer.StartedAt
            elapsed_min = int(elapsed.total_seconds() // 60)
            self.today_active_timer_text.value = f"稼働中: {summary.ActiveTimer.TaskName} ({elapsed_min}分)"
        else:
            self.today_active_timer_text.value = "稼働中タイマなし"

    def _refresh_split(self) -> None:
        if not self.selected_task_id:
            self.split_title_text.value = "対象タスク: -"
            self.split_status_text.value = "アクティブタスクがないため操作できません"
            self.split_apply_template_button.disabled = True
            self.split_equal_button.disabled = True
            self.split_execute_button.disabled = True
            self.split_template_select.disabled = True
            return

        try:
            task = self.service.get_task(self.selected_task_id)
        except UserInputError:
            self.selected_task_id = None
            self.split_parent_task_id = None
            self._refresh_split()
            return
        self.split_title_text.value = f"対象タスク: {task.Name} / 見込:{task.AssignedManHour}分 / 期限:{task.EndDate}"
        self.split_status_text.value = "右クリックで遷移 / セル貼り付けでExcel範囲を直接入力できます"

        buttons_enabled = bool(self.selected_task_id)
        self.split_apply_template_button.disabled = not buttons_enabled
        self.split_equal_button.disabled = not buttons_enabled
        self.split_execute_button.disabled = not buttons_enabled
        self.split_template_select.disabled = not buttons_enabled or not bool(self.settings.split_templates)

        if self.split_parent_task_id != self.selected_task_id:
            self.split_parent_task_id = self.selected_task_id
            self._reset_split_rows_for_active_task()

    def _refresh_my_tasks(self) -> None:
        self.my_task_list.controls = self._build_my_task_content()

    def _refresh_tasks(self) -> None:
        self.wbs_list.controls = self._build_wbs_content()

    def _refresh_detail(self) -> None:
        if not self.selected_task_id:
            self.detail_info.value = "WBS / My task / Today / Split タブでタスクを選択してください"
            self.detail_planned.controls = [ft.Text("-")]
            self.detail_outputs.controls = [ft.Text("-")]
            self.detail_memo_input.value = ""
            return

        task = self.service.get_task(self.selected_task_id)
        self.detail_title.value = f"タスク詳細: {task.Name}"
        self.detail_info.value = (
            f"担当:{task.AssigneeName} / 期限:{task.EndDate} / "
            f"Status:{task.Status} / Progress:{task.ProgressRate:.1f}% / "
            f"見込:{task.AssignedManHour}分 / 実績:{task.ActualManHour}分"
        )
        self.detail_memo_input.value = task.Memo

        self.detail_planned.controls = []
        for item in task.PlannedDeliverables:
            self.detail_planned.controls.append(
                ft.Text(f"・{item.get('title', '')} ({item.get('url', '')})", size=12)
            )
        if not self.detail_planned.controls:
            self.detail_planned.controls = [ft.Text("予定成果物なし", size=12)]

        self.detail_outputs.controls = []
        for idx, item in enumerate(task.OutputDeliverables):
            self.detail_outputs.controls.append(
                ft.Row(
                    [
                        ft.Text(f"・{item.get('title', '')} ({item.get('url', '')})", size=12, expand=True),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip="削除",
                            on_click=lambda e, i=idx: self._run_action(
                                lambda: self.service.remove_output_deliverable(self.selected_task_id or "", i)
                            ),
                        ),
                    ]
                )
            )
        if not self.detail_outputs.controls:
            self.detail_outputs.controls = [ft.Text("成果物未登録", size=12)]

    def _build_progress_chart_cache_key(self, dashboard, mode: str) -> tuple:
        history_key = tuple(
            (item.RecordedAt.isoformat(), round(item.ProgressRate, 4))
            for item in dashboard.History
        )
        return (
            mode,
            history_key,
            round(dashboard.ProgressRate, 4),
            round(dashboard.Speed.VActual, 4),
            round(dashboard.Speed.VCritical, 4) if isfinite(dashboard.Speed.VCritical) else "inf",
            round(dashboard.Speed.VSafe, 4) if isfinite(dashboard.Speed.VSafe) else "inf",
            dashboard.Speed.Signal,
            dashboard.ProjectEndDate.isoformat(),
            round(dashboard.CrashZonePct, 4),
        )

    def _compute_speed_series(self, dashboard) -> tuple[list[datetime], list[float], list[float], list[float]]:
        history = dashboard.History
        points: list[datetime] = []
        v_actuals: list[float] = []
        v_criticals: list[float] = []
        v_safes: list[float] = []

        for index in range(1, len(history)):
            prev = history[index - 1]
            now = history[index]
            elapsed_days = (now.RecordedAt - prev.RecordedAt).total_seconds() / 86400.0
            if elapsed_days <= 0:
                continue

            progress_now = now.ProgressRate
            remain_days = max(0.0, float((dashboard.ProjectEndDate - now.RecordedAt.date()).days))
            v_actual = (progress_now - prev.ProgressRate) / elapsed_days
            v_critical = calculate_v_critical(progress_now=progress_now, remain_days=remain_days)
            v_safe = calculate_v_safe(
                progress_now=progress_now,
                remain_days=remain_days,
                crash_zone_pct=dashboard.CrashZonePct,
            )
            if not isfinite(v_critical):
                v_critical = 0.0
            if not isfinite(v_safe):
                v_safe = 0.0

            points.append(now.RecordedAt)
            v_actuals.append(v_actual)
            v_criticals.append(v_critical)
            v_safes.append(v_safe)

        if not points and history:
            points = [history[-1].RecordedAt]
            v_actuals = [dashboard.Speed.VActual if isfinite(dashboard.Speed.VActual) else 0.0]
            v_criticals = [dashboard.Speed.VCritical if isfinite(dashboard.Speed.VCritical) else 0.0]
            v_safes = [dashboard.Speed.VSafe if isfinite(dashboard.Speed.VSafe) else 0.0]

        return points, v_actuals, v_criticals, v_safes

    def _render_progress_chart_png(self, dashboard, mode: str) -> bytes:
        if not HAS_MATPLOTLIB:
            return b""

        fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=140)
        fig.patch.set_facecolor("#FFFFFF")
        ax.set_facecolor("#F9FFFC")
        ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.4, color="#8AA29E")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.tick_params(axis="x", rotation=0, labelsize=9)
        ax.tick_params(axis="y", labelsize=9)

        if mode == "progress":
            x = [item.RecordedAt for item in dashboard.History]
            y = [item.ProgressRate for item in dashboard.History]
            if not x:
                x = [datetime.now()]
                y = [0.0]
            ax.plot(
                x,
                y,
                color="#0EA5A4",
                linewidth=2.4,
                marker="o",
                markersize=3.5,
                label="ProgressRate(%)",
            )
            ax.fill_between(x, y, [0.0] * len(y), color="#A7F3D0", alpha=0.25)
            ax.set_ylabel("Progress (%)", fontsize=10)
            ax.set_ylim(0, max(100.0, max(y) + 8.0))
            ax.set_title("Progress Rate Graph", fontsize=12, loc="left", color="#1F2937")
            ax.legend(loc="upper left", fontsize=9, frameon=False)
        else:
            x, v_actuals, v_criticals, v_safes = self._compute_speed_series(dashboard)
            if not x:
                x = [datetime.now()]
                v_actuals = [0.0]
                v_criticals = [0.0]
                v_safes = [0.0]

            ax.plot(
                x,
                v_actuals,
                color="#2563EB",
                linewidth=2.4,
                marker="o",
                markersize=3.5,
                label="V_actual",
            )
            ax.plot(
                x,
                v_criticals,
                color="#DC2626",
                linewidth=1.8,
                linestyle="--",
                label="V_critical",
            )
            ax.plot(
                x,
                v_safes,
                color="#059669",
                linewidth=1.8,
                linestyle="--",
                label="V_safe",
            )
            ax.axhline(0.0, color="#9CA3AF", linewidth=0.8)

            all_values = [*v_actuals, *v_criticals, *v_safes]
            lo = min(all_values)
            hi = max(all_values)
            if abs(hi - lo) < 0.1:
                lo -= 1.0
                hi += 1.0
            padding = max(0.5, (hi - lo) * 0.12)
            ax.set_ylim(lo - padding, hi + padding)

            ax.set_ylabel("Speed (%/day)", fontsize=10)
            ax.set_title("Speed Graph", fontsize=12, loc="left", color="#1F2937")
            ax.legend(loc="upper left", fontsize=9, frameon=False)

        ax.set_xlabel("Time", fontsize=10)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _refresh_progress(self) -> None:
        dashboard = self.service.get_progress_dashboard()

        color_map = {
            "Blue": "#3A86FF",
            "Yellow": "#F4A261",
            "Red": "#E63946",
        }

        self.progress_signal.value = f"Signal: {dashboard.Speed.Signal}"
        self.progress_signal.color = color_map.get(dashboard.Speed.Signal, "#264653")

        def _fmt_speed(value: float) -> str:
            return f"{value:.2f}" if isfinite(value) else "∞"

        self.progress_stats.value = (
            f"Progress: {dashboard.ProgressRate:.1f}% | "
            f"V_actual: {_fmt_speed(dashboard.Speed.VActual)} | "
            f"V_critical: {_fmt_speed(dashboard.Speed.VCritical)} | "
            f"V_safe: {_fmt_speed(dashboard.Speed.VSafe)}"
        )

        mode = self.progress_mode_select.value or "speed"
        cache_key = self._build_progress_chart_cache_key(dashboard, mode)
        if self.progress_chart_cache_key != cache_key:
            self.progress_chart_cache_png = self._render_progress_chart_png(dashboard, mode)
            self.progress_chart_cache_key = cache_key

        if HAS_MATPLOTLIB and self.progress_chart_cache_png:
            chart_body = ft.Image(
                src=self.progress_chart_cache_png,
                fit=ft.ImageFit.CONTAIN,
                expand=True,
                border_radius=8,
            )
        else:
            chart_body = ft.Text(
                "グラフ描画エンジンが利用できないため、Progress表示を生成できません。",
                color="#9D0208",
            )

        self.progress_chart_area.content = ft.Container(
            content=chart_body,
            padding=10,
            bgcolor="#FFFFFF",
            border=ft.border.all(1, "#CFE8DD"),
            border_radius=10,
            expand=True,
        )

    def _refresh_ranking(self) -> None:
        ym = (self.ranking_year_month.value or "").strip()
        rankings = self.service.get_rankings(self_user_id=self.selected_user_id, year_month=ym)
        metric = self.ranking_metric.value or "THR"
        data = rankings.get(metric, [])

        rows = []
        for item in data:
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(item.Rank))),
                        ft.DataCell(ft.Text(item.UserName + (" (自分)" if item.IsSelf else ""))),
                        ft.DataCell(ft.Text(f"{item.Value:.3f}")),
                        ft.DataCell(ft.Text("公開" if item.IsPublished else "自己のみ")),
                    ]
                )
            )
        self.ranking_table.rows = rows

    def _refresh_admin(self) -> None:
        top = self.service.get_top_manager()
        selected_user = self.user_by_id.get(self.selected_user_id)
        is_top = selected_user is not None and selected_user.Id == top.Id

        self.admin_message.value = (
            f"Top Manager: {top.Name} | 現在ユーザー: {selected_user.Name if selected_user else '-'}"
        )

        controls = [
            self.admin_education_user,
            self.admin_education_month,
            self.admin_education_rate,
            self.admin_education_save,
            self.admin_crash_zone,
            self.admin_crash_save,
            self.admin_create_name,
            self.admin_create_assignee,
            self.admin_create_minutes,
            self.admin_create_due,
            self.admin_create_is_rework,
            self.admin_create_button,
            self.admin_rework_task,
            self.admin_prevented_by,
            self.admin_link_button,
        ]
        for control in controls:
            control.disabled = not is_top

        tasks = self.service.list_tasks()
        self.admin_rework_task.options = [
            ft.dropdown.Option(t.Id, t.Name) for t in tasks if t.IsRework
        ]
        self.admin_prevented_by.options = [
            ft.dropdown.Option(t.Id, t.Name) for t in tasks
        ]

        alerts = self.service.get_infeasibility_alerts()
        user_name_by_id = {u.Id: u.Name for u in self.users}

        lines: list[ft.Control] = []
        team_alert = alerts.get("__team__")
        if team_alert and team_alert["IsInfeasible"]:
            lines.append(
                ft.Text(f"[全体] 不成立 Gap={team_alert['GapMinutes']}分", color="#9D0208")
            )
        else:
            lines.append(ft.Text("[全体] 不成立なし", color="#2A9D8F"))

        for user_id, result in alerts.items():
            if user_id == "__team__":
                continue
            name = user_name_by_id.get(user_id, user_id)
            if result["IsInfeasible"]:
                lines.append(ft.Text(f"[{name}] Gap={result['GapMinutes']}分", color="#9D0208"))
            else:
                lines.append(ft.Text(f"[{name}] 不成立なし", color="#2A9D8F"))

        self.admin_infeasible.controls = lines

    def _refresh_all(self) -> None:
        self._refresh_scope("all")

    async def _background_loop(self) -> None:
        while True:
            try:
                self.service.run_daily_snapshot_if_due()

                if self.current_tab_index == 2:
                    self._refresh_today()
                    self.page.update()
                elif self.current_tab_index == 5:
                    self._refresh_progress()
                    self.page.update()
            except Exception:
                logger.exception("background loop error")
            await asyncio.sleep(5)


def build_app(page: ft.Page, service: TmsService, settings: Settings) -> None:
    TmsFletApp(page=page, service=service, settings=settings)
