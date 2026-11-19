"""정답지 채점. 판정 결과와 정답을 비교해 개수만 센다 (메일 내용은 결과에 넣지 않는다)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Case:
    key: str  # 메일 번호나 가상 메일 id
    want_request: bool
    want_deadline: date | None
    got_request: bool
    got_deadline: date | None
    want_kind: str | None = None  # 회신 / 검토 (정답에 적힌 경우만 채점)
    got_kind: str | None = None


@dataclass
class Score:
    total: int = 0
    hit: int = 0  # 요청을 요청으로 맞힘
    missed: int = 0  # 요청인데 놓침
    false_alarm: int = 0  # 요청 아닌데 요청으로 잡음
    correct_reject: int = 0
    deadline_total: int = 0  # 정답에 기한이 있는 요청 중 요청으로 맞힌 수
    deadline_right: int = 0
    kind_total: int = 0
    kind_right: int = 0
    missed_keys: list[str] = field(default_factory=list)
    false_alarm_keys: list[str] = field(default_factory=list)
    deadline_wrong_keys: list[str] = field(default_factory=list)
    kind_wrong_keys: list[str] = field(default_factory=list)

    def summary(self) -> str:
        found = self.hit + self.false_alarm
        precision = self.hit / found if found else 0.0
        recall = self.hit / (self.hit + self.missed) if (self.hit + self.missed) else 0.0
        deadline = self.deadline_right / self.deadline_total if self.deadline_total else 0.0
        kind = self.kind_right / self.kind_total if self.kind_total else 0.0
        return (
            f"메일 {self.total}통 | 요청 맞힘 {self.hit}, 놓침 {self.missed}, 잘못 잡음 {self.false_alarm} "
            f"(정밀도 {precision:.0%}, 재현율 {recall:.0%}) | 기한 정확 {self.deadline_right}/{self.deadline_total} ({deadline:.0%})"
            f" | 종류 정확 {self.kind_right}/{self.kind_total} ({kind:.0%})"
        )


def score(cases: list[Case]) -> Score:
    s = Score()
    for c in cases:
        s.total += 1
        if c.want_request and c.got_request:
            s.hit += 1
            if c.want_deadline is not None or c.got_deadline is not None:
                s.deadline_total += 1
                if c.want_deadline == c.got_deadline:
                    s.deadline_right += 1
                else:
                    s.deadline_wrong_keys.append(c.key)
            if c.want_kind:
                s.kind_total += 1
                if c.want_kind == c.got_kind:
                    s.kind_right += 1
                else:
                    s.kind_wrong_keys.append(c.key)
        elif c.want_request:
            s.missed += 1
            s.missed_keys.append(c.key)
        elif c.got_request:
            s.false_alarm += 1
            s.false_alarm_keys.append(c.key)
        else:
            s.correct_reject += 1
    return s
