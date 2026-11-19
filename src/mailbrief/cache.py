"""본문/요약 캐시. 한 번 읽은 메일은 다시 열지 않고, 한 번 만든 요약은 다시 만들지 않는다.

이 PC 의 data(또는 %LOCALAPPDATA%/mailbrief)/cache 에만 저장한다. 보관 기간이 지난 메일은 지운다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from mailbrief.config import DATA_DIR
from mailbrief.groupware import MailBody
from mailbrief.summarize import Summary

CACHE_DIR = DATA_DIR / "cache"
KEEP_DAYS = 16  # 조회 기간(14일)보다 조금 길게


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}  # 깨진 캐시는 버리고 새로 만든다


def _save(path: Path, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _prune(data: dict, now: datetime, keep: set[str] = frozenset()) -> dict:
    """보관 기간이 지난 메일을 지운다. keep 에 있는 메일(오래된 안 읽은 메일)은 남긴다."""
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in data.items() if v.get("received_at", "") >= cutoff or k in keep}


class BodyCache:
    path = CACHE_DIR / "bodies.json"

    def __init__(self) -> None:
        self.data = _load(self.path)

    def get(self, mnum: str) -> MailBody | None:
        v = self.data.get(mnum)
        return MailBody(v["to"], v["cc"], v["text"]) if v else None

    def put(self, mnum: str, received_at: datetime, body: MailBody) -> None:
        self.data[mnum] = {"received_at": received_at.isoformat(), "to": body.to, "cc": body.cc, "text": body.text}

    def save(self, now: datetime, keep: set[str] = frozenset()) -> None:
        _save(self.path, _prune(self.data, now, keep))


class SummaryCache:
    path = CACHE_DIR / "summaries.json"

    def __init__(self) -> None:
        self.data = _load(self.path)

    def get(self, mnum: str, model: str) -> Summary | None:
        v = self.data.get(mnum)
        return Summary(v["summary"], v["todo"]) if v and v.get("model") == model else None

    def put(self, mnum: str, received_at: datetime, model: str, s: Summary) -> None:
        self.data[mnum] = {"received_at": received_at.isoformat(), "model": model, "summary": s.summary, "todo": s.todo}

    def save(self, now: datetime, keep: set[str] = frozenset()) -> None:
        _save(self.path, _prune(self.data, now, keep))
