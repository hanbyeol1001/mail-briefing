"""Sentry 로 업무 내용이 새지 않는지 확인하는 테스트. 실제 전송은 하지 않는다."""

import json

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from mailbrief.config import Config
from mailbrief.monitoring import REDACTED, SafeError, init_sentry, report_safe, scrub_event, stage

# 검사용 표식은 일부러 조각내서 만든다. Sentry 는 스택의 소스 코드 줄도 보내기 때문에,
# 표식을 소스에 통째로 쓰면 assert 문 자체가 '유출'로 잡힌다.
MARKER = "SEC" + "RET-SUBJECT"
SENDER_MARKER = "secret" + ".sender"
SECRET = f"{MARKER} 김비밀 {SENDER_MARKER}@example.com"


class _CaptureTransport(Transport):
    def __init__(self):
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type in ("event", "check_in"):
                self.events.append(item.payload.json)


@pytest.fixture
def sent():
    transport = _CaptureTransport()
    init_sentry(Config(sentry_dsn="", user_alias="user-1", environment="test"), transport=transport)
    yield transport.events
    sentry_sdk.get_client().close()


def _dump(events) -> str:
    return json.dumps(events, ensure_ascii=False)


def test_exception_message_and_locals_are_removed(sent):
    with pytest.raises(ValueError):
        with stage("read_body"):
            subject = SECRET  # noqa: F841
            raise ValueError(f"본문 해석 실패: {SECRET}")
    sentry_sdk.flush()

    assert len(sent) == 1
    assert MARKER not in _dump(sent)
    assert SENDER_MARKER not in _dump(sent)
    event = sent[0]
    assert event["tags"] == {"stage": "read_body"}
    assert event["user"] == {"id": "user-1"}
    assert event["exception"]["values"][-1]["type"] == "ValueError"
    assert event["exception"]["values"][-1]["value"] == REDACTED


def test_safe_error_keeps_message_and_diag_id(sent):
    with pytest.raises(SafeError):
        with stage("classify"):
            raise SafeError("기한 날짜 해석 실패", diag_id="abc123")
    sentry_sdk.flush()

    event = sent[0]
    assert event["exception"]["values"][-1]["value"] == "기한 날짜 해석 실패"
    assert event["tags"] == {"stage": "classify", "diag_id": "abc123"}


def test_safe_error_wrapping_unsafe_cause_hides_cause(sent):
    with pytest.raises(SafeError):
        with stage("login"):
            try:
                raise RuntimeError(SECRET)
            except RuntimeError as exc:
                raise SafeError("로그인 화면을 찾지 못함") from exc
    sentry_sdk.flush()

    assert MARKER not in _dump(sent)
    values = {v["type"]: v["value"] for v in sent[0]["exception"]["values"]}
    assert values == {"RuntimeError": REDACTED, "SafeError": "로그인 화면을 찾지 못함"}


def test_unknown_message_is_removed_but_report_safe_is_kept(sent):
    sentry_sdk.capture_message(f"logging 에서 온 메시지 {SECRET}")
    report_safe("읽음 복구 실패", stage_name="restore_unread")
    sentry_sdk.flush()

    assert MARKER not in _dump(sent)
    assert "읽음 복구 실패" in _dump(sent)
    assert all("safe_message" not in e["tags"] for e in sent)


def test_scrub_event_drops_everything_not_allowed():
    event = {
        "event_id": "1",
        "level": "error",
        "server_name": "DK-KIM-PC",
        "tags": {"stage": "login", "subject": SECRET},
        "extra": {"body": SECRET},
        "request": {"url": SECRET},
        "breadcrumbs": {"values": [{"message": SECRET}]},
        "user": {"id": "user-1", "email": "user@example.com"},
        "contexts": {"os": {"name": "Windows"}, "mail": {"subject": SECRET}},
    }
    clean = scrub_event(event, None)
    dumped = json.dumps(clean, ensure_ascii=False)
    assert MARKER not in dumped
    assert "user@example.com" not in dumped
    assert "DK-KIM-PC" not in dumped
    assert clean["tags"] == {"stage": "login"}


def test_cron_check_in_survives_scrub(sent):
    from sentry_sdk.crons import monitor

    config = {"schedule": {"type": "crontab", "value": "30 7 * * 1-6"}, "timezone": "UTC"}

    @monitor(monitor_slug="mail-briefing-user-1", monitor_config=config)
    def job():
        subject = SECRET  # noqa: F841

    job()
    sentry_sdk.flush()

    check_ins = [e for e in sent if e.get("type") == "check_in"]
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert all(c["monitor_slug"] == "mail-briefing-user-1" and c["check_in_id"] for c in check_ins)
    assert check_ins[0]["monitor_config"]["schedule"]["value"] == "30 7 * * 1-6"
    assert MARKER not in _dump(check_ins)


def test_groupware_relative_dates():
    from datetime import datetime, timedelta

    from mailbrief.groupware import _parse_dt

    today = datetime.now().date()
    assert _parse_dt("오늘 10:29") == datetime.combine(today, datetime.strptime("10:29", "%H:%M").time())
    assert _parse_dt("어제 09:05").date() == today - timedelta(days=1)
    assert _parse_dt("2026-11-12 10:29") == datetime(2026, 11, 12, 10, 29)
