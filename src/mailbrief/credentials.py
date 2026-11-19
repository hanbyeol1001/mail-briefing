"""그룹웨어 계정 저장. Windows 자격 증명 관리자(keyring)에만 저장한다."""

from __future__ import annotations

from dataclasses import dataclass

import keyring
import keyring.errors

from mailbrief.config import PROFILE

# 프로필마다 저장 칸을 나눈다. 기본 프로필은 예전 이름 그대로라 기존 저장 계정이 유지된다.
SERVICE = f"mailbrief-groupware:{PROFILE}" if PROFILE else "mailbrief-groupware"
_USERID_KEY = "__userid__"


@dataclass(frozen=True)
class Credentials:
    userid: str
    password: str


def load() -> Credentials | None:
    userid = keyring.get_password(SERVICE, _USERID_KEY)
    if not userid:
        return None
    password = keyring.get_password(SERVICE, userid)
    if not password:
        return None
    return Credentials(userid, password)


def save(creds: Credentials) -> None:
    old = keyring.get_password(SERVICE, _USERID_KEY)
    if old and old != creds.userid:
        keyring.delete_password(SERVICE, old)
    keyring.set_password(SERVICE, _USERID_KEY, creds.userid)
    keyring.set_password(SERVICE, creds.userid, creds.password)


def delete() -> bool:
    """저장된 계정을 지운다 (시험 프로필 정리용). 지운 게 있으면 True."""
    userid = keyring.get_password(SERVICE, _USERID_KEY)
    if not userid:
        return False
    for key in (userid, _USERID_KEY):
        try:
            keyring.delete_password(SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass
    return True


def ask_with_dialog(parent=None) -> Credentials | None:
    """아이디/비밀번호 입력 창을 띄운다. 취소하면 None. parent 가 있으면 그 창의 자식 창으로 띄운다."""
    import tkinter as tk
    from tkinter import ttk

    from mailbrief import ui

    result: list[Credentials] = []
    if parent is None:
        ui.prepare_process()
    root = tk.Toplevel(parent) if parent is not None else tk.Tk()
    colors = ui.apply(root)
    root.title("그룹웨어 계정" + (f" [{PROFILE} 프로필]" if PROFILE else ""))
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=(22, 18))
    frame.pack()
    ttk.Label(frame, text="그룹웨어 계정 입력", font="SunValleySubtitleFont").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        frame, text="그룹웨어 아이디와 비밀번호예요. 이 PC 의 Windows 자격 증명 관리자에만 저장돼요.",
        font="SunValleyCaptionFont", foreground=colors.muted,
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))
    ttk.Label(frame, text="아이디").grid(row=2, column=0, sticky="w", padx=(0, 12))
    userid_entry = ttk.Entry(frame, width=30)
    userid_entry.grid(row=2, column=1, sticky="ew", pady=4)
    ttk.Label(frame, text="비밀번호").grid(row=3, column=0, sticky="w", padx=(0, 12))
    password_entry = ttk.Entry(frame, width=30, show="•")
    password_entry.grid(row=3, column=1, sticky="ew", pady=4)

    current = load()
    if current:
        userid_entry.insert(0, current.userid)

    show = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frame, text="비밀번호 보기", variable=show, command=lambda: password_entry.config(show="" if show.get() else "•")
    ).grid(row=5, column=1, sticky="w", pady=(2, 0))

    warning = ttk.Label(frame, text="", foreground=colors.fail, font="SunValleyCaptionFont")
    warning.grid(row=4, column=0, columnspan=2, sticky="w")

    def on_key(event) -> None:
        # Windows 에서 event.state 의 0x2 비트가 Caps Lock 이다.
        caps = bool(event.state & 0x2)
        if caps:
            warning.config(text="Caps Lock 이 켜져 있어요.")
        elif str(warning.cget("text")) == "Caps Lock 이 켜져 있어요.":
            warning.config(text="")

    password_entry.bind("<KeyRelease>", on_key)

    def on_ok(_event=None) -> None:
        userid, password = userid_entry.get().strip(), password_entry.get()
        if not (userid and password):
            return
        # 비밀번호 칸은 * 로만 보여서 한/영 키가 한글 상태인 걸 알아채기 어렵다 (실제로 겪음).
        if not (userid.isascii() and password.isascii()):
            warning.config(text="한글이 섞여 있어요. 한/영 키를 영문으로 바꾸고 다시 입력해 주세요.")
            password_entry.delete(0, "end")
            password_entry.focus_set()
            return
        result.append(Credentials(userid, password))
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(18, 0))
    ttk.Button(buttons, text="취소", width=8, command=root.destroy).pack(side="right")
    ttk.Button(buttons, text="저장", width=8, style="Accent.TButton", command=on_ok).pack(side="right", padx=8)
    root.bind("<Return>", on_ok)
    (password_entry if current else userid_entry).focus_force()
    if parent is not None:
        root.transient(parent)
        root.grab_set()
        parent.wait_window(root)
    else:
        root.mainloop()
    return result[0] if result else None
