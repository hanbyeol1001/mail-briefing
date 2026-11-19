"""설정 마법사 (tkinter). 메일브리핑.exe 를 그냥 더블클릭하면 이 창이 뜬다.

맨 위: 주황 머리 띠, 오늘의 브리핑 (개수 알약 · 최신 브리핑 보기 · 메일 조회하기 · 진행 막대)
처음 한 번만 설정: 1 계정 입력 → 2 로그인 시험 → 3 그래픽카드·로컬 AI → 4 자동 실행 등록 → 5 메일 조회하기
  (계정 · 자동 실행 · 첫 브리핑이 다 있으면 한 줄로 접힌다)
도구: 브리핑에서 뺄 메일 관리 / 정답지 만들기 · 채점하기 / 데이터 폴더 열기

오래 걸리는 일은 별도 스레드에서 돌리고, 화면 갱신은 root.after 로 메인 스레드에서 한다.
로그 창에는 개수와 상태만 쓴다 (메일 내용 금지).
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime
from tkinter import ttk
from typing import Callable

from mailbrief import __version__, credentials, system, ui
from mailbrief.config import DATA_DIR, FROZEN, SITE, load_config, save_frozen_config, save_theme
from mailbrief.monitoring import init_sentry, stage

LATEST_BRIEFING = DATA_DIR / "briefings" / "최신_브리핑.html"  # briefing.save() 가 매번 덮어쓰는 파일


def _mix(a: str, b: str, t: float) -> str:
    """두 색(#rrggbb) 사이의 색. 머리 띠 그러데이션용."""
    x, y = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#" + "".join(f"{round(p + (q - p) * t):02x}" for p, q in zip(x, y))


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, **kw) -> int:
    """둥근 알약 모양 (양 끝이 반원)."""
    r = (y2 - y1) / 2
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, outline="", **kw)


