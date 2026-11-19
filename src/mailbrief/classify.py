"""패턴 기반 판정: 이 메일이 나에게 하는 요청인지, 기한은 언제인지, 이미 회신했는지.

AI 없이 한국어 표현 패턴만 쓴다. 판정 근거가 된 본문 문장을 원문 그대로 함께 돌려준다.
정확도는 정답지 채점(answers.py)으로 확인하며 다듬는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# ── 요청 판정 ────────────────────────────────────────────────────────────

# 제목 표식. 있으면 요청일 가능성이 높다.
SUBJECT_REQUEST = re.compile(r"\[(회신\s*요청|회신요망|요청|협조\s*요청|협조|제출\s*요청|긴급|필독|확인\s*요청|의견\s*요청|리마인드|재요청)\]")
SUBJECT_INFO = re.compile(r"\[(공지|안내|공유|참고|알림|보고|FYI)\]", re.I)

# 본문 요청 표현. (동사/명사) + (부탁/요청/바랍니다 류)
_ACTION = r"(회신|답변|답신|제출|송부|보내|작성|기재|검토|확인|공유|전달|보고|입력|등록|업데이트|승인|결재|피드백|[Ff]eedback|의견|참석|참여|취합|준비|수정|서명|컨펌|변경|이수|수강|수령|협조|응답|수리|처리|정리|말씀|알려|진행|갱신|반납|올려|만들어)"
_ASK = r"(부탁\s*드립니다|부탁드려요|부탁합니다|부탁해요|부탁\s*드리겠습니다|바랍니다|바라겠습니다|주시기\s*바랍니다|주십시오|주세요|해\s*주시면|해주시면|주시면\s*감사|주시면\s*됩니다|요청\s*드립니다|요청드려요|요청합니다|필요합니다|해\s*주실\s*수|가능하실까요|가능할까요|줄\s*수\s*있(어요|나요|을까요)|줄래요|주실래요)"
BODY_REQUEST = re.compile(_ACTION + r"[^.\n]{0,25}?" + _ASK)
BODY_REQUEST_LOOSE = re.compile(r"(회신|제출|송부|작성|기재)\s*(요망|필수|바람|要)")
# 행동 단어 없이도 요청인 공손한 질문형: '봐주실 수 있을까요', '미팅 가능하실까요'
BODY_REQUEST_POLITE = re.compile(r"(주실\s*수\s*(있을까요|있으신가요|있나요)|가능하실까요|가능하신지요|가능할까요)")
# 요청 종류. 이 표현이 하나라도 있으면 '회신'(상대에게 뭔가 보내야 끝남), 없으면 '검토'(읽고 챙기면 됨, 회신 불필요).
KIND_REPLY = "회신"
KIND_REVIEW = "검토"
_REPLY_WORDS = re.compile(
    r"(회신|답변|답신|제출|송부|보내|응답|알려|말씀|의견|피드백|feedback|승인|결재|컨펌|여부|작성|보고해|올려)", re.I
)
SUBJECT_REPLY = re.compile(r"\[(회신\s*요청|회신요망|제출\s*요청|의견\s*요청)\]")
# 공지성 '확인 바랍니다'. 기한도 제목 표식도 없으면 요청으로 보지 않는다.
BODY_WEAK_NOTICE = re.compile(r"확인\s*(해\s*)?(주시기\s*)?바랍니다")

# 요청이 아님을 뜻하는 표현.
BODY_NEGATIVE = re.compile(
    r"(회신\s*(은|하실\s*필요\s*)?(불필요|필요\s*없|않으셔도|안\s*하셔도)|참고\s*(만\s*)?(부탁|바랍니다|하시기|해\s*주시기|하세요)"
    r"|공유\s*드립니다|공유드려요|안내\s*드립니다|알려\s*드립니다|보고\s*드립니다|FYI|별도\s*조치\s*(는\s*)?(불필요|필요\s*없)"
    r"|희망(자|하시는\s*분)|궁금한\s*(점|사항)|문의\s*사항이?\s*있으(면|시면))",
    re.I,
)

# ── 기한 판정 ────────────────────────────────────────────────────────────

_DEADLINE_HINT = re.compile(
    r"(까지|기한|마감|내로|이내|중으로|중에|전까지|전에|due|deadline|즉시|(금일|오늘|당일|이번\s*주|금주|이번\s*달|금월)\s*중"
    r"|\d\s*(영업)?일\s*(내|안)|~\s*\d)",  # '3일 내에', '3영업일 안에', '(~11/17)'
    re.I,
)
_WEEKDAYS = "월화수목금토일"

_P_YMD = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?")
_P_MD_KOR = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_P_MD_NUM = re.compile(r"(?<![\d.\-/])(\d{1,2})\s*[/.]\s*(\d{1,2})(?![\d/.]\d)")
_P_REL = re.compile(r"(오늘|금일|당일|내일|명일|모레|익일|즉시)")
_P_WEEKDAY = re.compile(r"(이번\s*주|금주|다음\s*주|차주|돌아오는)\s*([월화수목금토일])요일")
# '수요일까지' 처럼 주 표시 없이 요일만 쓴 경우: 받은 날 이후 가장 가까운 그 요일
_P_BARE_WEEKDAY = re.compile(r"(?<![가-힣])([월화수목금토일])요일\s*(까지|중|오전|오후|전)")
_P_THIS_MONTH_DAY = re.compile(r"(이번\s*달|금월|이달)\s*(\d{1,2})\s*일")
_P_WEEK_END = re.compile(r"(이번\s*주|금주|다음\s*주|차주)\s*(중|내|안|까지)")
_P_MONTH_END = re.compile(r"((이번\s*달|금월|이달)\s*(말|중|내|까지)|월말)")
_P_DAYS_WITHIN = re.compile(r"(\d{1,2})\s*(영업)?일\s*(이내|내로|내에|내|안에|안으로)")
# 월 없이 날짜만: '17일까지', '20일(금) 오전' → 받은 날 이후 가장 가까운 그 날
_P_DAY_ONLY = re.compile(r"(?<![\d월/.\-])(\d{1,2})\s*일\s*(까지|오전|오후|\(|자로)")  # '3일 전' 은 '사흘 전'일 수 있어 뺀다
# 이름표 형식 기한: '회신 기한: 11/17', '제출 마감 : 11월 20일'
BODY_REQUEST_LABEL = re.compile(r"(회신|제출|답변|송부|의견|검토)\s*(기한|기일|마감|마감일|요청일|일정)\s*[:：]")
# 행동 단어 없이 이름표만 있는 기한 줄: '마감일 : 2026-11-20 17:00', '기한: 11/20'
DEADLINE_LABEL = re.compile(r"(마감|기한|납기|due\s*date)\s*(일|일자|일시|날짜|시간)?\s*[:：]", re.I)


@dataclass
class Judgement:
    is_request: bool
    deadline: date | None
    request_evidence: str | None  # 요청으로 본 근거 문장 (원문)
    deadline_evidence: str | None  # 기한으로 본 근거 문장 (원문)
    score: int  # 요청 점수 (디버깅용)
    reasons: list[str] = field(default_factory=list)
    kind: str | None = None  # KIND_REPLY / KIND_REVIEW (요청일 때만)


def split_sentences(text: str) -> list[str]:
    """본문을 문장 단위로 자른다. 메일 서명/인용 아래는 버린다."""
    text = re.split(r"\n\s*(-{2,}\s*Original Message|-----원본 메시지|From:\s|보낸 사람:)", text)[0]
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip(" \t -•*·▶■□○●>")
        if not line:
            continue
        # '다.' '요.' 뒤에서 한 번 더 자른다.
        parts.extend(s.strip() for s in re.split(r"(?<=[다요][.!])\s+", line) if s.strip())
    return parts


def _infer_year(month: int, day: int, base: date) -> date | None:
    """연도가 없는 월/일 → 받은 날짜 기준으로 가장 그럴듯한 날짜 (과거 30일 ~ 미래 11개월)."""
    for year in (base.year, base.year + 1, base.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if base - timedelta(days=30) <= d <= base + timedelta(days=335):
            return d
    return None


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def find_dates(sentence: str, received: date) -> list[date]:
    """문장 속 날짜 표현을 모두 날짜로 바꾼다."""
    found: list[date] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    for m in _P_YMD.finditer(sentence):
        try:
            found.append(date(int(m[1]), int(m[2]), int(m[3])))
            taken.append(m.span())
        except ValueError:
            pass
    for pat in (_P_MD_KOR, _P_MD_NUM):
        for m in pat.finditer(sentence):
            if not free(m):
                continue
            month, day = int(m[1]), int(m[2])
            if 1 <= month <= 12 and 1 <= day <= 31:
                # '3/4분기', '1.5배' 같은 오탐을 줄이려고 숫자형은 뒤에 날짜다운 말이 올 때만 쓴다.
                after = sentence[m.end():]
                tilde = re.search(r"~\s*$", sentence[: m.start()])  # '(~11/17)' 의 ~ 는 '까지'
                if pat is _P_MD_NUM and not (
                    re.match(r"\s*(\(|일|까지|마감|중|내|오전|오후|에\s|에$|$|\s*[가-힣]{0,3}요일)", after)
                    or (tilde and re.match(r"\s*[)\]\s,]", after))
                ):
                    continue
                d = _infer_year(month, day, received)
                if d:
                    found.append(d)
                    taken.append(m.span())
    for m in _P_REL.finditer(sentence):
        offset = {"오늘": 0, "금일": 0, "당일": 0, "즉시": 0, "내일": 1, "명일": 1, "익일": 1, "모레": 2}[m[1]]
        found.append(received + timedelta(days=offset))
    for m in _P_WEEKDAY.finditer(sentence):
        monday = _week_monday(received)
        if m[1].replace(" ", "") in ("다음주", "차주") or (m[1] == "돌아오는" and _WEEKDAYS.index(m[2]) <= received.weekday()):
            monday += timedelta(days=7)
        found.append(monday + timedelta(days=_WEEKDAYS.index(m[2])))
        taken.append(m.span())
    for m in _P_BARE_WEEKDAY.finditer(sentence):
        if free(m):
            found.append(received + timedelta(days=(_WEEKDAYS.index(m[1]) - received.weekday()) % 7))
    for m in _P_THIS_MONTH_DAY.finditer(sentence):
        try:
            found.append(date(received.year, received.month, int(m[2])))
            taken.append(m.span())
        except ValueError:
            pass
    for m in _P_WEEK_END.finditer(sentence):
        monday = _week_monday(received) + (timedelta(days=7) if m[1].replace(" ", "") in ("다음주", "차주") else timedelta())
        found.append(monday + timedelta(days=4))  # 주 단위 기한은 그 주 금요일로 본다
    for m in _P_MONTH_END.finditer(sentence):
        nxt = date(received.year + (received.month == 12), received.month % 12 + 1, 1)
        found.append(nxt - timedelta(days=1))
    for m in _P_DAYS_WITHIN.finditer(sentence):
        if free(m):  # '11월 30일 이내' 의 '30일 이내' 는 이미 날짜로 읽었다
            taken.append(m.span())
            if m[2]:  # 영업일: 일요일을 뺀다 (월~토 근무)
                d, n = received, int(m[1])
                while n:
                    d += timedelta(days=1)
                    n -= d.weekday() != 6
                found.append(d)
            else:
                found.append(received + timedelta(days=int(m[1])))
    for m in _P_DAY_ONLY.finditer(sentence):
        if free(m):
            d = _next_day_of_month(int(m[1]), received)
            if d:
                found.append(d)
    return found


def _next_day_of_month(day: int, base: date) -> date | None:
    """'17일' → base 이후(당일 포함, 1주 전까지 허용) 가장 가까운 17일."""
    for months_ahead in (0, 1):
        y, mo = base.year + (base.month + months_ahead - 1) // 12, (base.month + months_ahead - 1) % 12 + 1
        try:
            d = date(y, mo, day)
        except ValueError:
            continue
        if d >= base - timedelta(days=7):
            return d
    return None


def judge(subject: str, body: str, received: datetime) -> Judgement:
    sentences = split_sentences(body)
    base = received.date()
    score = 0
    reasons: list[str] = []
    request_evidence = None

    is_reply = bool(re.match(r"\s*(RE|FW|Fwd|회신|전달)\s*:", subject, re.I))
    if SUBJECT_REQUEST.search(subject):
        # 남이 쓴 RE: [회신 요청] 은 원래 요청에 대한 답장일 수 있어 가볍게 본다.
        score += 1 if is_reply else 3
        reasons.append("제목 요청 표식")
    if SUBJECT_INFO.search(subject):
        score -= 1
        reasons.append("제목 안내 표식")
    if is_reply:
        reasons.append("RE/FW 제목")

    request_sentences = [
        s for s in sentences if BODY_REQUEST.search(s) or BODY_REQUEST_LOOSE.search(s) or BODY_REQUEST_POLITE.search(s)
        # '이번 주 중으로 부탁드립니다' 처럼 기한 + 부탁만 있는 문장
        or (_DEADLINE_HINT.search(s) and re.search(_ASK, s) and find_dates(s, base))
        # '회신 기한: 11/17' 같은 이름표
        or (BODY_REQUEST_LABEL.search(s) and find_dates(s, base))
    ]
    request_sentences = [s for s in request_sentences if not BODY_NEGATIVE.search(s)]
    weak_only = request_sentences and all(BODY_WEAK_NOTICE.search(s) for s in request_sentences)
    if request_sentences and not weak_only:
        score += 2
        reasons.append("본문 요청 표현")
        request_evidence = request_sentences[0]
    elif weak_only:
        score += 1
        reasons.append("공지성 확인 요청")
        request_evidence = request_sentences[0]
    negatives = [s for s in sentences if BODY_NEGATIVE.search(s)]
    if negatives and not request_sentences:
        score -= 2
        reasons.append("본문 안내/불필요 표현")

    # 기한: '까지/마감/기한' 류 단서가 있는 문장의 날짜. 요청 문장 안의 기한을 우선한다.
    deadline = deadline_evidence = None
    candidates: list[tuple[int, date, str]] = []
    request_idx = {i for i, s in enumerate(sentences) if s in request_sentences}
    for i, s in enumerate(sentences):
        if not _DEADLINE_HINT.search(s):
            continue
        # 요청 문장이 아닌 곳의 날짜는 일정 안내일 수 있다('11/18 전까지 점검 진행').
        # 행동/요청 표현이 있거나 요청 문장 바로 앞뒤 줄일 때만 기한으로 본다.
        if i not in request_idx and not (
            re.search(_ACTION, s) or re.search(_ASK, s) or {i - 1, i + 1} & request_idx or DEADLINE_LABEL.search(s)
        ):
            continue
        dates = find_dates(s, base)
        if not dates and s in request_sentences and i > 0:
            # '12/5에 만료됩니다. 만료 전에 갱신 부탁드립니다.' 처럼 날짜가 바로 앞 문장에 있는 경우
            dates = find_dates(sentences[i - 1], base)
            s = f"{sentences[i - 1]} {s}"
        for d in dates:
            priority = 0 if s in request_sentences or any(r in s for r in request_sentences) else 1
            candidates.append((priority, d, s))
    if not candidates and _DEADLINE_HINT.search(subject):
        # 본문에 기한이 없으면 제목의 기한을 쓴다: '단가 확인 (~11/17)', '자료 요청 11/17까지 회신'
        for d in find_dates(subject, base):
            candidates.append((1, d, subject.strip()))
    if candidates:
        candidates.sort(key=lambda c: (c[0], c[1]))
        _, deadline, deadline_evidence = candidates[0]
        if any(c[2] in request_sentences for c in candidates) or BODY_REQUEST.search(deadline_evidence) or re.search(_ACTION, deadline_evidence):
            score += 1
            reasons.append("기한 + 행동 표현")

    is_request = score >= 2
    kind = None
    if is_request:
        asked = request_sentences + ([deadline_evidence] if deadline_evidence else [])
        replyish = (
            (SUBJECT_REPLY.search(subject) and not is_reply)
            or any(_REPLY_WORDS.search(s) or BODY_REQUEST_POLITE.search(s) for s in asked)
            # '지난주 요청드린 자료 ... 다시 요청드립니다' 같은 재요청은 받을 것이 있다는 뜻
            or any(re.search(r"(다시|재)\s*요청|요청\s*드린", s) for s in sentences)
        )
        kind = KIND_REPLY if replyish else KIND_REVIEW
    else:
        deadline = deadline_evidence = None
    return Judgement(
        is_request, deadline, request_evidence or (deadline_evidence if is_request else None), deadline_evidence, score, reasons, kind
    )


# ── 회신 여부 ────────────────────────────────────────────────────────────

REPLIED = "회신 완료"
MAYBE_REPLIED = "회신했을 수도 있음"
NOT_REPLIED = "미회신"

_SUBJECT_PREFIX = re.compile(r"^\s*((RE|FW|FWD|회신|전달)\s*:\s*|\[[^\]]*\]\s*)+", re.I)


def normalize_subject(subject: str) -> str:
    return re.sub(r"\s+", " ", _SUBJECT_PREFIX.sub("", subject)).strip().lower()


def reply_state(mail, sent_mails) -> str:
    """mail: InboxMail, sent_mails: list[SentMail]."""
    if mail.is_replied:
        return REPLIED
    after = [s for s in sent_mails if s.sent_at >= mail.received_at and mail.sender_email in s.recipient_emails]
    key = normalize_subject(mail.subject)
    if any(normalize_subject(s.subject) == key for s in after):
        return REPLIED
    if after:
        return MAYBE_REPLIED
    return NOT_REPLIED
