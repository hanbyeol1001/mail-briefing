"""설정 읽기.

개발(소스) 실행: 프로젝트 폴더의 .env 와 data/ 를 쓴다.
배포(exe) 실행: %LOCALAPPDATA%/mailbrief 의 config.json 과 그 폴더를 쓴다.
환경변수가 있으면 언제나 우선한다.

프로필: 한 PC 에서 다른 사람 계정을 시험할 때 쓴다 (예: --profile 동기시험).
기본 프로필과 계정·데이터 폴더가 완전히 나뉘고, 자동 실행은 등록할 수 없다.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


def _profile_from_argv(argv: list[str]) -> str:
    # 다른 모듈들이 import 될 때 DATA_DIR 을 읽기 때문에, argparse 보다 먼저 여기서 프로필을 정한다.
    for i, arg in enumerate(argv):
        if arg == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return ""


PROFILE = (os.environ.get("MAILBRIEF_PROFILE") or _profile_from_argv(sys.argv[1:])).strip()
if PROFILE and not re.fullmatch(r"[\w가-힣-]{1,30}", PROFILE):
    raise SystemExit(f"프로필 이름에는 글자, 숫자, -, _ 만 쓸 수 있습니다: {PROFILE!r}")

FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if FROZEN:
    BASE_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mailbrief"
else:
    BASE_DATA_DIR = PROJECT_ROOT / "data"  # 정답지, 진단 파일, 브리핑 결과. git 제외.
DATA_DIR = BASE_DATA_DIR / "profiles" / PROFILE if PROFILE else BASE_DATA_DIR
CONFIG_JSON = DATA_DIR / "config.json"


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bundled() -> dict[str, str]:
    # 배포 빌드 때 build_exe.py 가 .env 값을 넣어 만드는 파일 (git 제외).
    # Sentry DSN 과 회사 그룹웨어 주소처럼 코드에 적지 않는 값이 들어 있다. 비밀번호는 없다.
    try:
        from mailbrief._bundled import VALUES

        return VALUES
    except ImportError:
        return {}


def _values() -> dict[str, str]:
    """환경변수 > config.json > .env(개발) > exe 에 넣은 값 순서."""
    values = dict(_bundled())
    if not FROZEN:
        values.update({k: v for k, v in _load_dotenv(PROJECT_ROOT / ".env").items() if v})
    if CONFIG_JSON.exists():  # exe 설정, 또는 시험 프로필 설정
        values.update({k: v for k, v in json.loads(CONFIG_JSON.read_text(encoding="utf-8")).items() if v})
    values.update({k: v for k, v in os.environ.items() if v})
    return values


def _list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Site:
    """회사마다 다른 값. 코드에는 적지 않고 .env (배포 땐 exe 안) 에서 읽는다. 항목 설명은 .env.example."""

    groupware_url: str
    groupware_ip: str
    alert_words: tuple[str, ...]
    exclude_senders: tuple[str, ...]
    exclude_subjects: tuple[str, ...]
    timezone: str  # Sentry 출석부가 '월~토 07:50' 을 셀 시간대 (IANA 이름)


def load_site() -> Site:
    values = _values()
    return Site(
        groupware_url=values.get("GROUPWARE_URL", "").rstrip("/"),
        groupware_ip=values.get("GROUPWARE_IP", ""),
        alert_words=tuple(w.lower() for w in _list(values.get("ALERT_WORDS", ""))),
        exclude_senders=_list(values.get("EXCLUDE_SENDERS", "")),
        exclude_subjects=_list(values.get("EXCLUDE_SUBJECTS", "")),
        timezone=values.get("TIMEZONE", "") or "UTC",
    )


SITE = load_site()


@dataclass(frozen=True)
class Config:
    sentry_dsn: str
    user_alias: str
    environment: str


def load_config(*, scheduled: bool = False) -> Config:
    values = _values()

    def get(key: str, default: str = "") -> str:
        return values.get(key) or default

    return Config(
        sentry_dsn=get("SENTRY_DSN"),
        user_alias=get("MAILBRIEF_USER_ALIAS", "user-unknown"),
        # 매일 자동 실행은 prod, 손으로 돌린 개발 실행은 dev (Sentry 에서 나눠 보기). 시험 프로필은 언제나 dev.
        environment="prod" if scheduled and not PROFILE else get("SENTRY_ENVIRONMENT", "dev"),
    )


SETTINGS_JSON = DATA_DIR / "settings.json"  # 설정 창에서 고른 것 (뺄 메일, 화면 테마)
THEMES = {"dark": "다크", "light": "밝게", "system": "Windows 따라가기"}


def theme_choice() -> str:
    """화면 테마. 기본은 다크."""
    try:
        choice = json.loads(SETTINGS_JSON.read_text(encoding="utf-8")).get("theme", "dark")
    except (OSError, ValueError):
        choice = "dark"
    return choice if choice in THEMES else "dark"


def save_theme(choice: str) -> None:
    settings = json.loads(SETTINGS_JSON.read_text(encoding="utf-8")) if SETTINGS_JSON.exists() else {}
    settings["theme"] = choice
    SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_JSON.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_frozen_config(values: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = json.loads(CONFIG_JSON.read_text(encoding="utf-8")) if CONFIG_JSON.exists() else {}
    current.update(values)
    CONFIG_JSON.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
