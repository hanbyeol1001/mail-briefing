"""회사 그룹웨어 자동화 (주소는 설정의 GROUPWARE_URL). 읽기만 한다. 메일을 보내거나 지우거나 옮기지 않는다."""

from __future__ import annotations

import ipaddress
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterator

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from mailbrief.config import SITE
from mailbrief.credentials import Credentials
from mailbrief.monitoring import SafeError

BASE_URL = SITE.groupware_url
MAIN_FRAME_URL = f"{BASE_URL}/main_frame.aspx"
INBOX_URL = f"{BASE_URL}/mail/inbox.aspx?mpath=all"
OUTBOX_URL = f"{BASE_URL}/mail/inbox.aspx?mpath=outbox"
INTERNAL_IP = SITE.groupware_ip  # 사내망에서 Windows DNS 가 주는 그룹웨어 주소 (설정의 GROUPWARE_IP, 없으면 DNS 만 씀)
MAX_PAGES = 20  # 안전장치. 14일치가 이보다 많으면 Sentry 경고.

# 받은편지함 상태 아이콘의 title. 이 밖의 값은 '모름'으로 두고 읽음 상태를 건드리지 않는다.
STATUS_READ = "읽음"
STATUS_REPLIED = "회신"
STATUS_UNREAD = "읽지않음"


@dataclass
class InboxMail:
    mnum: str
    sender_name: str
    sender_email: str
    subject: str
    received_at: datetime
    status: str  # 아이콘 title 원문 (읽음/회신/읽지않음)
    has_attachment: bool
    view_path: str  # 본문 주소 (/mail/read_mail.aspx?...)

    @property
    def is_replied(self) -> bool:
        return self.status == STATUS_REPLIED

    @property
    def is_read(self) -> bool | None:
        """읽음 여부. 처음 보는 아이콘이면 None."""
        if self.status in (STATUS_READ, STATUS_REPLIED):
            return True
        if self.status == STATUS_UNREAD:
            return False
        return None


@dataclass
class SentMail:
    mnum: str
    recipients: list[str]  # "이름 <email>" 원문
    subject: str
    sent_at: datetime
    read_confirmed_at: datetime | None
    recipient_emails: list[str] = field(default_factory=list)


@dataclass
class MailBody:
    to: list[str]  # 받는 사람 "이름 <email>" 원문
    cc: list[str]  # 참조
    text: str  # 본문 텍스트


# 목록 표의 각 줄을 dict 로 뽑는 스크립트. 칸 순서: 체크 | 중요 | 첨부 | 사람 | 제목 | ...
_ROWS_JS = r"""
() => [...document.querySelectorAll('tr.old')].filter(tr => tr.querySelector('input[name=sel_row]')).map(tr => {
  const td = [...tr.children];
  const mnum = tr.querySelector('input[name=sel_row]').value;
  const icon = tr.querySelector('i[onclick^="ChangeReadState"]');
  const who = td[3];
  const link = who.querySelector('a[title]');
  const view = (tr.querySelector('#viewRead_' + mnum) || {getAttribute: () => ''}).getAttribute('onclick') || '';
  const m = view.match(/goView\('([^']+)'/);
  return {
    viewPath: m ? m[1].replace(/\\u0026/g, '&').replace(/^\.\./, '') : '',
    mnum,
    status: icon ? icon.getAttribute('title') : '',
    attach: !!td[2].querySelector('img, i'),
    senderName: link ? link.textContent.trim() : '',
    senderEmail: link ? link.getAttribute('title') : '',
    recipients: [...who.querySelectorAll('select option')].map(o => o.textContent.trim()),
    subject: (tr.querySelector('#subject_' + mnum) || {}).textContent || '',
    cells: td.slice(5).map(c => c.textContent.trim()),
    dates: td.slice(5).map(c => c.textContent.trim()).filter(t => /^(\d{4}-\d{2}-\d{2}|오늘|어제|그제)\s*\d{1,2}:\d{2}/.test(t)),
  };
})
"""


_RELATIVE_DAYS = {"오늘": 0, "어제": 1, "그제": 2}


