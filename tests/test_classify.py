"""가상 메일 정답지로 판정기를 채점한다. 실제 메일은 쓰지 않는다."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from mailbrief.classify import judge, normalize_subject
from mailbrief.scoring import Case, score

FIXTURES = Path(__file__).parent / "fixtures"
# synthetic_mails: 패턴을 다듬을 때 본 메일 / synthetic_holdout: 처음 본 표현으로 쓴 확인용 메일
MAILS = [m for name in ("synthetic_mails", "synthetic_holdout") for m in json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))]


def _run(mail):
    received = datetime.strptime(mail["received"], "%Y-%m-%d %H:%M")
    return judge(mail["subject"], mail["body"], received)


def test_synthetic_score():
    cases = []
    for m in MAILS:
        j = _run(m)
        want = date.fromisoformat(m["deadline"]) if m["deadline"] else None
        cases.append(Case(str(m["id"]), m["is_request"], want, j.is_request, j.deadline, m["kind"], j.kind))
    s = score(cases)
    print("\n" + s.summary())
    print("놓침:", s.missed_keys, "잘못 잡음:", s.false_alarm_keys, "기한 틀림:", s.deadline_wrong_keys, "종류 틀림:", s.kind_wrong_keys)
    assert s.missed == 0 and s.false_alarm <= 1
    assert s.deadline_right == s.deadline_total
    assert s.kind_right == s.kind_total


@pytest.mark.parametrize("mail", MAILS, ids=lambda m: f"{m['id']}-{m['subject'][:12]}")
def test_each_mail(mail):
    j = _run(mail)
    assert j.is_request == mail["is_request"], j.reasons
    want = date.fromisoformat(mail["deadline"]) if mail["deadline"] else None
    if mail["is_request"]:
        assert j.deadline == want, (j.deadline_evidence, j.reasons)
        assert j.kind == mail["kind"]
        assert j.request_evidence is not None


def test_normalize_subject():
    assert normalize_subject("RE: RE:[회신 요청] 2027년 계획") == normalize_subject("[회신 요청] 2027년 계획")
    assert normalize_subject("FW: 자료  공유") == "자료 공유"


# 2026-11-18 동기 메일함 시험에서 '기한 없음'으로 나온 표현들 (문장은 가상)
DEADLINE_CASES = [
    ("자료 요청", "첨부 자료 확인 후 3일 내에 회신 바랍니다.", date(2026, 11, 17)),
    ("자료 요청", "3일 안에 회신 부탁드립니다.", date(2026, 11, 17)),
    ("자료 요청", "3영업일 이내 회신 부탁드립니다.", date(2026, 11, 18)),  # 일요일 11/15 제외
    ("회신 요청", "회신 기한: 11/17", date(2026, 11, 17)),
    ("[회신요청] 단가 확인 (~11/17)", "단가 확인 부탁드립니다.", date(2026, 11, 17)),
    ("단가 확인 요청 11/17까지 회신", "확인 부탁드립니다.", date(2026, 11, 17)),
    ("자료 요청", "17일까지 회신 부탁드립니다.", date(2026, 11, 17)),
    ("자료 요청", "3일 전에 말씀드린 자료 송부 부탁드립니다.", None),  # '3일 전' 은 날짜가 아님
    ("교육 수강 요청", "아래 교육을 수강해 주시기 바랍니다.\n마감일 : 2026-11-20 17:00", date(2026, 11, 20)),
]


@pytest.mark.parametrize("subject,body,want", DEADLINE_CASES)
def test_deadline_phrases(subject, body, want):
    j = judge(subject, body, datetime(2026, 11, 14, 10, 0))
    assert j.is_request
    assert j.deadline == want


@pytest.mark.parametrize("subject,body", [
    ("일정 안내", "11월 20일 오전 설비 점검이 있습니다. 참고 바랍니다."),
    ("3/4분기 실적 공유", "3/4분기 실적 공유드립니다."),
])
def test_dates_in_notices_are_not_requests(subject, body):
    assert not judge(subject, body, datetime(2026, 11, 14, 10, 0)).is_request
