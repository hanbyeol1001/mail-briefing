from datetime import datetime

from mailbrief.briefing import is_excluded
from mailbrief.groupware import InboxMail


def _mail(sender: str, subject: str) -> InboxMail:
    return InboxMail("1", sender, "x@example.com", subject, datetime(2026, 11, 18, 9), "읽음", True, "")


EXCLUDE = {"senders": ["Wikibot", "시스템"], "subject_keywords": ["[광고]"]}


def test_sender_matches_part_of_name_ignoring_case():
    assert is_excluded(_mail("ERP시스템", "알림"), EXCLUDE)
    assert is_excluded(_mail("wikibot", "알림"), EXCLUDE)
    assert not is_excluded(_mail("김가상 과장", "알림"), EXCLUDE)


def test_subject_keyword():
    assert is_excluded(_mail("김가상 과장", "[광고] 할인 안내"), EXCLUDE)
    assert not is_excluded(_mail("김가상 과장", "광고 시안 검토 요청"), EXCLUDE)
