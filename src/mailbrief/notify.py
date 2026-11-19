"""Windows 알림. 누르면 브리핑 HTML 이 열린다."""

from __future__ import annotations

from pathlib import Path

from winotify import Notification

from mailbrief.ui import ICON_PNG

APP_ID = "메일 브리핑"
ICON = str(ICON_PNG) if ICON_PNG.exists() else ""


def briefing_ready(path: str, reply_count: int, review_count: int, new_count: int) -> None:
    urgent = f"회신 필요 {reply_count}건" if reply_count else "회신할 요청 없음"
    toast = Notification(
        app_id=APP_ID,
        title=f"메일 브리핑 도착 · {urgent}",
        msg=f"확인·검토 {review_count}건 · 새 메일 {new_count}통",
        launch=Path(path).as_uri(),
        icon=ICON,
    )
    toast.add_actions(label="브리핑 열기", launch=Path(path).as_uri())
    toast.show()


def failed(message: str) -> None:
    Notification(app_id=APP_ID, title="메일 브리핑 실패", msg=message, icon=ICON).show()
