"""창 공통 모양: Windows 11 테마(sv-ttk), 아이콘, 글꼴, 다크 모드, 선명한 글자(고해상도 화면).

설정 창과 계정 입력 창이 함께 쓴다. 색은 브리핑 HTML 과 맞춘다.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
import tkinter.font as tkfont
import winreg
from tkinter import ttk
from dataclasses import dataclass
from pathlib import Path

import sv_ttk

ASSETS = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS / "icon.ico"
ICON_PNG = ASSETS / "icon.png"
ICON_HEADER = ASSETS / "icon-64.png"
APP_USER_MODEL_ID = "mailbrief.app"
FONT = "Malgun Gothic"
# 글자 크기 (pt). 테마 기본값은 픽셀이라 화면마다 달라서 pt 로 고정한다.
FONT_SIZES = {
    "SunValleyTitleFont": 18,  # 맨 위 제목
    "SunValleySubtitleFont": 14,  # 단계 번호
    "SunValleyBodyFont": 10,  # 버튼, 입력칸
    "SunValleyBodyStrongFont": 10,  # 칸 이름
    "SunValleyCaptionFont": 9,  # 회색 설명
    "TkDefaultFont": 10,
    "TkTextFont": 10,
    "TkMenuFont": 10,
    "TkHeadingFont": 10,
}
LOG_FONT_SIZE = 9  # 진행 기록


@dataclass(frozen=True)
class Palette:
    ok: str
    fail: str
    muted: str
    log_bg: str
    log_fg: str
    # 아래는 브리핑 HTML 과 같은 색 (주황 강조색, 칸별 색)
    card: str  # 카드 바탕 (테마 기본 카드는 창 바탕과 같은 색이라 따로 칠한다)
    line: str  # 카드 테두리
    ink: str
    accent: str
    accent_hover: str
    accent_soft: str
    ok_soft: str
    reply: str
    reply_soft: str
    review: str
    review_soft: str
    new: str
    new_soft: str
    header: tuple[str, str]  # 머리 띠 그러데이션 (왼쪽, 오른쪽)


LIGHT = Palette(
    ok="#1b7f3b", fail="#c0392b", muted="#6b7280", log_bg="#f6f7f9", log_fg="#1d2330",
    card="#ffffff", line="#e3e6ea", ink="#191f28", accent="#d9480f", accent_hover="#b93c0b", accent_soft="#fff1e8",
    ok_soft="#e6f6ec", reply="#e03131", reply_soft="#fff0f0", review="#c76f00", review_soft="#fff6e5",
    new="#1c7ed6", new_soft="#e7f3ff", header=("#ff9254", "#d2400a"),
)
DARK = Palette(
    ok="#4ade80", fail="#ff8a80", muted="#9aa1ad", log_bg="#202226", log_fg="#e7e9ee",
    card="#2a2c31", line="#3a3d44", ink="#eceef1", accent="#ea5a16", accent_hover="#ff6f2c", accent_soft="#3d2416",
    ok_soft="#183323", reply="#ff6b6b", reply_soft="#3a1d1f", review="#ffb547", review_soft="#3a2d16",
    new="#5aa9ff", new_soft="#162a40", header=("#e8692c", "#a8360a"),
)


def is_dark() -> bool:
    """설정 창에서 고른 테마 (기본 다크). 'Windows 따라가기'면 Windows 앱 모드를 본다."""
    from mailbrief.config import theme_choice

    choice = theme_choice()
    return windows_dark() if choice == "system" else choice == "dark"


def windows_dark() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def prepare_process() -> None:
    """Tk 창을 만들기 전에 한 번. 글자를 선명하게 하고, 작업표시줄에 python 아이콘 대신 이 프로그램 아이콘이 뜨게 한다."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply(root: tk.Misc) -> Palette:
    """테마·글꼴·아이콘을 입히고, 지금 테마의 색을 돌려준다."""
    dark = is_dark()
    sv_ttk.set_theme("dark" if dark else "light", root)
    for name in tkfont.names(root):
        if name.startswith("SunValley") or name in FONT_SIZES:
            font = tkfont.nametofont(name, root)
            font.configure(family=FONT)
            if name in FONT_SIZES:
                font.configure(size=FONT_SIZES[name])
    if ICON_ICO.exists():
        try:
            root.iconbitmap(default=str(ICON_ICO)) if isinstance(root, tk.Tk) else root.iconbitmap(str(ICON_ICO))
        except tk.TclError:
            pass
    colors = DARK if dark else LIGHT
    # 카드 안에 놓는 위젯용 스타일 (바탕만 카드색). 이름이 Panel.T버튼 이면 모양은 기본 TButton 을 따른다.
    style = ttk.Style(root)
    for name in ("Panel.TFrame", "Panel.TLabel", "Panel.TButton", "Panel.Accent.TButton"):
        style.configure(name, background=colors.card)
    style.configure("Panel.TLabel", foreground=colors.ink)
    if dark:
        _dark_title_bar(root)
    return colors


def panel(parent: tk.Misc, colors: Palette, padding: tuple[int, int] = (16, 14)) -> tuple[tk.Frame, ttk.Frame]:
    """흰 카드 (얇은 테두리). (바깥 틀, 안쪽 내용 칸) 을 돌려준다."""
    outer = tk.Frame(parent, background=colors.card, highlightthickness=1, highlightbackground=colors.line, highlightcolor=colors.line)
    inner = ttk.Frame(outer, style="Panel.TFrame", padding=padding)
    inner.pack(fill="both", expand=True)
    return outer, inner


def primary_button(parent: tk.Misc, colors: Palette, text: str, command) -> tk.Button:
    """주황 강조 버튼. 테마의 강조 버튼은 파란색이라 브리핑 색과 맞춰 직접 그린다."""
    button = tk.Button(
        parent, text=text, command=command, font="SunValleyBodyStrongFont", cursor="hand2",
        background=colors.accent, foreground="#ffffff", activebackground=colors.accent_hover, activeforeground="#ffffff",
        relief="flat", borderwidth=0, highlightthickness=0, padx=18, pady=7,
    )
    button.bind("<Enter>", lambda _e: button.configure(background=colors.accent_hover))
    button.bind("<Leave>", lambda _e: button.configure(background=colors.accent))
    return button


def _dark_title_bar(root: tk.Misc) -> None:
    # Windows 11: 제목 표시줄도 어둡게 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass
