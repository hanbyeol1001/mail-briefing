"""그룹웨어에서 브리핑 재료를 모은다: 받은 메일 + 본문, 보낸 메일.

단계마다 monitoring.stage() 로 감싸서, 실패하면 '어느 단계'인지 Sentry 에 남긴다.
메일 한 통의 본문 읽기가 실패해도 나머지는 계속 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from mailbrief import credentials, diagnostics, groupware
from mailbrief.cache import BodyCache
from mailbrief.config import SITE
from mailbrief.groupware import InboxMail, MailBody, SentMail
from mailbrief.monitoring import SafeError, report_safe, stage

LOOKBACK_DAYS = 14
# 다른 시스템에서 바로 열어 보는 알림 메일 (설정의 ALERT_WORDS: 보낸 사람·제목에 든 말). 그룹웨어에선 영원히 '안 읽음'으로 남는다.
# 오래된 목록에선 빼고, 브리핑에는 회신할 것과 새 메일일 때만 보여 준다.
ALERT_WORDS = SITE.alert_words


def is_alert_mail(mail: InboxMail) -> bool:
    text = f"{mail.sender_name} {mail.subject}".lower()
    return any(word in text for word in ALERT_WORDS)


@dataclass
class Collected:
    inbox: list[InboxMail]
    sent: list[SentMail]
    bodies: dict[str, MailBody]  # mnum -> 본문
    failed_bodies: list[str] = field(default_factory=list)  # 본문을 못 읽은 mnum
    restore_failed: list[str] = field(default_factory=list)  # 읽지않음 복구 실패 mnum
    restored: int = 0  # 열었다가 '읽지않음'으로 되돌린 메일 수
    old_unread: list[InboxMail] = field(default_factory=list)  # 조회 기간보다 오래된 안 읽은 메일
    old_unread_failed: bool = False  # 오래된 안 읽은 메일 목록을 못 가져옴 (브리핑 본편은 계속)


def collect(
    now: datetime | None = None, *, headless: bool = True, lookback_days: int = LOOKBACK_DAYS,
    progress: Callable[[str, float], None] = lambda _label, _done: None,
) -> Collected:
    """progress(단계 이름, 0~1) 로 설정 창의 진행 막대를 움직인다. 0.55 까지가 수집, 나머지는 요약 몫."""
    now = now or datetime.now()
    since = now - timedelta(days=lookback_days)

    with stage("setup"):
        creds = credentials.load()
        if creds is None:
            raise SafeError("저장된 그룹웨어 계정 없음 (mailbrief setup 필요)")

    with stage("login"):
        ip = groupware.groupware_ip()  # 사내망 연결 확인 (실패하면 login 단계로 Sentry 에 남는다)

    with groupware.open_browser(headless=headless, ip=ip) as browser:
        page = browser.new_context().new_page()
        progress("로그인 중", 0.02)
        with stage("login"):
            groupware.login(page, creds)
        progress("메일 목록 읽는 중", 0.08)
        with stage("list_inbox"):
            inbox = groupware.list_inbox(page, since)
        with stage("list_sent"):
            sent = groupware.list_sent(page, since)
        old_unread: list[InboxMail] = []
        old_unread_failed = False
        try:
            # 덤으로 보여 주는 칸이라, 실패해도 Sentry 에 남기고 브리핑 본편은 계속 만든다.
            with stage("list_unread"):
                old_unread = [m for m in groupware.list_unread(page) if m.received_at < since and not is_alert_mail(m)]
        except Exception:
            old_unread_failed = True

        unknown = [m for m in inbox if m.is_read is None]
        if unknown:
            # 처음 보는 상태 아이콘. 읽음 상태를 망가뜨리지 않도록 본문을 열지 않는다.
            diag_id = diagnostics.save("list_inbox", {"mnums": [m.mnum for m in unknown], "status": sorted({m.status for m in unknown})})
            report_safe(f"처음 보는 메일 상태 아이콘 {len(unknown)}건 (diag {diag_id})", stage_name="list_inbox")

        result = Collected(inbox=inbox, sent=sent, bodies={}, old_unread=old_unread, old_unread_failed=old_unread_failed)
        cache = BodyCache()
        opened_unread: list[str] = []  # 이번에 열어서 '읽음'이 돼 버린 메일
        try:
            targets = inbox + old_unread
            for n, mail in enumerate(targets, start=1):
                progress(f"본문 읽는 중 {n}/{len(targets)}", 0.15 + 0.4 * n / len(targets))
                cached = cache.get(mail.mnum)
                if cached is not None:  # 전에 읽은 메일은 다시 열지 않는다 (읽음 상태도 안 건드림)
                    result.bodies[mail.mnum] = cached
                    continue
                if mail.is_read is None:
                    continue
                if mail.is_read is False:
                    opened_unread.append(mail.mnum)
                try:
                    with stage("read_body"):
                        body = groupware.read_body(page, mail)
                    result.bodies[mail.mnum] = body
                    cache.put(mail.mnum, mail.received_at, body)
                except Exception:
                    result.failed_bodies.append(mail.mnum)
        finally:
            # 중간에 실패해도 열었던 안 읽은 메일은 반드시 한꺼번에 되돌린다.
            if opened_unread:
                try:
                    with stage("restore_unread"):
                        result.restore_failed = groupware.restore_unread_many(page, opened_unread)
                        if result.restore_failed:
                            raise SafeError(f"읽음 복구 실패 {len(result.restore_failed)}통")
                except Exception:
                    result.restore_failed = result.restore_failed or list(opened_unread)
                result.restored = len(opened_unread) - len(result.restore_failed)
            # 오래된 안 읽은 메일은 보관 기간이 지나도 남겨 둔다 (매일 다시 열지 않도록).
            cache.save(now, keep={m.mnum for m in old_unread})
    return result
