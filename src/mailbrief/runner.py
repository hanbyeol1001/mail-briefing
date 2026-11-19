"""브리핑 한 번 실행: 수집 → 판정 → 요약 → HTML → 알림. 명령줄(run)과 설정 마법사가 함께 쓴다."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from mailbrief import briefing, diagnostics, notify, summarize
from mailbrief.collect import collect
from mailbrief.monitoring import report_safe, stage

# 오래된 안 읽은 메일 요약에 쓸 최대 시간. 보통은 한 번에 다 끝난다 (RTX 3050 기준 한 통 약 2초).
# 느린 PC 에서 아침 브리핑이 너무 늦어지지 않게, 넘으면 남은 건 다음 실행 때 이어서 한다.
OLD_SUMMARY_BUDGET_SECONDS = 10 * 60


def _wake_ollama(log: Callable[[str], None]) -> None:
    """Ollama 가 설치돼 있는데 꺼져 있으면 켜고 최대 30초 기다린다 (트레이에서 꺼 둔 경우 대비)."""
    from mailbrief import system

    if system.ollama_running() or not system.ollama_exe():
        return
    log("로컬 AI(Ollama)를 켜는 중")
    system.start_ollama()
    for _ in range(30):
        if system.ollama_running():
            return
        time.sleep(1)


@dataclass
class Result:
    path: str
    reply: int
    review: int
    new: int


def run_briefing(
    *, headless: bool = True, use_summary: bool = True, use_notify: bool = True, log: Callable[[str], None] = print,
    progress: Callable[[str, float], None] = lambda _label, _done: None,
) -> Result:
    data = collect(headless=headless, progress=progress)
    # 화면/로그에는 개수만 남긴다 (메일 내용 출력 금지).
    log(
        f"받은 메일 {len(data.inbox)}통, 14일보다 오래된 안 읽은 메일 {len(data.old_unread)}통, 보낸 메일 {len(data.sent)}통 "
        f"(본문 {len(data.bodies)}통 읽음, 못 읽음 {len(data.failed_bodies)}통)"
    )
    if data.restore_failed:
        log(f"⚠ 안 읽은 메일 {len(data.restore_failed)}통을 '읽지 않음'으로 되돌리지 못했습니다. 그룹웨어에서 확인해 주세요.")
    elif data.restored:
        log(f"열어 본 안 읽은 메일 {data.restored}통은 모두 다시 '읽지 않음'으로 되돌렸습니다.")

    with stage("classify"):
        b = briefing.build(data)

    if use_summary:
        _wake_ollama(log)

    if not use_summary:
        pass
    elif not summarize.available():
        report_safe("로컬 AI(Ollama) 없음, 요약 없이 브리핑", stage_name="summarize")
        b.notes.append("로컬 AI(Ollama)가 꺼져 있어 요약 없이 만들었습니다.")
    else:
        from mailbrief.cache import SummaryCache

        cache = SummaryCache()
        model = summarize.DEFAULT_MODEL
        items = [i for i in b.all_items() if i.mail.mnum in data.bodies]
        old_items = [i for i in b.old_unread if i.mail.mnum in data.bodies]
        for item in items + old_items:
            item.summary = cache.get(item.mail.mnum, model)
        todo = [i for i in items if i.summary is None]
        old_todo = [i for i in old_items if i.summary is None]
        log(f"요약: 새로 {len(todo) + len(old_todo)}통 (저장된 요약 {len(items) + len(old_items) - len(todo) - len(old_todo)}통 재사용)")
        failed = 0
        old_started: float | None = None
        for n, item in enumerate(todo + old_todo, start=1):
            if n > len(todo):
                # 오래된 메일은 시간 제한 안에서만 (남은 건 다음 실행 때 이어서).
                old_started = old_started or time.monotonic()
                if time.monotonic() - old_started > OLD_SUMMARY_BUDGET_SECONDS:
                    later = len(todo) + len(old_todo) - n + 1
                    log(f"오래된 메일 {later}통은 시간이 오래 걸려 다음 실행 때 이어서 요약합니다.")
                    break
            log(f"요약 중 {n}/{len(todo) + len(old_todo)}")
            progress(f"요약 중 {n}/{len(todo) + len(old_todo)}", 0.55 + 0.4 * n / (len(todo) + len(old_todo)))
            try:
                with stage("summarize"):
                    item.summary = summarize.summarize(item.mail.subject, item.mail.sender_name, data.bodies[item.mail.mnum].text)
                cache.put(item.mail.mnum, item.mail.received_at, model, item.summary)
            except Exception:
                failed += 1
        cache.save(b.created_at, keep={i.mail.mnum for i in b.old_unread})
        if failed:
            b.notes.append(f"요약하지 못한 메일 {failed}통이 있습니다.")

    pending = diagnostics.pending()
    if pending:
        b.notes.append(
            f"진단 파일 {len(pending)}개가 있습니다 ({diagnostics.DIAG_DIR}). 내용을 직접 확인한 뒤 공유할지 정해 주세요."
        )

    progress("브리핑 만드는 중", 0.97)
    with stage("render"):
        path = briefing.save(b)
    result = Result(path, len(b.reply_needed), len(b.review), len(b.new_mails))
    log(f"회신 필요 {result.reply}건, 확인·검토 {result.review}건, 새 메일 {result.new}통 → {path}")
    if use_notify:
        with stage("notify"):
            notify.briefing_ready(path, result.reply, result.review, result.new)
    return result