def _parse_dt(text: str) -> datetime:
    """'2026-11-12 10:29' 또는 당일/최근 메일의 '오늘 10:29', '어제 09:00' 형식."""
    text = text.strip()
    word, _, hm = text.partition(" ")
    if word in _RELATIVE_DAYS:
        t = datetime.strptime(hm.strip(), "%H:%M").time()
        return datetime.combine(datetime.now().date() - timedelta(days=_RELATIVE_DAYS[word]), t)
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


def _email_of(recipient: str) -> str:
    if "<" in recipient and recipient.endswith(">"):
        return recipient.rsplit("<", 1)[1][:-1].strip().lower()
    return recipient.strip().lower()


def groupware_ip() -> str:
    """그룹웨어에 실제로 연결되는 IP 를 고른다.

    브라우저의 '보안 DNS'(DoH)는 그룹웨어를 외부 IP 로 찾아가는데, 사내망에서는 그 주소로 연결이 안 된다.
    그래서 Windows DNS 가 준 주소를 먼저 쓰고, 그게 외부 주소이거나 연결이 안 되면 알려진 사내 주소를 시험한다.
    실패 문장은 원인 종류만 담는다 (IP 는 Sentry 로 보내지 않음).
    """
    if not BASE_URL:
        raise SafeError("그룹웨어 주소 설정 없음 (.env 의 GROUPWARE_URL)")
    host = BASE_URL.split("//", 1)[1]
    try:
        os_ip = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
    except OSError:
        os_ip = None
    candidates = [ip for ip in (os_ip, INTERNAL_IP) if ip and ipaddress.ip_address(ip).is_private]
    for ip in dict.fromkeys(candidates):
        try:
            socket.create_connection((ip, 443), timeout=5).close()
            return ip
        except OSError:
            continue
    if os_ip is None:
        raise SafeError("그룹웨어 주소를 찾지 못함 (Windows DNS 실패)")
    if not ipaddress.ip_address(os_ip).is_private:
        raise SafeError("Windows DNS 가 외부 주소를 주고 사내 주소에도 연결 안 됨 (사내망 연결 확인)")
    raise SafeError("그룹웨어 사내 주소에 연결 안 됨 (사내망/방화벽 확인)")


@contextmanager
def open_browser(*, headless: bool = True, ip: str | None = None) -> Iterator[Browser]:
    ip = ip or groupware_ip()
    host = BASE_URL.split("//", 1)[1]
    with sync_playwright() as p:
        # Windows 에 기본 설치된 Edge 를 쓴다 (평소에 안 써도 됨). 따로 Chromium 을 받을 필요가 없다.
        # 사용자의 Edge 프로필/기본 브라우저 설정과는 섞이지 않는 임시 프로필로 실행된다.
        # 보안 DNS 를 끄고, 그룹웨어 주소는 위에서 연결을 확인한 사내 IP 로 고정한다.
        args = ["--disable-features=DnsOverHttps", f"--host-resolver-rules=MAP {host} {ip}"]
        try:
            browser = p.chromium.launch(channel="msedge", headless=headless, args=args)
        except Exception as exc:
            raise SafeError("Edge 실행 실패 (Microsoft Edge 설치 확인)") from exc
        try:
            yield browser
        finally:
            browser.close()


def login(page: Page, creds: Credentials) -> None:
    """로그인한다. 실패하면 이유를 고정 문장으로 담은 SafeError 를 던진다."""
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout as exc:
        raise SafeError("그룹웨어 접속 시간 초과 (사내망 연결 확인)") from exc

    if not page.locator("#txtUserid").count():
        raise SafeError("로그인 화면 구조가 바뀜 (아이디 칸 없음)")
    page.fill("#txtUserid", creds.userid)
    page.fill("#txtPassword", creds.password)
    page.click("#btnLogin_web")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except PlaywrightTimeout as exc:
        raise SafeError("로그인 후 화면 응답 없음") from exc

    if page.locator("#txtOtp").count():
        raise SafeError("OTP 인증이 필요함 (자동 로그인 불가)")
    if page.locator("#txtPassword").count():
        # 로그인 화면에 그대로 있으면 실패. 알림창 문구는 내용 확인 없이 보내지 않는다.
        # 그룹웨어는 틀린 계정 안내를 팝업이 아니라 화면 안 #lblErr 글자로 보여 준다.
        err = page.locator("#lblErr").first.inner_text().strip() if page.locator("#lblErr").count() else ""
        wrong_account = dialogs or "비밀번호" in err
        raise SafeError("로그인 실패 (아이디/비밀번호 확인)" if wrong_account else "로그인 실패 (원인 불명)")


