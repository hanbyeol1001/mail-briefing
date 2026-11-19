"""실제 메일 정답지 (엑셀). 이 PC 의 data/answers/ 에만 저장한다 (git 제외).

  export: 메일함을 읽어 본문 사본(캐시)과 정답지 엑셀을 만든다. 이미 적은 정답은 보존한다.
          sample 을 주면 표본 정답지: 판정기가 '요청'이라 본 메일 전부 + '요청 아님' 중 무작위 sample 통.
  score : 캐시로 다시 판정해서 정답지와 비교한다. 결과는 개수와 메일 번호만 출력한다.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from mailbrief.classify import judge
from mailbrief.config import DATA_DIR
from mailbrief.scoring import Case, Score, score

ANSWERS_DIR = DATA_DIR / "answers"
HEADERS = ["메일번호", "받은날짜", "보낸사람", "제목", "판정_요청", "판정_종류", "판정_기한", "판정_근거", "정답_요청", "정답_종류", "정답_기한", "메모"]
WIDTHS = [11, 17, 16, 48, 10, 10, 12, 60, 10, 10, 12, 30]
ANSWER_COLS = slice(8, 11)  # 정답_요청, 정답_종류, 정답_기한


def _paths(alias: str):
    return ANSWERS_DIR / f"cache_{alias}.json", ANSWERS_DIR / f"정답지_{alias}.xlsx"


def _read_existing(xlsx) -> dict[str, dict]:
    if not xlsx.exists():
        return {}
    ws = load_workbook(xlsx).active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    return {str(r[0]): dict(zip(header, r)) for r in rows[1:] if r and r[0]}


def export(alias: str, lookback_days: int, sample: int | None = None) -> tuple[int, int]:
    """(정답지에 넣은 메일 수, 이미 정답이 적힌 수)."""
    from mailbrief.collect import collect

    data = collect(lookback_days=lookback_days)
    ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path, xlsx_path = _paths(alias)

    cache = []
    for m in data.inbox:
        body = data.bodies.get(m.mnum)
        cache.append(
            {
                "mnum": m.mnum,
                "received_at": m.received_at.isoformat(),
                "sender_name": m.sender_name,
                "sender_email": m.sender_email,
                "subject": m.subject,
                "body": body.text if body else None,
                "to": body.to if body else [],
                "cc": body.cc if body else [],
            }
        )
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    existing = _read_existing(xlsx_path)
    judged = []
    for item in cache:
        received = datetime.fromisoformat(item["received_at"])
        j = judge(item["subject"], item["body"] or "", received) if item["body"] is not None else None
        judged.append((item, received, j))

    population_no = [x for x in judged if x[2] and not x[2].is_request]
    if sample is not None:
        # 이미 정답지에 있던 메일은 그대로 두고(다시 만들어도 표본이 바뀌지 않게), 모자란 만큼만 무작위로 채운다.
        kept_no = [x for x in population_no if x[0]["mnum"] in existing]
        rest = [x for x in population_no if x[0]["mnum"] not in existing]
        random.Random(alias).shuffle(rest)
        chosen_no = {x[0]["mnum"] for x in kept_no + rest[: max(0, sample - len(kept_no))]}
        judged = [x for x in judged if (x[2] and x[2].is_request) or x[0]["mnum"] in chosen_no]

    wb = Workbook()
    ws = wb.active
    ws.title = "정답지"
    ws.append(HEADERS)
    for item, received, j in judged:
        old = existing.get(item["mnum"], {})
        ws.append(
            [
                item["mnum"],
                received.strftime("%Y-%m-%d %H:%M"),
                item["sender_name"],
                item["subject"],
                ("Y" if j.is_request else "N") if j else "본문없음",
                (j.kind or "") if j else "",
                j.deadline.isoformat() if j and j.deadline else "",
                (j.request_evidence or "") if j else "",
                old.get("정답_요청") or "",
                old.get("정답_종류") or "",
                _as_text(old.get("정답_기한")),
                old.get("메모") or "",
            ]
        )

    # 보기 좋게: 머리줄 고정, 정답 칸 색칠, Y/N 드롭다운
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True)
    answer_fill = PatternFill("solid", fgColor="FFF4CC")
    for row in ws.iter_rows(min_row=2):
        for cell in row[ANSWER_COLS]:
            cell.fill = answer_fill
        row[7].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"I2:I{ws.max_row}")
    dv_kind = DataValidation(type="list", formula1='"회신,검토"', allow_blank=True)
    ws.add_data_validation(dv_kind)
    dv_kind.add(f"J2:J{ws.max_row}")

    # 채점 때 '요청 아님' 표본으로 전체 놓침을 어림하려고 모집단 크기를 남긴다.
    info = wb.create_sheet("정보")
    info.append(["판정_요청아님_전체", len(population_no)])
    info.append(["표본", "예" if sample is not None else "아니오 (전체)"])
    wb.active = 0
    wb.save(xlsx_path)

    answered = sum(1 for r in existing.values() if r.get("정답_요청") in ("Y", "N"))
    return len(judged), answered


def _as_text(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value or "")


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    return date.fromisoformat(text) if text else None


def _population_no(xlsx) -> int | None:
    wb = load_workbook(xlsx)
    if "정보" not in wb.sheetnames:
        return None
    values = {r[0]: r[1] for r in wb["정보"].iter_rows(values_only=True)}
    return int(values["판정_요청아님_전체"]) if values.get("표본") == "예" else None


def sample_note(alias: str, s: Score) -> str:
    """표본 정답지면 '요청 아님 표본 n통 중 놓침 k통 → 전체 추정' 한 줄. 전체 정답지면 빈 문자열."""
    _, xlsx_path = _paths(alias)
    population = _population_no(xlsx_path)
    sampled = s.missed + s.correct_reject  # 판정기가 '요청 아님'이라 했고 정답이 적힌 메일
    if not population or not sampled:
        return ""
    estimate = s.missed * population / sampled
    return f"'요청 아님' 표본 {sampled}통 중 놓침 {s.missed}통 → 전체 {population}통 기준 놓침 약 {estimate:.0f}통 추정"


def score_answers(alias: str) -> Score:
    cache_path, xlsx_path = _paths(alias)
    if not xlsx_path.exists() or not cache_path.exists():
        raise FileNotFoundError("정답지가 없습니다. 먼저 'mailbrief answers export' 를 실행하세요.")
    cache = {c["mnum"]: c for c in json.loads(cache_path.read_text(encoding="utf-8"))}
    cases = []
    for mnum, row in _read_existing(xlsx_path).items():
        want = row.get("정답_요청")
        item = cache.get(mnum)
        if want not in ("Y", "N") or not item or item["body"] is None:
            continue
        j = judge(item["subject"], item["body"], datetime.fromisoformat(item["received_at"]))
        cases.append(
            Case(mnum, want == "Y", _as_date(row.get("정답_기한")), j.is_request, j.deadline, row.get("정답_종류") or None, j.kind)
        )
    return score(cases)


