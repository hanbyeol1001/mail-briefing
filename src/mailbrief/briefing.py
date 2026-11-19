"""브리핑 조립: 수집한 메일을 세 칸(회신 필요 / 확인·검토 / 새로 온 메일)으로 나누고 HTML 로 만든다.

  🚨 회신 필요  : 종류가 '회신'이고 아직 회신 안 한 요청. 기한이 지난 것은 뺀다.
  👀 확인·검토  : 종류가 '검토'(회신 불필요). 기한이 있으면 기한까지, 없으면 받은 날부터 3근무일까지.
  📬 새로 온 메일: 지난 브리핑 이후(24시간, 월요일은 토요일 07:30 이후) 받은 메일.

기한 없는 '회신' 요청도 확인·검토와 같은 기간(3근무일) 동안 회신 필요 칸에 보여 준다.
"""

from __future__ import annotations

import html
import json
from urllib.parse import parse_qs, urlencode, urlsplit
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from mailbrief import __version__, classify
from mailbrief.classify import KIND_REPLY, Judgement
from mailbrief.collect import Collected, is_alert_mail
from mailbrief.config import DATA_DIR, SETTINGS_JSON, SITE, theme_choice
from mailbrief.groupware import BASE_URL, InboxMail
from mailbrief.summarize import Summary

BRIEFING_DIR = DATA_DIR / "briefings"
LATEST_COUNTS = BRIEFING_DIR / "최신_개수.json"
SETTINGS_PATH = SETTINGS_JSON
WORKDAYS = {0, 1, 2, 3, 4, 5}  # 월~토
NO_DEADLINE_WORKDAYS = 3
RUN_TIME = time(7, 30)

# 회사에서 늘 빼는 메일 (설정의 EXCLUDE_SENDERS / EXCLUDE_SUBJECTS). 설정 창에서 더 넣을 수 있다.
DEFAULT_EXCLUDE = {"senders": list(SITE.exclude_senders), "subject_keywords": list(SITE.exclude_subjects)}


@dataclass
class Item:
    mail: InboxMail
    judgement: Judgement | None
    reply: str | None = None  # classify.REPLIED / MAYBE_REPLIED / NOT_REPLIED
    summary: Summary | None = None

    @property
    def d_day(self) -> str:
        if not self.judgement or not self.judgement.deadline:
            return ""
        days = (self.judgement.deadline - date.today()).days
        return "D-day" if days == 0 else f"D-{days}"


@dataclass
class Briefing:
    created_at: datetime
    new_since: datetime | None = None
    reply_needed: list[Item] = field(default_factory=list)
    review: list[Item] = field(default_factory=list)
    new_mails: list[Item] = field(default_factory=list)
    old_unread: list[Item] = field(default_factory=list)  # 조회 기간보다 오래된 안 읽은 메일 (맨 아래 접힌 목록)
    notes: list[str] = field(default_factory=list)  # 브리핑 아래에 보여 줄 안내 (진단 파일 등)

    def all_items(self) -> list[Item]:
        seen, out = set(), []
        for item in self.reply_needed + self.review + self.new_mails:
            if item.mail.mnum not in seen:
                seen.add(item.mail.mnum)
                out.append(item)
        return out


def _read_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return {}


def load_user_exclude() -> dict[str, list[str]]:
    """설정 창에서 사람마다 추가한 제외 목록 (data/settings.json)."""
    extra = _read_settings().get("exclude", {})
    return {key: list(extra.get(key, [])) for key in DEFAULT_EXCLUDE}


def save_user_exclude(extra: dict[str, list[str]]) -> None:
    settings = _read_settings()
    settings["exclude"] = {key: list(extra.get(key, [])) for key in DEFAULT_EXCLUDE}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_exclude() -> dict[str, list[str]]:
    """기본 제외 목록 + 사람마다 추가한 것."""
    merged = {k: list(v) for k, v in DEFAULT_EXCLUDE.items()}
    for key, values in load_user_exclude().items():
        merged[key] += [x for x in values if x not in merged[key]]
    return merged


def is_excluded(mail: InboxMail, exclude: dict[str, list[str]]) -> bool:
    # 보낸 사람은 이름 일부만 적어도 걸리게 한다 (예: '시스템' → 'ERP시스템'). 대소문자 무시.
    sender, subject = mail.sender_name.lower(), mail.subject.lower()
    return any(s.lower() in sender for s in exclude["senders"]) or any(k.lower() in subject for k in exclude["subject_keywords"])


