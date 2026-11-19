"""Sentry 연결과 데이터 거르기.

원칙: Sentry 에는 "에러 종류"와 "어느 단계에서 실패했는지"만 보낸다.
메일 제목/본문/보낸 사람 등 업무 내용은 절대 보내지 않는다.

그래서 블랙리스트(위험한 것만 지우기)가 아니라 화이트리스트로 동작한다.
scrub_event() 가 허용된 필드만 골라 새 이벤트를 만들고, 나머지는 전부 버린다.
에러 메시지(exception value)도 기본적으로 지운다. 개발자가 직접 쓴 문장임이
보장되는 SafeError 의 메시지만 남긴다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import sentry_sdk

from mailbrief import __version__
from mailbrief.config import Config

REDACTED = "[제거됨]"

# 실패 단계 이름. Sentry 의 stage 태그로 들어간다.
STAGES = (
    "setup",
    "login",
    "list_inbox",
    "list_unread",
    "list_sent",
    "read_body",
    "classify",
    "summarize",
    "restore_unread",
    "render",
    "notify",
)

_ALLOWED_TAGS = {"stage", "diag_id"}
_ALLOWED_CONTEXTS = {"os", "runtime", "trace"}
_ALLOWED_FRAME_KEYS = {
    "filename",
    "function",
    "module",
    "lineno",
    "colno",
    "in_app",
    "context_line",
    "pre_context",
    "post_context",
}
_SAFE_MESSAGE_TAG = "safe_message"


class SafeError(Exception):
    """메시지를 Sentry 로 보내도 되는 에러.

    메시지에는 개발자가 직접 쓴 고정 문장만 넣는다. 메일 내용을 넣지 말 것.
    메일 정보가 필요하면 diagnostics.save() 로 PC 안에만 저장하고 diag_id 를 넘긴다.
    """

    def __init__(self, message: str, *, diag_id: str | None = None):
        super().__init__(message)
        self.diag_id = diag_id


def _safe_messages(exc: BaseException | None) -> set[str]:
    """예외 체인(원인 포함)에서 SafeError 의 메시지만 모은다."""
    found: set[str] = set()
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, SafeError):
            found.add(str(exc))
        exc = exc.__cause__ or exc.__context__
    return found


def _scrub_frame(frame: dict[str, Any]) -> dict[str, Any]:
    # 소스 코드 줄(context_line 등)은 우리 코드라 괜찮지만,
    # 로컬 변수(vars)와 절대 경로(abs_path, PC 사용자명 포함)는 버린다.
    return {k: v for k, v in frame.items() if k in _ALLOWED_FRAME_KEYS}


def _scrub_exception(value: dict[str, Any], safe: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": value.get("type"),
        "module": value.get("module"),
        "value": value.get("value") if value.get("value") in safe else REDACTED,
    }
    if "mechanism" in value:
        out["mechanism"] = {
            k: v for k, v in value["mechanism"].items() if k in {"type", "handled", "exception_id", "parent_id", "source"}
        }
    frames = (value.get("stacktrace") or {}).get("frames")
    if frames:
        out["stacktrace"] = {"frames": [_scrub_frame(f) for f in frames]}
    return out


_CHECK_IN_KEYS = (
    "type", "event_id", "check_in_id", "monitor_slug", "status", "duration", "monitor_config", "environment", "release",
    "timestamp", "sdk",
)


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """before_send 훅. 허용된 필드만 남긴 새 이벤트를 돌려준다."""
    if event.get("type") == "check_in":
        # Cron 체크인도 before_send 를 거친다. 아래 에러용 허용 목록을 쓰면 모니터 이름/상태가 지워져
        # 체크인이 버려진다 (2026-11-18 실제로 겪음). 체크인에는 실행 상태만 있으므로 그 칸만 남긴다.
        clean = {k: event[k] for k in _CHECK_IN_KEYS if k in event}
        trace = (event.get("contexts") or {}).get("trace")
        if trace:
            clean["contexts"] = {"trace": trace}
        return clean

    exc_info = (hint or {}).get("exc_info")
    safe = _safe_messages(exc_info[1] if exc_info else None)
    tags = event.get("tags") or {}

    clean: dict[str, Any] = {
        key: event[key]
        for key in ("event_id", "timestamp", "platform", "level", "release", "environment", "sdk", "fingerprint")
        if key in event
    }
    clean["server_name"] = REDACTED
    clean["tags"] = {k: v for k, v in tags.items() if k in _ALLOWED_TAGS}

    user_id = (event.get("user") or {}).get("id")
    if user_id:
        clean["user"] = {"id": user_id}

    contexts = event.get("contexts") or {}
    clean["contexts"] = {k: v for k, v in contexts.items() if k in _ALLOWED_CONTEXTS}

    if "exception" in event:
        values = event["exception"].get("values") or []
        clean["exception"] = {"values": [_scrub_exception(v, safe) for v in values]}

    # capture_message 로 보낸 문장은 report_safe() 를 거친 경우에만 남긴다.
    if tags.get(_SAFE_MESSAGE_TAG) == "1":
        if "logentry" in event:
            clean["logentry"] = {"message": event["logentry"].get("message")}
        if "message" in event:
            clean["message"] = event["message"]
    elif "exception" not in event:
        # 출처를 알 수 없는 메시지 이벤트(예: logging.error)는 내용을 버린다.
        clean["message"] = REDACTED

    return clean


def init_sentry(config: Config, *, transport: Any = None) -> bool:
    """Sentry 를 켠다. DSN 이 없으면 켜지 않고 False 를 돌려준다."""
    if not config.sentry_dsn and transport is None:
        return False
    sentry_sdk.init(
        dsn=config.sentry_dsn or "https://public@example.invalid/1",
        release=f"mailbrief@{__version__}",
        environment=config.environment,
        # SDK 단계에서도 최대한 덜 모으고, 남은 것은 scrub_event 가 한 번 더 거른다.
        data_collection={
            "user_info": False,
            "http_bodies": [],
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
        },
        max_breadcrumbs=0,
        before_send=scrub_event,
        transport=transport,
    )
    sentry_sdk.set_user({"id": config.user_alias})
    return True


@contextmanager
def stage(name: str) -> Iterator[None]:
    """이 블록에서 난 에러를 '어느 단계에서 실패했는지' 태그와 함께 Sentry 로 보낸다."""
    if name not in STAGES:
        raise ValueError(f"알 수 없는 단계: {name}")
    try:
        yield
    except Exception as exc:
        if not getattr(exc, "_mailbrief_reported", False):
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("stage", name)
                diag_id = getattr(exc, "diag_id", None)
                if diag_id:
                    scope.set_tag("diag_id", diag_id)
                sentry_sdk.capture_exception(exc)
            exc._mailbrief_reported = True  # type: ignore[attr-defined]
        raise


def report_safe(message: str, *, stage_name: str, level: str = "warning") -> None:
    """개발자가 쓴 고정 문장을 Sentry 로 보낸다. 메일 내용을 넣지 말 것."""
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("stage", stage_name)
        scope.set_tag(_SAFE_MESSAGE_TAG, "1")
        sentry_sdk.capture_message(message, level=level)