class Wizard:
    def __init__(self, *, log_text: str = "", position: str = "") -> None:
        """log_text, position: 테마를 바꿔 창을 다시 열 때 이어 받는 진행 기록과 창 위치."""
        from mailbrief.config import PROFILE

        ui.prepare_process()
        self.root = tk.Tk()
        self.colors = c = ui.apply(self.root)
        self.root.title("메일 브리핑" + (f" [{PROFILE} 시험 프로필 - 자동 실행 없음]" if PROFILE else ""))
        self.root.minsize(595, 0)
        if position:
            self.root.geometry(position)
        self.reopen: dict[str, str] | None = None  # 테마를 바꾸면 채워지고, main() 이 창을 새로 연다
        self._drain_job = ""
        self.events: queue.Queue[Callable[[], None]] = queue.Queue()
        self.busy = False
        self.status: dict[str, ttk.Label] = {}
        self.badges: dict[str, tuple[tk.Canvas, str]] = {}  # 단계 번호 동그라미 (캔버스, 번호)
        self.progress = 0.0

        self._header(PROFILE)
        page = ttk.Frame(self.root, padding=(20, 16, 20, 14))
        page.pack(fill="both", expand=True)

        # 오늘의 브리핑: 설정이 끝난 뒤에는 대부분 이걸 하러 창을 연다 (알림을 놓쳤을 때, 하루 중간에 새로 볼 때).
        outer, today = ui.panel(page, c)
        outer.pack(fill="x")
        top = ttk.Frame(today, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="오늘의 브리핑", font="SunValleyBodyStrongFont", style="Panel.TLabel").pack(side="left")
        self.latest_label = ttk.Label(top, text="", font="SunValleyCaptionFont", foreground=c.muted, style="Panel.TLabel")
        self.latest_label.pack(side="left", padx=10)
        self.chips = ttk.Frame(today, style="Panel.TFrame")
        self.chips.pack(fill="x")
        actions = ttk.Frame(today, style="Panel.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ui.primary_button(actions, c, "최신 브리핑 보기", self.on_open_latest).pack(side="left")
        ttk.Button(actions, text="메일 조회하기", style="Panel.TButton", command=self.on_trial).pack(side="left", padx=8)
        # 조회하는 동안만 보이는 진행 막대
        self.progress_box = ttk.Frame(today, style="Panel.TFrame")
        self.progress_label = ttk.Label(self.progress_box, text="", font="SunValleyCaptionFont", foreground=c.muted, style="Panel.TLabel")
        self.progress_label.pack(anchor="w")
        self.progress_bar = tk.Canvas(self.progress_box, height=8, background=c.card, highlightthickness=0)
        self.progress_bar.pack(fill="x", pady=(4, 0))
        self.progress_bar.bind("<Configure>", lambda _e: self._draw_progress())

        # 처음 한 번만 설정: 다 끝났으면 한 줄로 접어 둔다.
        head = ttk.Frame(page)
        head.pack(fill="x", pady=(18, 8))
        self.setup_title = ttk.Label(head, text="처음 한 번만 설정", font="SunValleyBodyStrongFont")
        self.setup_title.pack(side="left")
        self.setup_hint = ttk.Label(head, text="위에서부터 차례로 눌러 주세요.", font="SunValleyCaptionFont", foreground=c.muted)
        self.setup_hint.pack(side="left", padx=10)
        self.setup_toggle = ttk.Button(head, text="접기", command=self.toggle_setup)
        self.steps_outer, steps = ui.panel(page, c, padding=(16, 4))
        self.steps_outer.pack(fill="x")
        steps.columnconfigure(1, weight=1)
        self._step(steps, 0, "account", "그룹웨어 계정 입력", "Windows 자격 증명 관리자에만 저장", "입력", self.on_account)
        self._step(steps, 1, "login", "로그인 시험", "그룹웨어에 들어가지는지 확인", "시험", self.on_login)
        self._step(steps, 2, "ai", "그래픽카드·로컬 AI 확인", "요약용 AI 를 이 PC 에 준비", "확인", self.on_ai)
        self._step(steps, 3, "task", "자동 실행 등록", "월~토 07:30 에 저절로 브리핑", "등록", self.on_task)
        self._step(steps, 4, "trial", "메일 조회하기", "메일을 읽고 브리핑을 만들어요", "조회", self.on_trial, last=True)

        # 도구
        self.tools_head = self._section(page, "도구", None)
        outer, tools = ui.panel(page, c, padding=(16, 4))
        outer.pack(fill="x")
        tools.columnconfigure(0, weight=1)
        self._tool(tools, 0, "브리핑에서 뺄 메일", "광고·자동 알림처럼 안 봐도 되는 메일", [("관리", self.on_exclude)])
        self._tool(
            tools, 1, "정답지 (선택)", "패턴이 메일을 잘 골라내는지 확인할 때만",
            [("만들기", self.on_answers_export), ("채점하기", self.on_answers_score)],
        )
        self._tool(tools, 2, "데이터 폴더", "브리핑과 설정이 저장된 곳", [("열기", lambda: os.startfile(DATA_DIR))], last=True)

        # 진행 기록
        self._section(page, "진행 기록", None)
        log_frame = tk.Frame(page, background=c.card, highlightthickness=1, highlightbackground=c.line, highlightcolor=c.line)
        log_frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(
            log_frame, height=5, width=40, state="disabled", wrap="word", relief="flat", borderwidth=0, padx=12, pady=10,
            font=(ui.FONT, ui.LOG_FONT_SIZE), background=c.card, foreground=c.log_fg, highlightthickness=0,
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)
        if log_text:
            self.log(log_text.rstrip("\n"))

        self._ensure_alias()
        self.refresh_status()
        # 빨리 알 수 있는 것(계정, 브리핑 파일)만 먼저 보고 접어 둔다. 느린 작업 스케줄러 확인은 check_setup 이 마저 한다.
        # 이렇게 안 하면 창을 열 때마다(테마를 바꿀 때도) 설정 단계가 잠깐 펼쳐졌다 접힌다.
        if credentials.load() is not None and LATEST_BRIEFING.exists():
            self._set_setup_done(True)
        self.check_setup()
        self._drain_job = self.root.after(100, self._drain)

    # ── 화면 도우미 ──

    def _header(self, profile: str) -> None:
        """주황 머리 띠: 아이콘 + 이름 + 버전. 창 너비가 바뀌면 그러데이션을 다시 그린다."""
        title_font, caption_font = tkfont.nametofont("SunValleyTitleFont"), tkfont.nametofont("SunValleyCaptionFont")
        height = title_font.metrics("linespace") + caption_font.metrics("linespace") + 30
        canvas = tk.Canvas(self.root, height=height, highlightthickness=0, borderwidth=0)
        canvas.pack(fill="x")
        if ui.ICON_HEADER.exists():
            self._logo = tk.PhotoImage(file=str(ui.ICON_HEADER))
        left, right = self.colors.header

        def draw(_event=None) -> None:
            canvas.delete("all")
            width = canvas.winfo_width()
            for x in range(0, width, 2):
                canvas.create_line(x, 0, x, height, fill=_mix(left, right, x / max(width, 1)), width=2)
            text_x = 22
            if ui.ICON_HEADER.exists():
                canvas.create_image(18, height // 2, image=self._logo, anchor="w")
                text_x = 18 + self._logo.width() + 12
            mid = height // 2
            canvas.create_text(text_x, mid + 2, text="메일 브리핑", font=title_font, fill="#ffffff", anchor="sw")
            canvas.create_text(
                text_x, mid + 4, text=f"v{__version__} · 메일은 읽기만 하고 보내거나 지우지 않아요",
                font=caption_font, fill="#ffe6d8", anchor="nw",
            )
            right_edge = self._theme_switch(canvas, width - 18, mid, caption_font, right)
            if profile:
                canvas.create_text(right_edge - 14, mid, text=f"시험 프로필 · {profile}", font=caption_font, fill="#ffffff", anchor="e")

        canvas.bind("<Configure>", draw)

    def _theme_switch(self, canvas: tk.Canvas, right: int, mid: int, font: tkfont.Font, base: str) -> int:
        """머리 띠 오른쪽의 Light | Dark 알약. 지금 테마 쪽이 흰색. 왼쪽 끝 x 를 돌려준다."""
        current = "dark" if ui.is_dark() else "light"
        pad, height = 12, font.metrics("linespace") + 10
        widths = [font.measure(label) + 2 * pad for label in ("Light", "Dark")]
        left = right - sum(widths) - 6
        top, bottom = mid - height // 2, mid + height // 2
        track = _mix(base, "#000000", 0.25)
        _round_rect(canvas, left, top, right, bottom, fill=track)
        x = left + 3
        for (choice, label), w in zip((("light", "Light"), ("dark", "Dark")), widths):
            tag = f"theme-{choice}"
            active = choice == current
            # 안 고른 쪽도 바탕과 같은 색 칸을 깔아야 글자 밖을 눌러도 눌린다 (빈 칸은 클릭을 못 받음)
            _round_rect(canvas, x, top + 3, x + w, bottom - 3, fill="#ffffff" if active else track, tags=tag)
            canvas.create_text(x + w / 2, mid, text=label, font=font, fill=base if active else "#ffffff", tags=tag)
            if not active:
                canvas.tag_bind(tag, "<Button-1>", lambda _e, c=choice: self.set_theme(c))
                canvas.tag_bind(tag, "<Enter>", lambda _e: canvas.config(cursor="hand2"))
                canvas.tag_bind(tag, "<Leave>", lambda _e: canvas.config(cursor=""))
            x += w
        return left

    def set_theme(self, choice: str) -> None:
        """테마를 저장하고 창을 새 색으로 다시 연다 (직접 칠한 색이 많아 그 자리에서 바꾸면 섞여 보인다)."""
        if self.busy:
            self.log("작업이 끝난 뒤에 테마를 바꿔 주세요.")
            return
        save_theme(choice)
        self.reopen = {
            "log_text": self.log_box.get("1.0", "end"),
            "position": f"+{self.root.winfo_x()}+{self.root.winfo_y()}",
        }
        if self._drain_job:
            self.root.after_cancel(self._drain_job)
        # 클릭 처리 도중에 창을 없애면 Tk 가 죽는다. 클릭이 끝난 다음에 닫는다.
        self.root.after(10, self.root.destroy)

    def _section(self, parent: ttk.Frame, title: str, hint: str | None) -> ttk.Frame:
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(18, 8))
        ttk.Label(box, text=title, font="SunValleyBodyStrongFont").pack(side="left")
        if hint:
            ttk.Label(box, text=hint, font="SunValleyCaptionFont", foreground=self.colors.muted).pack(side="left", padx=10)
        return box

    def _step(
        self, parent: ttk.Frame, row: int, key: str, title: str, hint: str, button: str, command: Callable[[], None],
        *, last: bool = False,
    ) -> None:
        r = row * 2
        pad = {"pady": 5}
        size = round(tkfont.nametofont("SunValleyBodyFont").metrics("linespace") * 1.45)
        badge = tk.Canvas(parent, width=size, height=size, background=self.colors.card, highlightthickness=0)
        badge.grid(row=r, column=0, sticky="w", padx=(0, 12), **pad)
        self.badges[key] = (badge, str(row + 1))
        self._draw_badge(key, None)
        text = ttk.Frame(parent, style="Panel.TFrame")
        text.grid(row=r, column=1, sticky="w", **pad)
        ttk.Label(text, text=title, style="Panel.TLabel").pack(anchor="w")
        ttk.Label(text, text=hint, font="SunValleyCaptionFont", foreground=self.colors.muted, style="Panel.TLabel").pack(anchor="w")
        status = ttk.Label(parent, text="", font="SunValleyCaptionFont", style="Panel.TLabel")
        status.grid(row=r, column=2, sticky="e", padx=12, **pad)
        ttk.Button(parent, text=button, width=7, style="Panel.TButton", command=command).grid(row=r, column=3, sticky="e", **pad)
        if not last:
            ttk.Separator(parent).grid(row=r + 1, column=0, columnspan=4, sticky="ew")
        self.status[key] = status

    def _tool(
        self, parent: ttk.Frame, row: int, title: str, hint: str, buttons: list[tuple[str, Callable[[], None]]],
        *, last: bool = False,
    ) -> None:
        r = row * 2
        text = ttk.Frame(parent, style="Panel.TFrame")
        text.grid(row=r, column=0, sticky="w", pady=5)
        ttk.Label(text, text=title, style="Panel.TLabel").pack(anchor="w")
        ttk.Label(text, text=hint, font="SunValleyCaptionFont", foreground=self.colors.muted, style="Panel.TLabel").pack(anchor="w")
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.grid(row=r, column=1, sticky="e", pady=5)
        for i, (label, command) in enumerate(buttons):
            ttk.Button(box, text=label, style="Panel.TButton", command=command).pack(side="left", padx=(6 if i else 0, 0))
        if not last:
            ttk.Separator(parent).grid(row=r + 1, column=0, columnspan=2, sticky="ew")

    def _draw_badge(self, key: str, ok: bool | None) -> None:
        """단계 번호 동그라미: 할 일은 연한 주황 + 번호, 끝나면 초록 + ✓, 실패하면 빨강 + !"""
        canvas, number = self.badges[key]
        c = self.colors
        fill, ink, text = {
            True: (c.ok, "#ffffff", "✓"), False: (c.reply_soft, c.reply, "!"), None: (c.accent_soft, c.accent, number),
        }[ok]
        size = int(canvas["width"])
        canvas.delete("all")
        canvas.create_oval(1, 1, size - 1, size - 1, fill=fill, outline="")
        canvas.create_text(size / 2, size / 2, text=text, fill=ink, font="SunValleyBodyStrongFont")

    def _draw_progress(self) -> None:
        bar = self.progress_bar
        bar.delete("all")
        width, y = bar.winfo_width(), 4
        bar.create_line(4, y, width - 4, y, fill=self.colors.line, width=8, capstyle="round")
        if self.progress > 0:
            bar.create_line(4, y, 4 + (width - 8) * min(self.progress, 1.0), y, fill=self.colors.accent, width=8, capstyle="round")

    def set_progress(self, label: str | None, done: float = 0.0) -> None:
        """조회 진행 막대. label 이 None 이면 막대를 숨긴다. 어느 스레드에서 불러도 된다."""

        def apply() -> None:
            if label is None:
                self.progress_box.pack_forget()
                return
            self.progress = done
            self.progress_label.config(text=label)
            if not self.progress_box.winfo_ismapped():
                self.progress_box.pack(fill="x", pady=(12, 0))
            self._draw_progress()

        self.events.put(apply)

    def _show_counts(self) -> None:
        """'오늘의 브리핑' 칸의 개수 알약 (회신 · 확인 · 새 메일 · 오늘 마감)."""
        from mailbrief import briefing

        for child in self.chips.winfo_children():
            child.destroy()
        counts = briefing.load_counts() if LATEST_BRIEFING.exists() else None
        if not counts:
            return
        c = self.colors
        chips = [
            (f"회신 {counts['reply']}", c.reply_soft, c.reply),
            (f"확인 {counts['review']}", c.review_soft, c.review),
            (f"새 메일 {counts['new']}", c.new_soft, c.new),
        ]
        if counts.get("due_today"):
            chips.append((f"오늘 마감 {counts['due_today']}", "#e03131", "#ffffff"))
        for i, (text, bg, fg) in enumerate(chips):
            tk.Label(self.chips, text=text, background=bg, foreground=fg, font="SunValleyCaptionFont", padx=10, pady=2).pack(
                side="left", padx=(0 if i == 0 else 6, 0), pady=(10, 0)
            )

    def toggle_setup(self) -> None:
        if self.steps_outer.winfo_manager():
            self.steps_outer.pack_forget()
            self.setup_toggle.config(text="설정 다시 보기")
        else:
            self.steps_outer.pack(fill="x", before=self.tools_head)
            self.setup_toggle.config(text="접기")

    def _set_setup_done(self, done: bool) -> None:
        from mailbrief.config import PROFILE

        if done:
            self.setup_title.config(text="✓ 처음 설정 끝", foreground=self.colors.ok)
            self.setup_hint.config(text="시험 프로필이라 자동 실행은 없어요" if PROFILE else "월~토 07:30 에 저절로 브리핑해요")
            self.setup_toggle.pack(side="right")
            if self.steps_outer.winfo_manager():
                self.toggle_setup()
        else:
            self.setup_title.config(text="처음 한 번만 설정", foreground="")
            self.setup_hint.config(text="위에서부터 차례로 눌러 주세요.")
            self.setup_toggle.pack_forget()
            if not self.steps_outer.winfo_manager():
                self.toggle_setup()

    def check_setup(self) -> None:
        """계정 · 자동 실행 · 첫 브리핑이 다 있으면 '처음 설정 끝'으로 접는다. 작업 스케줄러 확인이 느려서 스레드에서."""
        from mailbrief.config import PROFILE

        def job() -> None:
            has_account = credentials.load() is not None
            registered = system.task_registered()
            has_briefing = LATEST_BRIEFING.exists()
            if registered:
                self.set_status("task", "등록됨", True)
            if has_briefing:
                self.set_status("trial", "완료", True)
            done = has_account and has_briefing and (registered or bool(PROFILE))
            self.events.put(lambda: self._set_setup_done(done))

        threading.Thread(target=job, daemon=True).start()

    def set_status(self, key: str, text: str, ok: bool | None = None) -> None:
        # ✓ / ! 표시는 왼쪽 번호 동그라미가 맡고, 오른쪽에는 글만 색을 입혀 쓴다.
        color = {True: self.colors.ok, False: self.colors.fail, None: self.colors.muted}[ok]

        def apply() -> None:
            self.status[key].config(text=text, foreground=color)
            self._draw_badge(key, ok)

        self.events.put(apply)

    def log(self, text: str) -> None:
        def write() -> None:
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")

        self.events.put(write)

    def _drain(self) -> None:
        while not self.events.empty():
            self.events.get()()
        self._drain_job = self.root.after(100, self._drain)

    def background(self, job: Callable[[], None], key: str | None = None) -> None:
        """오래 걸리는 일을 스레드에서 돌린다. 동시에 하나만. 실패하면 key 단계의 상태를 '실패'로 바꾼다."""
        if self.busy:
            self.log("다른 작업이 끝날 때까지 기다려 주세요.")
            return
        self.busy = True

        def wrapper() -> None:
            try:
                job()
            except Exception as exc:  # 화면에는 종류만, Sentry 에는 stage() 가 이미 보냄
                self.log(f"실패: {exc}")
                if key:
                    self.set_status(key, "실패", False)
            finally:
                self.busy = False

        threading.Thread(target=wrapper, daemon=True).start()

    # ── 상태 ──

    def _ensure_alias(self) -> None:
        from mailbrief.config import PROFILE

        if not FROZEN and not PROFILE:
            return
        config = load_config()
        if config.user_alias == "user-unknown":
            # 배포본 기본 별칭. 사람이 늘면 config.json 에서 바꾼다.
            save_frozen_config({"MAILBRIEF_USER_ALIAS": "user-2", "SENTRY_ENVIRONMENT": "dev"})

    def refresh_status(self) -> None:
        creds = credentials.load()
        self.set_status("account", "저장됨" if creds else "아직 없음", True if creds else None)
        if LATEST_BRIEFING.exists():
            made = datetime.fromtimestamp(LATEST_BRIEFING.stat().st_mtime)
            text = f"{made:%m/%d}({'월화수목금토일'[made.weekday()]}) {made:%H:%M} 에 만듦"
        else:
            text = "아직 없어요 · '메일 조회하기'를 눌러 보세요"
        self.events.put(lambda: (self.latest_label.config(text=text), self._show_counts()))

    def on_open_latest(self) -> None:
        if LATEST_BRIEFING.exists():
            os.startfile(LATEST_BRIEFING)
        else:
            self.log("아직 만든 브리핑이 없습니다. 5번 '메일 조회하기'를 눌러 주세요.")

    # ── 단계 ──

    def on_account(self) -> None:
        creds = credentials.ask_with_dialog(self.root)
        if creds:
            credentials.save(creds)
            self.log("계정을 Windows 자격 증명 관리자에 저장했습니다.")
        self.refresh_status()
        self.check_setup()

    def on_login(self) -> None:
        def job() -> None:
            from mailbrief import groupware

            creds = credentials.load()
            if not creds:
                self.set_status("login", "1번 먼저", False)
                return
            self.set_status("login", "확인 중...")
            with stage("login"), groupware.open_browser() as browser:
                groupware.login(browser.new_context().new_page(), creds)
            self.set_status("login", "성공", True)
            self.log("그룹웨어 로그인에 성공했습니다.")

        self.background(job, "login")

    def on_ai(self) -> None:
        def job() -> None:
            found = system.gpus()
            for g in found:
                vram = f", 메모리 {g.vram_gb}GB" if g.vram_gb else ""
                self.log(f"그래픽카드: {g.name}{vram} {'(AI 가속 가능)' if g.usable else ''}")
            if not any(g.usable for g in found):
                self.log("NVIDIA 그래픽카드가 없어 CPU 로 요약합니다. 메일 한 통에 수십 초 걸릴 수 있습니다.")

            if not system.ollama_exe():
                self.set_status("ai", "Ollama 설치 필요", False)
                self.log("Ollama 가 없습니다. 열리는 페이지에서 설치한 뒤 3번을 다시 눌러 주세요.")
                webbrowser.open(system.OLLAMA_DOWNLOAD_URL)
                return
            system.start_ollama()
            for _ in range(20):
                if system.ollama_running():
                    break
                threading.Event().wait(1)
            if not system.model_ready():
                self.set_status("ai", "모델 내려받는 중...")
                system.pull_model(lambda t: self.set_status("ai", t))
            self.set_status("ai", "준비됨", True)
            self.log("로컬 AI(Ollama, qwen3:4b) 준비가 끝났습니다.")

        self.background(job, "ai")

    def on_task(self) -> None:
        def job() -> None:
            with stage("setup"):
                next_run = system.register_task()
            self.set_status("task", "등록됨", True)
            self.log(f"작업 스케줄러에 '{system.TASK_NAME}' 을 등록했습니다. 월~토 07:30 에 실행됩니다 (다음 실행 {next_run}).")
            self.check_setup()

        self.background(job, "task")

    def on_trial(self) -> None:
        def job() -> None:
            from mailbrief.runner import run_briefing

            self.set_status("trial", "만드는 중...")
            self.set_progress("시작하는 중", 0.0)
            try:
                result = run_briefing(log=self.log, use_notify=False, progress=self.set_progress)
            finally:
                self.set_progress(None)
            self.set_status("trial", "완료", True)
            self.refresh_status()
            self.check_setup()
            os.startfile(result.path)

        self.background(job, "trial")

    def on_exclude(self) -> None:
        from mailbrief import briefing

        win = tk.Toplevel(self.root)
        win.title("브리핑에서 뺄 메일")
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        defaults = [*SITE.exclude_senders, *(f"'{s}' 제목" for s in SITE.exclude_subjects)]
        defaults += [f"{w} 알림(회신 필요·새 메일일 때만 표시)" for w in SITE.alert_words]
        ttk.Label(
            frame,
            text="여기 넣은 메일은 다음 브리핑부터 빠집니다. 그룹웨어 메일은 그대로예요."
            + (f"\n기본으로 빠지는 것: {', '.join(defaults)}" if defaults else ""),
            font="SunValleyCaptionFont", foreground=self.colors.muted, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        extra = briefing.load_user_exclude()

        def column(col: int, key: str, title: str, hint: str) -> None:
            box = ttk.Frame(frame)
            box.grid(row=1, column=col, sticky="n", padx=(0, 16) if col == 0 else 0)
            ttk.Label(box, text=title, font="SunValleyBodyStrongFont").pack(anchor="w")
            ttk.Label(box, text=hint, font="SunValleyCaptionFont", foreground=self.colors.muted).pack(anchor="w", pady=(0, 6))
            items = tk.Listbox(
                box, height=8, width=26, activestyle="none", relief="flat", highlightthickness=1,
                font=(ui.FONT, ui.LOG_FONT_SIZE), background=self.colors.log_bg, foreground=self.colors.log_fg,
            )
            items.pack(fill="x")
            for value in extra[key]:
                items.insert("end", value)
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(8, 0))
            entry = ttk.Entry(row, width=16)
            entry.pack(side="left", fill="x", expand=True)

            def add(_event=None) -> None:
                value = entry.get().strip()
                if len(value) < 2:
                    self.log("두 글자 이상 입력해 주세요. (너무 짧으면 엉뚱한 메일까지 빠져요)")
                    return
                if value not in extra[key]:
                    extra[key].append(value)
                    items.insert("end", value)
                    briefing.save_user_exclude(extra)
                    self.log(f"'{value}' 을(를) 빼는 목록에 넣었습니다. 다음 브리핑부터 적용돼요.")
                entry.delete(0, "end")

            def remove() -> None:
                for index in reversed(items.curselection()):
                    value = items.get(index)
                    items.delete(index)
                    extra[key].remove(value)
                    self.log(f"'{value}' 을(를) 빼는 목록에서 지웠습니다.")
                briefing.save_user_exclude(extra)

            entry.bind("<Return>", add)
            ttk.Button(row, text="추가", command=add).pack(side="left", padx=(6, 0))
            ttk.Button(box, text="고른 것 지우기", command=remove).pack(anchor="e", pady=(6, 0))

        column(0, "senders", "보낸 사람", "이름 일부만 써도 돼요")
        column(1, "subject_keywords", "제목에 들어간 말", "예: [광고], 뉴스레터")
        ttk.Button(frame, text="닫기", style="Accent.TButton", command=win.destroy).grid(
            row=2, column=0, columnspan=2, sticky="e", pady=(16, 0)
        )
        win.grab_set()

    def on_answers_export(self) -> None:
        def job() -> None:
            from mailbrief import answers

            alias = load_config().user_alias
            self.log("최근 30일 메일을 읽어 정답지를 만드는 중입니다... (몇 분 걸릴 수 있음)")
            total, answered = answers.export(alias, 30, sample=20)
            self.log(
                f"메일 {total}통으로 정답지를 만들었습니다 (기존 정답 {answered}건 보존). "
                "노란 칸(정답_요청, 정답_종류, 정답_기한)을 채워 저장한 뒤 '채점하기'를 눌러 주세요."
            )
            os.startfile(answers.ANSWERS_DIR / f"정답지_{alias}.xlsx")

        self.background(job)

    def on_answers_score(self) -> None:
        def job() -> None:
            from mailbrief import answers

            alias = load_config().user_alias
            result = answers.score_answers(alias)
            self.log("── 아래 줄들을 복사해서 관리자에게 보내 주세요 (메일 내용은 들어 있지 않습니다) ──")
            self.log(result.summary())
            note = answers.sample_note(alias, result)
            if note:
                self.log(note)
            self.log(f"놓침 {result.missed_keys} / 잘못 잡음 {result.false_alarm_keys} / 기한 틀림 {result.deadline_wrong_keys} / 종류 틀림 {result.kind_wrong_keys}")

        self.background(job)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    init_sentry(load_config())
    carry: dict[str, str] = {}
    while True:  # 테마를 바꾸면 같은 자리에 새 색으로 다시 연다
        wizard = Wizard(**carry)
        wizard.run()
        if wizard.reopen is None:
            break
        carry = wizard.reopen