def add_workdays(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() in WORKDAYS:
            n -= 1
    return d


def new_mail_since(now: datetime) -> datetime:
    if now.weekday() == 0:  # 월요일: 토요일 07:30 이후
        return datetime.combine(now.date() - timedelta(days=2), RUN_TIME)
    return now - timedelta(hours=24)


def build(data: Collected, now: datetime | None = None) -> Briefing:
    now = now or datetime.now()
    today = now.date()
    exclude = load_exclude()
    since = new_mail_since(now)
    result = Briefing(created_at=now, new_since=since)

    for mail in data.inbox:
        if is_excluded(mail, exclude):
            continue
        body = data.bodies.get(mail.mnum)
        j = classify.judge(mail.subject, body.text, mail.received_at) if body else None
        item = Item(mail, j)
        alert = is_alert_mail(mail)

        if j and j.is_request:
            if j.deadline:
                still_open = j.deadline >= today
            else:
                still_open = today <= add_workdays(mail.received_at.date(), NO_DEADLINE_WORKDAYS)
            if still_open and j.kind == KIND_REPLY:
                item.reply = classify.reply_state(mail, data.sent)
                if item.reply != classify.REPLIED:
                    result.reply_needed.append(item)
            elif still_open and not alert:
                result.review.append(item)
        if mail.received_at >= since:
            result.new_mails.append(item)

    far = date.max
    result.reply_needed.sort(key=lambda i: (i.judgement.deadline or far, i.mail.received_at))
    result.review.sort(key=lambda i: (i.judgement.deadline or far, i.mail.received_at))
    result.new_mails.sort(key=lambda i: i.mail.received_at, reverse=True)
    result.old_unread = sorted(
        (Item(m, None) for m in data.old_unread if not is_excluded(m, exclude)), key=lambda i: i.mail.received_at, reverse=True
    )
    if data.old_unread_failed:
        result.notes.append("14일보다 오래된 안 읽은 메일 목록을 가져오지 못했습니다.")
    if data.failed_bodies:
        result.notes.append(f"본문을 읽지 못한 메일 {len(data.failed_bodies)}통은 판정에서 빠졌습니다.")
    if data.restore_failed:
        result.notes.append(f"'읽지 않음' 표시를 되돌리지 못한 메일이 {len(data.restore_failed)}통 있습니다. 그룹웨어에서 확인해 주세요.")
    return result


# ── HTML ────────────────────────────────────────────────────────────────

_WEEKDAYS = "월화수목금토일"


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def _when(dt: datetime, today: date) -> str:
    """'오늘 09:30', '어제 18:02', '11/16(월) 05:30'."""
    days = (today - dt.date()).days
    if days == 0:
        return f"오늘 {dt:%H:%M}"
    if days == 1:
        return f"어제 {dt:%H:%M}"
    return f"{dt:%m/%d}({_WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"


def _avatar(name: str) -> str:
    # 이름마다 같은 색이 나오게 (hash() 는 실행마다 바뀌므로 글자 코드 합을 쓴다).
    initial = (name.strip() or "?")[0]
    hue = sum(map(ord, name)) % 6
    return f'<span class="avatar a{hue}">{_esc(initial)}</span>'


def _open_link(m: InboxMail, label: str = "메일 보기") -> str:
    # 평소 쓰는 브라우저로 그룹웨어 본문을 연다. 그 브라우저에 로그인돼 있어야 하고, 열면 '읽음'이 된다.
    if not m.view_path:
        return ""
    return f'<a class="open" href="{_esc(BASE_URL + m.view_path)}" target="_blank">{label} <span aria-hidden="true">→</span></a>'


def _reply_link(m: InboxMail) -> str:
    # 그룹웨어 읽기 화면을 따로 열면 답장 버튼이 안 보여서, 답장 쓰기 화면을 바로 연다 (받는 사람·인용문이 채워져 있음).
    q = parse_qs(urlsplit(m.view_path or "").query)
    if not q.get("mnum") or not q.get("filename"):
        return ""
    query = urlencode({"mode": "reply", "mnum": q["mnum"][0], "filename": q["filename"][0], "mpath": "all", "sFilterType": "all"})
    return f'<a class="open reply-link" href="{_esc(BASE_URL + "/mail/mail_form.aspx?" + query)}" target="_blank">답장</a>'


def _due_level(item: Item) -> str:
    """카드 왼쪽 띠 색: 오늘 마감 빨강, 2일 안 주황, 그 뒤 파랑, 기한 없음 회색."""
    j = item.judgement
    if not j or not j.deadline:
        return "none"
    days = (j.deadline - date.today()).days
    return "today" if days <= 0 else "soon" if days <= 2 else "later"


def _search_text(item: Item) -> str:
    # 검색칸이 찾는 글자: 보낸 사람, 제목, 요약 (근거 문장과 버튼 글자는 빼서 엉뚱하게 걸리지 않게)
    m = item.mail
    summary = item.summary.summary if item.summary and item.summary.summary else ""
    return _esc(f"{m.sender_name} {m.subject} {summary}".lower())


def _due(item: Item) -> str:
    j = item.judgement
    if not j or not j.deadline:
        # '기한 없음'은 표시하지 않는다. AI 요약에 '3일 내에' 같은 말이 있으면 옆에 붙은 '기한 없음'이 헷갈린다.
        return ""
    level = _due_level(item)
    return (
        f'<span class="due {level}"><b>{item.d_day}</b>'
        f'<small>{j.deadline:%m/%d}({_WEEKDAYS[j.deadline.weekday()]})까지</small></span>'
    )


def _card(item: Item, *, section: str, is_new: bool, today: date) -> str:
    """회신 필요 / 확인·검토 칸의 큰 카드."""
    m, j = item.mail, item.judgement
    tags = []
    if is_new:
        tags.append('<span class="tag new">NEW</span>')
    if section == "review":
        tags.append('<span class="tag">회신 불필요</span>')
    if item.reply == classify.MAYBE_REPLIED:
        tags.append('<span class="tag warn">회신했을 수도 있음</span>')

    # 평소엔 요약 두 줄과 할 일만 보이고, '자세히'를 누르면 요약 전체와 근거 문장이 펼쳐진다.
    body = []
    summary = item.summary.summary if item.summary and item.summary.summary else ""
    if summary:
        body.append(f'<p class="summary">{_esc(summary)}</p>')
    if item.summary and item.summary.todo:
        body.append(f'<div class="todo"><span class="todo-label">할 일</span>{_esc(item.summary.todo)}</div>')
    evidence = [e for e in dict.fromkeys([j.request_evidence, j.deadline_evidence]) if e] if j else []
    if evidence:
        body.append(
            '<div class="evidence"><span class="evidence-label">근거</span><blockquote>'
            + "<br>".join(_esc(e) for e in evidence)
            + "</blockquote></div>"
        )
    more = '<button class="more" type="button" aria-expanded="false">자세히</button>' if evidence or len(summary) > 70 else ""
    return (
        f'<article class="card {section} due-{_due_level(item)}" data-mnum="{_esc(m.mnum)}" data-q="{_search_text(item)}">'
        f'<div class="card-top">{_avatar(m.sender_name)}'
        f'<div class="who"><span class="sender">{_esc(m.sender_name)}</span><span class="time">{_when(m.received_at, today)}</span></div>'
        f"{_due(item)}</div>"
        f'<h3 class="subject">{_esc(m.subject)}</h3>'
        f'{"".join(body)}'
        f'<div class="card-foot"><div class="tags">{more}{"".join(tags)}</div>'
        f'<div class="actions"><label class="done-box"><input type="checkbox"> 완료</label>{_reply_link(m)}{_open_link(m)}</div></div>'
        "</article>"
    )


def _row(item: Item, *, today: date, pending_note: bool = False) -> str:
    """새로 온 메일 / 오래된 안 읽은 메일 칸의 한 줄."""
    m, j = item.mail, item.judgement
    tag = ""
    if j and j.is_request:
        tag = f'<span class="tag {"reply" if j.kind == KIND_REPLY else "review"}">{"회신 필요" if j.kind == KIND_REPLY else "확인·검토"}</span>'
    if item.summary and item.summary.summary:
        summary = f'<p class="row-summary">{_esc(item.summary.summary)}</p>'
    elif pending_note:
        summary = '<p class="row-summary muted">요약 준비 중 · 다음 브리핑 때 이어서 요약해요</p>'
    else:
        summary = ""
    return (
        f'<li class="row" data-q="{_search_text(item)}">{_avatar(m.sender_name)}<div class="row-main">'
        f'<div class="row-head"><span class="row-subject">{_esc(m.subject)}</span>{tag}</div>'
        f'<div class="row-meta">{_esc(m.sender_name)} · {_when(m.received_at, today)}</div>{summary}</div>'
        f"{_open_link(m, '보기')}</li>"
    )


def _section_head(anchor: str, title: str, count: str, hint: str) -> str:
    return (
        f'<div class="section-head" id="{anchor}"><span class="dot {anchor}"></span><h2>{title}</h2>'
        f'<span class="count">{count}</span><span class="hint">{hint}</span></div>'
    )


def _cards(cards) -> str:
    body = "".join(cards)
    return f'<div class="cards">{body}</div>' if body else ""


def _empty(text: str) -> str:
    return f'<div class="empty">{text}</div>'


def render(b: Briefing) -> str:
    today = b.created_at.date()
    new_ids = {i.mail.mnum for i in b.new_mails}
    shown_above = {i.mail.mnum for i in b.reply_needed + b.review}
    # 새로 온 메일 칸에는 위 칸에 없는 것만 보여 주고, 개수는 전체를 알려 준다.
    new_only = [i for i in b.new_mails if i.mail.mnum not in shown_above]
    overlap = len(b.new_mails) - len(new_only)

    reply_n, review_n, new_n, old_n = len(b.reply_needed), len(b.review), len(b.new_mails), len(b.old_unread)
    if reply_n:
        headline = f"회신할 메일이 <em>{reply_n}건</em> 있어요."
    elif review_n:
        headline = f"회신할 메일은 없고, 확인할 메일이 <em>{review_n}건</em> 있어요."
    else:
        headline = "오늘은 챙길 요청이 없어요. 🎉"

    def stat(anchor: str, label: str, n: int) -> str:
        return (
            f'<a class="stat {anchor}" href="#{anchor}"><span class="stat-icon">{_ICONS[anchor]}</span>'
            f'<span class="stat-text"><b data-count="{anchor}" data-total="{n}">{n}</b><span>{label}</span></span></a>'
        )

    def tab(anchor: str, label: str, n: int) -> str:
        return f'<a class="tab {anchor}" href="#{anchor}"><span class="dot {anchor}"></span>{label} <b data-count="{anchor}">{n}</b></a>'

    sections = [("reply", "회신 필요", "회신", reply_n), ("review", "확인·검토", "확인", review_n), ("new", "새 메일", "새 메일", new_n)]
    if old_n:
        sections.append(("old", "오래된 안 읽음", "오래된", old_n))
    stats = "".join(stat(a, label, n) for a, label, _, n in sections)
    tabs = "".join(tab(a, short, n) for a, _, short, n in sections)

    reply = _section_head("reply", "회신 필요", str(reply_n), "아직 답하지 않은 요청이에요") + (
        _cards(_card(i, section="reply", is_new=i.mail.mnum in new_ids, today=today) for i in b.reply_needed)
        or _empty("회신할 요청이 없어요.")
    )
    review = _section_head("review", "확인·검토", str(review_n), "읽고 챙기면 되는 요청이에요") + (
        _cards(_card(i, section="review", is_new=i.mail.mnum in new_ids, today=today) for i in b.review)
        or _empty("확인할 요청이 없어요.")
    )
    since = f"{_when(b.new_since, today)} 이후" if b.new_since else "지난 브리핑 이후"
    new_count = f"{new_n}통" + (f" · 위에 나온 {overlap}통 포함" if overlap else "")
    new = _section_head("new", "새로 온 메일", new_count, since) + (
        f'<ul class="rows">{"".join(_row(i, today=today) for i in new_only)}</ul>'
        if new_only
        else _empty("새로 온 메일이 없어요." if not b.new_mails else "새로 온 메일은 모두 위에 있어요.")
    )
    old = ""
    if b.old_unread:
        # 평소엔 접어 두고, 누르면 펼친다.
        old = (
            '<details class="old-box" id="old"><summary>'
            + _section_head("old", "14일보다 오래된 안 읽은 메일", f"{old_n}통", "눌러서 펼치기")
            + f'</summary><ul class="rows">{"".join(_row(i, today=today, pending_note=True) for i in b.old_unread)}</ul></details>'
        )
    notes = ""
    if b.notes:
        notes = '<aside class="notes"><b>참고</b><ul>' + "".join(f"<li>{_esc(n)}</li>" for n in b.notes) + "</ul></aside>"

    weekday = _WEEKDAYS[b.created_at.weekday()]
    return _PAGE.format(
        title=f"메일 브리핑 {b.created_at:%m/%d}",
        theme=theme_choice(),
        date=f"{b.created_at.month}월 {b.created_at.day}일 {weekday}요일",
        time=f"{b.created_at:%H:%M}",
        headline=headline,
        stats=stats,
        tabs=tabs,
        reply=reply,
        review=review,
        new=new,
        old=old,
        notes=notes,
        version=__version__,
        script=_DONE_SCRIPT,
    )


def save(b: Briefing) -> str:
    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    page = render(b)
    path = BRIEFING_DIR / f"브리핑_{b.created_at:%Y-%m-%d_%H%M}.html"
    path.write_text(page, encoding="utf-8")
    (BRIEFING_DIR / "최신_브리핑.html").write_text(page, encoding="utf-8")
    LATEST_COUNTS.write_text(json.dumps(counts(b), ensure_ascii=False), encoding="utf-8")
    return str(path)


def counts(b: Briefing) -> dict:
    """설정 창의 '오늘의 브리핑' 칸에 보여 줄 개수 (메일 내용 없음)."""
    today = b.created_at.date()
    due_today = sum(1 for i in b.reply_needed + b.review if i.judgement and i.judgement.deadline == today)
    return {
        "created_at": b.created_at.isoformat(timespec="minutes"),
        "reply": len(b.reply_needed), "review": len(b.review), "new": len(b.new_mails), "due_today": due_today,
    }


def load_counts() -> dict | None:
    try:
        return json.loads(LATEST_COUNTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# '완료' 체크는 이 PC 의 브라우저에 메일 번호로 기억한다. 다음 브리핑에서도 연하게 보이고, 다시 누르면 풀린다.
_DONE_SCRIPT = """<script>
(() => {
  const KEY = "mailbrief-done", KEEP_DAYS = 60;
  let done = {};
  try { done = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) {}
  const cutoff = Date.now() - KEEP_DAYS * 864e5;
  for (const k in done) if (done[k] < cutoff) delete done[k];
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(done)); } catch (e) {} };
  const left = sec => {
    const b = document.querySelector('[data-count="' + sec + '"][data-total]');
    return b ? b.dataset.total - document.querySelectorAll(".card." + sec + ".is-done").length : 0;
  };
  // 위 숫자 칸·메뉴 숫자와 첫 문장을 남은 개수로 맞춘다 (render() 의 headline 문구와 같은 규칙).
  const recount = () => {
    for (const sec of ["reply", "review"]) {
      const n = left(sec);
      document.querySelectorAll('[data-count="' + sec + '"]').forEach(b => (b.textContent = n));
    }
    const reply = left("reply"), review = left("review"), head = document.querySelector(".headline");
    if (!head) return;
    if (reply) head.innerHTML = "회신할 메일이 <em>" + reply + "건</em> 있어요.";
    else if (review) head.innerHTML = "회신할 메일은 없고, 확인할 메일이 <em>" + review + "건</em> 있어요.";
    else head.innerHTML = "오늘은 챙길 요청이 없어요. 🎉";
  };
  // 완료한 카드는 칸 맨 아래 '완료한 메일 N건' 묶음으로 옮기고, 풀면 원래 순서 자리로 돌려놓는다.
  const arrange = list => {
    let fold = list.querySelector(".done-fold");
    if (!fold) {
      fold = document.createElement("details");
      fold.className = "done-fold";
      fold.innerHTML = "<summary></summary>";
      list.appendChild(fold);
    }
    const cards = [...list.querySelectorAll(".card")].sort((a, b) => a.dataset.order - b.dataset.order);
    cards.forEach(c => (c.classList.contains("is-done") ? fold.appendChild(c) : list.insertBefore(c, fold)));
    const n = fold.querySelectorAll(".card").length;
    fold.hidden = n === 0;
    fold.querySelector("summary").textContent = "완료한 메일 " + n + "건";
    let empty = list.querySelector(".all-done");
    if (n && n === cards.length && !empty) list.insertAdjacentHTML("afterbegin", '<div class="all-done">남은 메일이 없어요. 모두 완료했어요 👍</div>');
    else if (n < cards.length && empty) empty.remove();
  };
  // 스르륵 움직임: 카드 높이를 줄였다 늘린다. 움직임 줄이기 설정이면 바로 옮긴다.
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MS = 320;
  const animate = async (el, frames, keep) => {
    if (calm) return null;
    const a = el.animate(frames, { duration: MS, easing: "cubic-bezier(.4,0,.2,1)", fill: keep ? "forwards" : "none" });
    await a.finished.catch(() => {});
    return a;
  };
  const shape = el => {
    const cs = getComputedStyle(el);
    return { height: el.offsetHeight + "px", paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, marginBottom: cs.marginBottom, opacity: cs.opacity };
  };
  const flat = { height: "0px", paddingTop: "0px", paddingBottom: "0px", marginBottom: "0px", opacity: 0 };
  const fold = async el => {
    const from = shape(el);
    el.style.overflow = "hidden";
    return animate(el, [from, flat], true);  // 접힌 채로 멈춰 두고, 옮긴 뒤에 풀어 준다
  };
  const unfold = async el => {
    const to = shape(el);
    el.style.overflow = "hidden";
    await animate(el, [flat, to]);
    el.style.overflow = "";
  };
  const pulse = el => { if (el && !calm) el.animate([{ color: "var(--accent)" }, { color: "" }], { duration: 900 }); };

  document.querySelectorAll(".cards").forEach(list => {
    list.querySelectorAll(".card[data-mnum]").forEach((card, i) => {
      const box = card.querySelector(".done-box input"), id = card.dataset.mnum;
      card.dataset.order = i;
      box.checked = id in done;
      card.classList.toggle("is-done", box.checked);
      box.addEventListener("change", async () => {
        if (box.checked) done[id] = Date.now(); else delete done[id];
        save();
        card.classList.toggle("is-done", box.checked);
        recount();
        box.disabled = true;
        const folded = await fold(card);  // 그 자리에서 스르륵 접히고
        arrange(list);
        if (folded) folded.cancel();
        card.style.overflow = "";
        if (card.offsetParent) await unfold(card);  // 옮긴 자리가 보이면 스르륵 펼친다 (닫힌 묶음 안이면 생략)
        box.disabled = false;
        pulse(list.querySelector(".done-fold summary"));
      });
    });
    arrange(list);
  });
  save(); recount();

  // 자세히: 요약 전체와 근거 문장을 펼친다.
  document.querySelectorAll(".card .more").forEach(btn => btn.addEventListener("click", () => {
    const open = btn.closest(".card").classList.toggle("expanded");
    btn.setAttribute("aria-expanded", open);
    btn.textContent = open ? "접기" : "자세히";
  }));

  // 검색: 보낸 사람·제목·요약에 그 글자가 있는 메일만 남긴다. 접힌 묶음 안에서 찾으면 펼친다.
  const input = document.querySelector(".search input"), nothing = document.querySelector(".search-empty");
  const filter = () => {
    const q = input.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll("[data-q]").forEach(el => {
      const hit = !q || el.dataset.q.includes(q);
      el.classList.toggle("no-match", !hit);
      if (hit) shown++;
    });
    document.body.classList.toggle("searching", !!q);
    if (q) document.querySelectorAll("details.old-box, details.done-fold").forEach(d => {
      if (d.querySelector("[data-q]:not(.no-match)")) d.open = true;
    });
    nothing.hidden = !q || shown > 0;
    nothing.textContent = "'" + input.value.trim() + "' 이(가) 들어간 메일이 없어요.";
  };
  input.addEventListener("input", filter);
  input.addEventListener("keydown", e => { if (e.key === "Escape") { input.value = ""; filter(); } });

  // 위에 붙는 메뉴: 지금 보고 있는 칸을 표시하고, 내려가면 아래에 선을 긋는다.
  const bar = document.querySelector(".bar"), heads = [...document.querySelectorAll(".section-head[id]")];
  const onScroll = () => {
    bar.classList.toggle("stuck", bar.getBoundingClientRect().top <= 0 && window.scrollY > 0);
    let current = heads[0] && heads[0].id;
    for (const h of heads) if (h.getBoundingClientRect().top < 120) current = h.id;
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.classList.contains(current)));
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
})();
</script>"""

# 위 숫자 칸 아이콘 (24x24, 글자색을 따라감)
_ICONS = {
    "reply": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/></svg>',
    "review": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5C7 4.5 2.7 7.6 1 12c1.7 4.4 6 7.5 11 7.5s9.3-3.1 11-7.5c-1.7-4.4-6-7.5-11-7.5zM12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>',
    "new": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 4-8 5-8-5V6l8 5 8-5v2z"/></svg>',
    "old": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3a9 9 0 0 0-9 9H1l3.9 3.9L9 12H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.25 2.52.77-1.28-3.52-2.09V8H12z"/></svg>',
}
_SEARCH_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
    '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 20.5 20.5"/></svg>'
)


_LOGO = (
    '<svg viewBox="0 0 64 64" width="40" height="40" aria-hidden="true">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff9254"/>'
    '<stop offset="1" stop-color="#d2400a"/></linearGradient></defs>'
    '<rect x="3" y="3" width="58" height="58" rx="14" fill="url(#g)"/>'
    '<rect x="14" y="20" width="36" height="25" rx="4" fill="#fff"/>'
    '<path d="M17 23 L32 34 L47 23" fill="none" stroke="#d9480f" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="49" cy="18" r="9" fill="#fff"/><circle cx="49" cy="18" r="7.4" fill="#22a05a"/>'
    '<path d="M45.6 18.2 L48 20.6 L52.4 15.9" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_DARK_VARS = (
    "--bg:#111317; --card:#1b1e24; --ink:#eceef1; --sub:#b4bcc6; --muted:#7d8792; --line:#2a2f37; --soft:#232730;"
    " --accent:#ff8a4c; --reply:#ff6b6b; --reply-soft:#3a1d1f; --review:#ffb547; --review-soft:#3a2d16;"
    " --new:#5aa9ff; --new-soft:#162a40; --old:#a3abb4; --old-soft:#262a31; --shadow:none;"
    " color-scheme:dark;"  # 검색칸·체크박스도 어둡게
)

_PAGE = (
    """<!doctype html>
<html lang="ko" data-theme="{theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg:#f4f5f7; --card:#fff; --ink:#191f28; --sub:#4e5968; --muted:#8b95a1; --line:#e5e8eb; --soft:#f2f4f6;
  --accent:#d9480f; --reply:#e03131; --reply-soft:#fff0f0; --review:#e8890c; --review-soft:#fff6e5;
  --new:#1c7ed6; --new-soft:#e7f3ff; --old:#868e96; --old-soft:#f1f3f5; --ok:#22a05a;
  --shadow:0 1px 2px rgba(0,0,0,.04), 0 4px 14px rgba(0,0,0,.04);
}}
/* 테마: 설정 창에서 고른 것 (data-theme). system 이면 Windows/브라우저 설정을 따른다. */
:root[data-theme="dark"] {{ """
    + _DARK_VARS
    + """ }}
@media (prefers-color-scheme: dark) {{ :root[data-theme="system"] {{ """
    + _DARK_VARS
    + """ }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 'Malgun Gothic','Segoe UI','Apple SD Gothic Neo',sans-serif; font-variant-numeric:tabular-nums; -webkit-font-smoothing:antialiased; }}
main {{ max-width:820px; margin:0 auto; padding:32px 16px 56px; }}
a {{ color:inherit; }}

.hero {{ display:flex; align-items:center; gap:14px; }}
.hero .brand {{ font-size:13px; font-weight:600; color:var(--muted); letter-spacing:.02em; }}
.hero h1 {{ font-size:26px; line-height:1.25; margin:2px 0 0; letter-spacing:-.02em; }}
.hero h1 small {{ font-size:15px; font-weight:500; color:var(--muted); margin-left:6px; }}
.headline {{ font-size:18px; margin:18px 0 0; color:var(--sub); }}
.headline em {{ font-style:normal; color:var(--reply); font-weight:700; }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin:20px 0 8px; }}
.stat {{ display:flex; align-items:center; gap:12px; padding:14px 16px; border-radius:16px; background:var(--card); text-decoration:none; box-shadow:var(--shadow); border:1px solid var(--line); transition:transform .15s, border-color .15s; }}
.stat:hover {{ border-color:var(--muted); transform:translateY(-1px); }}
.stat-icon {{ flex:none; width:40px; height:40px; border-radius:12px; display:grid; place-items:center; }}
.stat-icon svg {{ width:22px; height:22px; fill:currentColor; }}
.stat-text {{ display:flex; flex-direction:column; line-height:1.2; }}
.stat b {{ font-size:26px; }}
.stat-text span {{ font-size:13px; color:var(--sub); }}
.stat.reply b, .stat.reply .stat-icon {{ color:var(--reply); }} .stat.reply .stat-icon {{ background:var(--reply-soft); }}
.stat.review b, .stat.review .stat-icon {{ color:var(--review); }} .stat.review .stat-icon {{ background:var(--review-soft); }}
.stat.new b, .stat.new .stat-icon {{ color:var(--new); }} .stat.new .stat-icon {{ background:var(--new-soft); }}
.stat.old b, .stat.old .stat-icon {{ color:var(--old); }} .stat.old .stat-icon {{ background:var(--old-soft); }}

.bar {{ position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:10px; margin:14px -16px 0; padding:10px 16px; background:color-mix(in srgb, var(--bg) 86%, transparent); backdrop-filter:blur(12px); border-bottom:1px solid transparent; transition:border-color .2s; }}
.bar.stuck {{ border-bottom-color:var(--line); }}
.tabs {{ display:flex; gap:4px; overflow-x:auto; scrollbar-width:none; }}
.tab {{ display:flex; align-items:center; gap:6px; white-space:nowrap; padding:6px 12px; border-radius:999px; font-size:13.5px; font-weight:600; color:var(--sub); text-decoration:none; }}
.tab b {{ font-weight:700; }}
.tab:hover {{ background:var(--soft); }}
.tab.active {{ background:var(--card); color:var(--ink); box-shadow:var(--shadow); outline:1px solid var(--line); }}
.tab .dot {{ width:8px; height:8px; }}
.search {{ position:relative; flex:1; min-width:150px; margin-left:auto; max-width:260px; }}
.search svg {{ position:absolute; left:11px; top:50%; width:17px; height:17px; transform:translateY(-50%); color:var(--muted); pointer-events:none; }}
.search input {{ width:100%; font:inherit; font-size:14px; color:var(--ink); padding:7px 12px 7px 34px; border-radius:999px; border:1px solid var(--line); background:var(--card); outline:none; }}
.search input:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }}
.search-empty {{ margin:18px 0 0; padding:18px; border:1px dashed var(--line); border-radius:14px; color:var(--muted); text-align:center; font-size:14px; }}
.no-match {{ display:none !important; }}
body.searching .empty, body.searching .all-done {{ display:none; }}

.section-head {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin:32px 0 12px; scroll-margin-top:72px; }}
.section-head h2 {{ font-size:18px; margin:0; letter-spacing:-.01em; }}
.section-head .count {{ font-size:13px; font-weight:700; padding:1px 9px; border-radius:999px; background:var(--line); color:var(--sub); }}
.section-head .hint {{ font-size:13px; color:var(--muted); }}
.dot {{ width:10px; height:10px; border-radius:50%; }}
.dot.reply {{ background:var(--reply); }} .dot.review {{ background:var(--review); }} .dot.new {{ background:var(--new); }} .dot.old {{ background:var(--old); }}

.card {{ position:relative; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px 18px 14px 22px; margin-bottom:12px; box-shadow:var(--shadow); overflow:hidden; }}
.card::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:5px; }}
.card.due-today::before {{ background:var(--reply); }} .card.due-soon::before {{ background:var(--review); }}
.card.due-later::before {{ background:var(--new); }} .card.due-none::before {{ background:var(--line); }}
.card.due-today {{ border-color:color-mix(in srgb, var(--reply) 40%, var(--line)); background:linear-gradient(100deg, var(--reply-soft), var(--card) 45%); }}
.card-top {{ display:flex; align-items:center; gap:10px; }}
.who {{ display:flex; flex-direction:column; line-height:1.3; min-width:0; flex:1; }}
.sender {{ font-weight:600; font-size:14px; }}
.time {{ font-size:12px; color:var(--muted); }}
.subject {{ font-size:16.5px; line-height:1.45; margin:12px 0 4px; letter-spacing:-.01em; word-break:keep-all; overflow-wrap:anywhere; }}
.summary {{ margin:4px 0 0; color:var(--sub); }}
.todo {{ display:flex; gap:10px; align-items:baseline; margin-top:10px; padding:10px 12px; border-radius:10px; background:var(--soft); font-weight:600; }}
.card.reply .todo {{ background:var(--reply-soft); }} .card.review .todo {{ background:var(--review-soft); }}
.todo-label {{ flex:none; font-size:12px; font-weight:700; color:var(--accent); }}
.card:not(.expanded) .summary {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.card:not(.expanded) .evidence {{ display:none; }}
.evidence {{ margin-top:10px; }}
.evidence-label {{ font-size:12px; font-weight:700; color:var(--muted); }}
blockquote {{ margin:4px 0 0; padding:8px 12px; border-left:3px solid var(--line); color:var(--sub); font-size:13px; }}
.more {{ font:inherit; font-size:12.5px; font-weight:600; color:var(--muted); background:none; border:0; padding:3px 6px 3px 2px; cursor:pointer; border-radius:6px; }}
.more::after {{ content:' ▾'; }}
.more[aria-expanded="true"]::after {{ content:' ▴'; }}
.more:hover {{ color:var(--ink); background:var(--soft); }}
.card-foot {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:12px; }}
.tags {{ display:flex; flex-wrap:wrap; gap:6px; }}
.actions {{ flex:none; display:flex; align-items:center; gap:4px; }}
.done-box {{ display:flex; align-items:center; gap:5px; font-size:13px; color:var(--sub); cursor:pointer; padding:5px 8px; border-radius:8px; user-select:none; }}
.done-box:hover {{ background:var(--soft); }}
.done-box input {{ width:16px; height:16px; margin:0; accent-color:var(--accent); cursor:pointer; }}
.card {{ transition:opacity .2s; }}
.card.is-done {{ opacity:.45; box-shadow:none; }}
.card.is-done .subject, .card.is-done .summary, .card.is-done .todo {{ text-decoration:line-through; }}
.card.is-done .done-box {{ color:var(--accent); font-weight:600; }}
.done-fold {{ margin-top:4px; }}
.done-fold > summary {{ list-style:none; cursor:pointer; padding:8px 4px; font-size:13.5px; font-weight:600; color:var(--muted); }}
.done-fold > summary::-webkit-details-marker {{ display:none; }}
.done-fold > summary::before {{ content:'▸ '; }}
.done-fold[open] > summary::before {{ content:'▾ '; }}
.all-done {{ padding:14px 18px; border:1px dashed var(--line); border-radius:14px; color:var(--muted); font-size:14px; text-align:center; margin-bottom:6px; }}

.due {{ flex:none; display:flex; flex-direction:column; align-items:flex-end; gap:3px; line-height:1.2; }}
.due b {{ font-size:15px; padding:3px 11px; border-radius:999px; background:var(--soft); color:var(--sub); }}
.due small {{ font-size:11.5px; color:var(--muted); }}
.due.today b {{ background:var(--reply); color:#fff; }}
.due.soon b {{ background:var(--review-soft); color:var(--review); }}
.due.later b {{ background:var(--new-soft); color:var(--new); }}
.due.none {{ font-size:12px; color:var(--muted); }}

.tag {{ font-size:11.5px; font-weight:600; padding:2px 8px; border-radius:6px; background:var(--soft); color:var(--sub); white-space:nowrap; }}
.tag.new {{ background:var(--accent); color:var(--card); }}
.tag.warn {{ background:var(--review-soft); color:var(--review); }}
.tag.reply {{ background:var(--reply-soft); color:var(--reply); }}
.tag.review {{ background:var(--review-soft); color:var(--review); }}

.open {{ flex:none; font-size:13px; font-weight:600; color:var(--accent); text-decoration:none; padding:5px 10px; border-radius:8px; }}
.open:hover {{ background:var(--soft); }}

.avatar {{ flex:none; width:34px; height:34px; border-radius:50%; display:grid; place-items:center; font-size:14px; font-weight:700; color:#fff; }}
.a0 {{ background:#4c6ef5; }} .a1 {{ background:#12b886; }} .a2 {{ background:#f76707; }}
.a3 {{ background:#ae3ec9; }} .a4 {{ background:#1098ad; }} .a5 {{ background:#e64980; }}

.rows {{ list-style:none; margin:0; padding:0; background:var(--card); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); overflow:hidden; }}
.row {{ display:flex; align-items:flex-start; gap:12px; padding:12px 14px 12px 16px; border-top:1px solid var(--line); }}
.row:first-child {{ border-top:0; }}
.row-main {{ flex:1; min-width:0; }}
.row-head {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; }}
.row-subject {{ font-weight:600; word-break:keep-all; overflow-wrap:anywhere; }}
.row-meta {{ font-size:12px; color:var(--muted); }}
.row-summary {{ margin:3px 0 0; font-size:14px; color:var(--sub); }}
.row .open {{ margin-top:2px; }}
.muted {{ color:var(--muted) !important; }}

.old-box > summary {{ list-style:none; cursor:pointer; }}
.old-box > summary::-webkit-details-marker {{ display:none; }}
.old-box > summary .section-head h2::before {{ content:'▸ '; color:var(--muted); }}
.old-box[open] > summary .section-head h2::before {{ content:'▾ '; }}
.old-box[open] .hint {{ display:none; }}

.empty {{ padding:18px; border:1px dashed var(--line); border-radius:14px; color:var(--muted); text-align:center; font-size:14px; }}
.notes {{ margin-top:32px; padding:14px 16px; border-radius:12px; background:var(--soft); color:var(--sub); font-size:13px; }}
.notes ul {{ margin:6px 0 0; padding-left:18px; }}
footer {{ margin-top:40px; text-align:center; font-size:12px; color:var(--muted); }}

@media (max-width:520px) {{
  main {{ padding-top:22px; }}
  .hero h1 {{ font-size:22px; }}
  .stats {{ grid-template-columns:repeat(2, 1fr); }}
  .bar {{ flex-wrap:wrap; }}
  .search {{ max-width:none; flex-basis:100%; }}
  .card {{ padding:14px 14px 12px 18px; }}
  .row .open {{ display:none; }}
}}
</style></head>
<body><main>
<header class="hero">"""
    + _LOGO
    + """<div><div class="brand">메일 브리핑</div><h1>{date}<small>{time} 기준</small></h1></div></header>
<p class="headline">{headline}</p>
<nav class="stats">{stats}</nav>
<div class="bar"><nav class="tabs">{tabs}</nav><label class="search">"""
    + _SEARCH_ICON.replace("{", "{{").replace("}", "}}")
    + """<input type="search" placeholder="보낸 사람·제목 찾기" aria-label="메일 찾기"></label></div>
<p class="search-empty" hidden></p>
<section>{reply}</section>
<section>{review}</section>
<section>{new}</section>
{old}
{notes}
<footer>메일 내용은 이 PC 안에서만 처리했어요 · mailbrief v{version}</footer>
</main>{script}</body></html>
"""
)