def _collect_rows(
    page: Page,
    url: str,
    since: datetime,
    *,
    box: str,
    prepare: Callable[[Page], None] | None = None,
    row_filter: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """since 이후 메일이 나올 때까지 페이지를 넘기며 목록 줄을 모은다 (최신순 정렬 가정).

    prepare: 목록을 연 뒤 필터 등을 거는 함수. row_filter: 남길 줄. 한 페이지에 남는 줄이 없으면 멈춘다.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if prepare:
            prepare(page)
    except PlaywrightTimeout as exc:
        raise SafeError(f"{box} 목록 열기 시간 초과") from exc

    rows: list[dict] = []
    for page_no in range(1, MAX_PAGES + 1):
        try:
            batch = page.evaluate(_ROWS_JS)
        except Exception as exc:
            raise SafeError(f"{box} 목록 구조가 바뀜") from exc
        if row_filter:
            batch = [r for r in batch if row_filter(r)]
        rows.extend(batch)

        oldest = min((_parse_dt(r["dates"][0]) for r in batch if r["dates"]), default=None)
        if not batch or (oldest and oldest < since):
            return rows

        next_link = page.locator(
            "a[href^=\"javascript:__doPostBack('dglist\"]", has_text=re_exact(str(page_no + 1))
        )
        if not next_link.count():
            return rows
        # 페이지 이동은 표만 바뀌는 부분 새로고침이라, 첫 줄 메일 번호가 바뀔 때까지 기다린다.
        first_mnum = batch[0]["mnum"]
        next_link.first.click()
        try:
            page.wait_for_function(
                "m => { const c = document.querySelector('tr.old input[name=sel_row]'); return c && c.value !== m; }",
                arg=first_mnum,
                timeout=30_000,
            )
        except PlaywrightTimeout as exc:
            raise SafeError(f"{box} {page_no + 1}페이지 넘기기 시간 초과") from exc

    raise SafeError(f"{box} 목록이 {MAX_PAGES}페이지를 넘음")


def re_exact(text: str):
    import re

    return re.compile(rf"^\s*{re.escape(text)}\s*$")


def _date_error(box: str, row: dict) -> SafeError:
    """처음 보는 날짜 형식. 칸의 '모양'만 진단 파일에 남긴다 (숫자→#, 한글→가, 영문→a)."""
    import re

    from mailbrief import diagnostics

    def shape(t: str) -> str:
        return re.sub(r"[A-Za-z]", "a", re.sub(r"[가-힣]", "가", re.sub(r"\d", "#", t)))

    diag_id = diagnostics.save("list", {"box": box, "cell_shapes": [shape(c) for c in row.get("cells", [])]})
    return SafeError(f"{box} 날짜 형식을 모름", diag_id=diag_id)


def _inbox_mails(rows: list[dict], since: datetime, box: str) -> list[InboxMail]:
    mails = []
    for r in rows:
        if not r["dates"]:
            raise _date_error(box, r)
        received = _parse_dt(r["dates"][0])
        if received < since:
            continue
        mails.append(
            InboxMail(
                mnum=r["mnum"],
                sender_name=r["senderName"],
                sender_email=r["senderEmail"].lower(),
                subject=r["subject"].strip(),
                received_at=received,
                status=r["status"],
                has_attachment=r["attach"],
                view_path=r["viewPath"],
            )
        )
    return mails


def list_inbox(page: Page, since: datetime) -> list[InboxMail]:
    return _inbox_mails(_collect_rows(page, INBOX_URL, since, box="받은편지함"), since, "받은편지함")


def _is_inbox_post(response) -> bool:
    return "inbox.aspx" in response.url and response.request.method == "POST"


def _show_unread_only(page: Page) -> None:
    """목록 위 '안 읽은 메일 수'를 누른 것과 같다. 화면을 다시 열면 풀린다 (2026-11-18 시험으로 확인)."""
    # 부분 새로고침이라 페이지 이동이 없다. 지금 줄에 표시를 해 두고, 표시된 줄이 사라지면(표가 새로 그려지면) 끝.
    page.evaluate("() => document.querySelectorAll('tr.old').forEach(tr => tr.dataset.mbOld = '1')")
    with page.expect_response(_is_inbox_post, timeout=30_000):
        page.add_script_tag(content="read_unread('unread');")
    page.wait_for_function(
        "() => !document.querySelector('tr.old[data-mb-old]')"
        " && !(window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack())",
        timeout=30_000,
    )


def list_unread(page: Page) -> list[InboxMail]:
    """기간과 상관없이 안 읽은 받은 메일 전체. 필터가 풀린 페이지가 섞여도 안 읽은 줄만 남긴다."""
    rows = _collect_rows(
        page,
        INBOX_URL,
        datetime.min,
        box="안 읽은 메일",
        prepare=_show_unread_only,
        row_filter=lambda r: r["status"] == STATUS_UNREAD,
    )
    return _inbox_mails(rows, datetime.min, "안 읽은 메일")


def list_sent(page: Page, since: datetime) -> list[SentMail]:
    mails = []
    for r in _collect_rows(page, OUTBOX_URL, since, box="송신함"):
        if not r["dates"]:
            raise _date_error("송신함", r)
        sent = _parse_dt(r["dates"][0])
        if sent < since:
            continue
        mails.append(
            SentMail(
                mnum=r["mnum"],
                recipients=r["recipients"],
                subject=r["subject"].strip(),
                sent_at=sent,
                read_confirmed_at=_parse_dt(r["dates"][1]) if len(r["dates"]) > 1 else None,
                recipient_emails=[_email_of(x) for x in r["recipients"]],
            )
        )
    return mails


# 본문 화면: 받는 사람/참조는 줄 제목(수신/참조) 옆 select 의 option 들.
_BODY_JS = """
() => {
  const people = label => [...document.querySelectorAll('#read_header tr')]
    .filter(tr => (tr.querySelector('td .font_9pt_bold') || {}).textContent?.trim() === label)
    .flatMap(tr => [...tr.querySelectorAll('select option')].map(o => o.textContent.trim()));
  const body = document.getElementById('lblContents');
  return {
    ok: !!document.getElementById('lblSubject'),
    to: people('수신'),
    cc: people('참조'),
    text: body ? body.innerText : null,
  };
}
"""


def read_body(page: Page, mail: InboxMail) -> MailBody:
    """본문을 읽는다. 본문을 열면 '읽음'이 되므로, 원래 안 읽은 메일은 restore_unread_many() 로 되돌려야 한다."""
    if not mail.view_path:
        raise SafeError("본문 주소를 못 찾음")
    try:
        page.goto(BASE_URL + mail.view_path, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout as exc:
        raise SafeError("본문 열기 시간 초과") from exc
    if page.locator("#txtUserid").count():
        raise SafeError("본문 열기 중 로그인이 풀림")
    data = page.evaluate(_BODY_JS)
    if not data["ok"] or data["text"] is None:
        raise SafeError("본문 화면 구조가 바뀜")
    return MailBody(to=data["to"], cc=data["cc"], text=data["text"])


def _status_of(page: Page, mnum: str) -> str | None:
    page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=30_000)
    return page.evaluate(
        "m => document.getElementById(m)?.closest('tr')?.querySelector('i[onclick^=\"ChangeReadState\"]')?.getAttribute('title') ?? null",
        mnum,
    )


def restore_unread_many(page: Page, mnums: list[str], chunk: int = 50) -> list[str]:
    """여러 메일을 한 번에 '읽지않음'으로 되돌리고, 안 읽은 메일 목록을 다시 읽어 확인한다. 되돌리지 못한 번호를 돌려준다.

    그룹웨어의 ChangeReadState 는 쉼표로 이은 번호를 한 번에 받는다 (2026-11-17 시험으로 확인).
    확인은 기간 상관없는 '안 읽은 메일' 목록으로 해서, 14일보다 오래된 메일도 확인할 수 있다.
    """
    for i in range(0, len(mnums), chunk):
        nums = ",".join(str(int(m)) for m in mnums[i : i + chunk])
        page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=30_000)
        try:
            with page.expect_response(_is_inbox_post, timeout=30_000):
                page.add_script_tag(content=f"ChangeReadState('{nums}', 'unread');")
        except PlaywrightTimeout as exc:
            raise SafeError("읽음 복구 요청 응답 없음") from exc
    unread = {m.mnum for m in list_unread(page)}
    return [m for m in mnums if m not in unread]
