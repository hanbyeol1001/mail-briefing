"""명령줄 진입점.

  mailbrief                        (인자 없이) 설정 마법사 창
  mailbrief setup                  그룹웨어 계정 입력 (Windows 자격 증명 관리자에 저장)
  mailbrief run                    브리핑 실행 (지금은 수집까지)
  mailbrief answers export|score   실제 메일 정답지 만들기 / 채점
  mailbrief sentry-test --dry-run  Sentry 로 보낼 내용을 화면에만 출력 (전송 안 함)
  mailbrief sentry-test            가짜 에러를 실제로 Sentry 에 보내 거르기 확인
"""

from __future__ import annotations

import argparse
import json
import sys

import sentry_sdk
from sentry_sdk.crons import monitor
from sentry_sdk.transport import Transport

from mailbrief import __version__, diagnostics
from mailbrief.config import load_config
from mailbrief.monitoring import SafeError, init_sentry, report_safe, stage

# 가짜 업무 데이터. sentry-test 에서 이 문자열이 Sentry 에 나타나면 거르기가 실패한 것.
FAKE_SUBJECT = "[회신 요청] 테스트용 비밀 제목 SECRET-SUBJECT"
FAKE_SENDER = "secret.sender@example.com"


def _monitor_config() -> dict:
    return {
        "schedule": {"type": "crontab", "value": "50 7 * * 1-6"},  # 월~토 07:50
        "timezone": "Asia/Ho_Chi_Minh",
        "checkin_margin": 10,  # 분. 08:00 까지 시작 안 하면 '누락'
        "max_runtime": 30,  # 분. 30분 넘게 걸리면 '시간 초과'
    }


def cmd_run(args: argparse.Namespace) -> int:
    from mailbrief.config import PROFILE

    if PROFILE:
        args.no_cron = True  # 시험 프로필은 Cron 모니터에 섞지 않는다
    config = load_config(scheduled=args.scheduled)
    enabled = init_sentry(config)
    print(f"mailbrief {__version__} / Sentry {'켜짐' if enabled else '꺼짐 (.env 에 SENTRY_DSN 없음)'}")

    def pipeline() -> None:
        from mailbrief.runner import run_briefing

        run_briefing(headless=not args.show_browser, use_summary=not args.no_summary, use_notify=not args.no_notify)

    try:
        # 출석부(Cron)에는 작업 스케줄러 실행만 적는다. 손으로 돌린 실행까지 적으면 dev 환경 모니터가 생겨 매일 '누락'이 뜬다.
        if enabled and args.scheduled and not args.no_cron:
            monitored = monitor(monitor_slug=f"mail-briefing-{config.user_alias}", monitor_config=_monitor_config())(pipeline)
            monitored()
        else:
            pipeline()
    except Exception:
        if not args.no_notify:
            from mailbrief import notify

            notify.failed("오늘 브리핑을 만들지 못했습니다. 관리자에게 알려 주세요.")
        raise
    print("완료")
    return 0


class _PrintTransport(Transport):
    """전송하지 않고, Sentry 로 나갈 이벤트를 화면에 출력한다."""

    def capture_envelope(self, envelope) -> None:  # type: ignore[override]
        for item in envelope.items:
            if item.type == "event":
                print(json.dumps(item.payload.json, ensure_ascii=False, indent=2))


