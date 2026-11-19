"""브리핑 화면 조립 확인 (가짜 메일만 씀)."""

from datetime import date, datetime, timedelta

from mailbrief import briefing
from mailbrief.briefing import Briefing, Item
from mailbrief.classify import Judgement
from mailbrief.groupware import InboxMail
from mailbrief.summarize import Summary

NOW = datetime.combine(date.today(), datetime.min.time()).replace(hour=7, minute=30)


def _item(mnum: str, days: int | None, kind: str = "회신") -> Item:
    mail = InboxMail(mnum, "가상 과장", "x@example.com", f"제목 {mnum}", NOW - timedelta(hours=3), "읽음", True, "")
    deadline = date.today() + timedelta(days=days) if days is not None else None
    return Item(mail, Judgement(True, deadline, "부탁드립니다.", None, 5, [], kind), None, Summary("요약 문장", "할 일"))


def test_card_stripe_follows_deadline():
    b = Briefing(created_at=NOW)
    b.reply_needed = [_item("1", 0), _item("2", 1), _item("3", 7), _item("4", None)]
    page = briefing.render(b)
    for level in ("due-today", "due-soon", "due-later", "due-none"):
        assert level in page


def test_counts_for_settings_window():
    b = Briefing(created_at=NOW)
    b.reply_needed = [_item("1", 0), _item("2", 3)]
    b.review = [_item("3", 0, "검토")]
    counts = briefing.counts(b)
    assert (counts["reply"], counts["review"], counts["new"], counts["due_today"]) == (2, 1, 0, 2)


def test_search_text_has_sender_subject_summary_only():
    page = briefing.render(Briefing(created_at=NOW, reply_needed=[_item("1", 0)]))
    assert 'data-q="가상 과장 제목 1 요약 문장"' in page