def cmd_sentry_test(args: argparse.Namespace) -> int:
    config = load_config()
    transport = _PrintTransport() if args.dry_run else None
    if not init_sentry(config, transport=transport):
        print(".env 에 SENTRY_DSN 이 없습니다. --dry-run 으로 먼저 확인하세요.")
        return 1

    # 1) 메일 내용이 에러 메시지와 로컬 변수에 섞여 들어간 '나쁜' 경우
    try:
        with stage("read_body"):
            subject, sender = FAKE_SUBJECT, FAKE_SENDER  # noqa: F841 (로컬 변수 유출 확인용)
            raise ValueError(f"본문 해석 실패: {subject} / {sender}")
    except ValueError:
        pass

    # 2) 권장 방식: 메일 정보는 진단 파일에, Sentry 에는 고정 문장과 diag_id 만
    try:
        with stage("classify"):
            diag_id = diagnostics.save("classify", {"subject": FAKE_SUBJECT, "sender": FAKE_SENDER, "test": True})
            raise SafeError("기한 날짜 해석 실패 (테스트)", diag_id=diag_id)
    except SafeError:
        # 가짜 데이터라 남겨 둘 필요가 없다 (남기면 브리핑 '참고' 칸에 계속 뜬다).
        diagnostics.remove(diag_id)

    # 3) 개발자가 쓴 경고 문장
    report_safe("읽음 복구 확인 테스트 경고", stage_name="restore_unread")

    sentry_sdk.flush(timeout=10)
    if args.dry_run:
        print("\n위 JSON 에 SECRET-SUBJECT 나 secret.sender 가 없으면 거르기 성공입니다. (전송 안 함)")
    else:
        print("Sentry 로 테스트 이벤트 3건을 보냈습니다. Issues 화면에서 SECRET-SUBJECT 가 안 보이는지 확인하세요.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from mailbrief import credentials
    from mailbrief.config import PROFILE, save_frozen_config

    if PROFILE:
        save_frozen_config({"MAILBRIEF_USER_ALIAS": args.alias or "user-test", "SENTRY_ENVIRONMENT": "dev"})

    creds = credentials.ask_with_dialog()
    if creds is None:
        print("취소했습니다.")
        return 1
    credentials.save(creds)
    print("계정을 Windows 자격 증명 관리자에 저장했습니다.")
    return 0


def cmd_answers(args: argparse.Namespace) -> int:
    from mailbrief import answers

    config = load_config()
    init_sentry(config)
    if args.action == "export":
        total, answered = answers.export(config.user_alias, args.days, sample=args.sample)
        print(f"메일 {total}통으로 정답지를 만들었습니다 (기존 정답 {answered}건 보존).")
        print(f"파일: {answers.ANSWERS_DIR / f'정답지_{config.user_alias}.xlsx'}")
        print("노란 칸(정답_요청 Y/N, 정답_종류 회신/검토, 정답_기한 YYYY-MM-DD)을 채운 뒤 'mailbrief answers score' 를 실행하세요.")
    else:
        result = answers.score_answers(config.user_alias)
        print(result.summary())
        note = answers.sample_note(config.user_alias, result)
        if note:
            print(note)
        # 메일 번호만 출력한다. 내용은 엑셀에서 번호로 찾아보면 된다.
        print(
            "놓침:", result.missed_keys, "| 잘못 잡음:", result.false_alarm_keys,
            "| 기한 틀림:", result.deadline_wrong_keys, "| 종류 틀림:", result.kind_wrong_keys,
        )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from mailbrief import compare

    config = load_config()
    if args.action == "build":
        page, seconds = compare.build(config.user_alias, tuple(args.models), count=args.count)
        print(f"비교 화면: {page}")
        print("메일 1통 평균 요약 시간: " + ", ".join(f"{m} {s:.1f}초" for m, s in seconds.items()))
    else:
        print(compare.tally(args.code))
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    """시험 프로필의 데이터 폴더와 저장 계정을 지운다. 기본 프로필은 지우지 않는다."""
    import shutil

    from mailbrief import credentials
    from mailbrief.config import DATA_DIR, PROFILE

    if not PROFILE:
        print("cleanup 은 시험 프로필에만 쓸 수 있습니다 (--profile 이름).")
        return 1
    existed = DATA_DIR.exists()
    if existed:
        shutil.rmtree(DATA_DIR)
    had_account = credentials.delete()
    print(f"[{PROFILE}] 데이터 폴더 {'삭제' if existed else '없음'}, 저장 계정 {'삭제' if had_account else '없음'}")
    print(f"확인: 폴더 남음={DATA_DIR.exists()}, 계정 남음={credentials.load() is not None}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailbrief")
    parser.add_argument("--version", action="version", version=f"mailbrief {__version__}")
    parser.add_argument("--profile", default="", help="시험 프로필 이름 (다른 사람 계정 시험용, 계정/데이터 분리)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="브리핑 실행")
    p_run.add_argument("--no-cron", action="store_true", help="Sentry Cron 체크인 안 함 (개발용)")
    p_run.add_argument("--show-browser", action="store_true", help="브라우저 창을 띄워서 실행 (디버깅용)")
    p_run.add_argument("--no-summary", action="store_true", help="로컬 AI 요약 건너뛰기")
    p_run.add_argument("--scheduled", action="store_true", help="작업 스케줄러 실행 표시 (Sentry 환경 prod)")
    p_run.add_argument("--no-notify", action="store_true", help="Windows 알림 띄우지 않기")
    p_run.set_defaults(func=cmd_run)

    p_setup = sub.add_parser("setup", help="그룹웨어 계정 입력")
    p_setup.add_argument("--alias", default="", help="시험 프로필의 Sentry 별칭 (예: user-2)")
    p_clean = sub.add_parser("cleanup", help="시험 프로필의 데이터와 계정 지우기")
    p_clean.set_defaults(func=cmd_cleanup)
    p_setup.set_defaults(func=cmd_setup)

    p_ans = sub.add_parser("answers", help="실제 메일 정답지 만들기/채점")
    p_ans.add_argument("action", choices=["export", "score"])
    p_ans.add_argument("--days", type=int, default=365, help="export 할 기간 (일)")
    p_ans.add_argument("--sample", type=int, default=None, help="표본 정답지: '요청 아님' 중 무작위로 넣을 수")
    p_ans.set_defaults(func=cmd_answers)

    p_cmp = sub.add_parser("compare", help="요약 모델 블라인드 비교")
    p_cmp.add_argument("action", choices=["build", "tally"])
    p_cmp.add_argument("code", nargs="?", help="tally: 비교 화면의 선택 코드")
    p_cmp.add_argument("--models", nargs=2, default=["gemma3:4b", "qwen3:4b"])
    p_cmp.add_argument("--count", type=int, default=10)
    p_cmp.set_defaults(func=cmd_compare)

    p_test = sub.add_parser("sentry-test", help="Sentry 거르기 확인")
    p_test.add_argument("--dry-run", action="store_true", help="보내지 않고 화면에만 출력")
    p_test.set_defaults(func=cmd_sentry_test)

    p_wiz = sub.add_parser("wizard", help="설정 마법사 창 열기")
    p_wiz.set_defaults(func=lambda _args: (__import__("mailbrief.wizard").wizard.main(), 0)[1])

    if argv is None:
        argv = sys.argv[1:]
        rest = [x for i, x in enumerate(argv) if not (x.startswith("--profile") or (i and argv[i - 1] == "--profile"))]
        if not rest:
            argv = argv + ["wizard"]  # exe 를 그냥 더블클릭하면(또는 --profile 만 주면) 설정 마법사
    args = parser.parse_args(argv)
    if sys.stdout is None:
        # 작업 스케줄러가 pythonw(창 없음)로 실행하면 출력할 곳이 없으므로 로그 파일로 보낸다.
        # 로그에는 개수와 파일 경로만 남는다 (메일 내용 출력 금지 규칙 그대로).
        from mailbrief.config import DATA_DIR

        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA_DIR / "logs" / "run.log", "a", encoding="utf-8", buffering=1)
        print(f"\n=== {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(sys.argv[1:])}")
    try:
        return args.func(args)
    except Exception:
        sentry_sdk.flush(timeout=10)
        raise


if __name__ == "__main__":
    sys.exit(main())
