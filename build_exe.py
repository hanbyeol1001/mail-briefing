"""동기 배포용 exe 빌드: dist/메일브리핑/ 폴더와 dist/메일브리핑_v{버전}.zip 을 만든다.

  uv run python build_exe.py

- Sentry DSN 과 그룹웨어 주소 같은 회사 값은 .env 에서 읽어 src/mailbrief/_bundled.py 로 넣는다 (git 제외 파일).
- 브라우저는 Windows 기본 Edge 를 쓰므로 Chromium 을 넣지 않는다.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from mailbrief import __version__  # noqa: E402
from mailbrief.config import _load_dotenv  # noqa: E402

NAME = "메일브리핑"


# exe 안에 넣는 .env 값. 동기 PC 에는 .env 가 없으니 여기서 넣어 준다.
BUNDLED_KEYS = ("SENTRY_DSN", "GROUPWARE_URL", "GROUPWARE_IP", "ALERT_WORDS", "EXCLUDE_SENDERS", "EXCLUDE_SUBJECTS", "TIMEZONE")
REQUIRED_KEYS = ("SENTRY_DSN", "GROUPWARE_URL", "TIMEZONE")


def main() -> None:
    env = _load_dotenv(ROOT / ".env")
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        sys.exit(f".env 에 {', '.join(missing)} 이 없습니다.")
    values = {key: env[key] for key in BUNDLED_KEYS if env.get(key)}
    (ROOT / "src" / "mailbrief" / "_bundled.py").write_text(f"VALUES = {values!r}\n", encoding="utf-8")

    entry = ROOT / "build" / "entry.py"
    entry.parent.mkdir(exist_ok=True)
    entry.write_text("import sys\nfrom mailbrief.cli import main\nsys.exit(main())\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", str(entry),
            "--name", NAME, "--onedir", "--noconsole", "--noconfirm", "--clean",
            "--paths", str(ROOT / "src"),
            "--collect-submodules", "mailbrief",
            "--hidden-import", "keyring.backends.Windows",
            "--collect-data", "winotify",
            "--collect-data", "sv_ttk",
            "--icon", str(ROOT / "src" / "mailbrief" / "assets" / "icon.ico"),
            "--add-data", f"{ROOT / 'src' / 'mailbrief' / 'assets'};mailbrief/assets",
            "--distpath", str(ROOT / "dist" / f"v{__version__}"), "--workpath", str(ROOT / "build" / "work"), "--specpath", str(ROOT / "build"),
        ],
        check=True,
    )
    folder = ROOT / "dist" / f"v{__version__}" / NAME
    (folder / "읽어주세요.md").write_text(README, encoding="utf-8")
    shutil.copyfile(ROOT / "CHANGELOG.md", folder / "바뀐점.md")
    archive = shutil.make_archive(str(ROOT / "dist" / f"{NAME}_v{__version__}"), "zip", folder.parent, NAME)
    size = Path(archive).stat().st_size / 1024 / 1024
    print(f"완료: {archive} ({size:.0f}MB)")


README = """# 메일 브리핑 설치 안내

## 처음 설치
1. 이 폴더를 통째로 `C:\\mailbrief` 같은 곳에 두세요.
   - 바탕화면이나 다운로드 폴더는 실수로 지우기 쉬워서 추천하지 않아요.
2. `메일브리핑.exe` 를 더블클릭하면 설정 창이 떠요. **처음 한 번만 설정** 칸을 위에서부터 차례로 눌러 주세요.
   1. 그룹웨어 계정 입력
   2. 로그인 시험
   3. 그래픽카드·로컬 AI 확인
   4. 자동 실행 등록 (월~토 07:50)
   5. 메일 조회하기 (메일을 읽고 브리핑을 만들어요)
3. 그 뒤로는 월~토 07:50 에 알림이 오고, 누르면 브리핑이 열려요.
   - 첫 실행은 메일이 많으면 몇 분 걸릴 수 있어요. 다음 날부터는 새 메일만 읽어서 빨라요.

## 새 버전으로 바꿀 때
- 압축을 풀어 **원래 폴더에 덮어쓰기**만 하면 돼요. 계정·설정·데이터는 그대로 남아요.
- 폴더 위치를 옮겼다면 4번(자동 실행 등록)을 한 번 더 눌러 주세요.
- 무엇이 바뀌었는지는 `바뀐점.md` 에 있어요.

## 브리핑 다시 보기
- 알림을 놓쳤으면 `메일브리핑.exe` 를 열고 맨 위 **최신 브리핑 보기** 를 누르세요.
- 하루 중간에 새 메일까지 보고 싶으면 5번 **메일 조회하기** 의 **조회** 를 누르세요. 보통 1분 안팎이면 새 브리핑이 열려요.
- 지난 브리핑은 `%LOCALAPPDATA%\\mailbrief\\briefings` 폴더에 날짜별로 있어요.

## 화면 색
- 기본은 다크 모드예요. 설정 창 맨 위 오른쪽 **Light | Dark** 를 누르면 바뀌고, 브리핑도 다음 조회부터 같은 색으로 나와요.

## 브리핑 카드 버튼
- **자세히**: 요약 전체와 '왜 요청으로 봤는지' 근거 문장이 펼쳐져요.
- 위쪽 검색칸에 보낸 사람이나 제목 일부를 쓰면 그 메일만 남아요.
- **완료**: 회신했거나 확인한 메일에 체크하면 칸 맨 아래 '완료한 메일' 로 접혀 들어가요. 잘못 눌렀으면 펼쳐서 다시 풀면 돼요.
- **답장**: 그룹웨어 답장 쓰기 화면이 바로 열려요 (받는 사람이 채워져 있어요).
- **메일 보기**: 그룹웨어에서 그 메일이 열려요. 이때는 그 메일이 '읽음'으로 바뀌어요.
- 답장·메일 보기는 평소 쓰는 브라우저가 그룹웨어에 로그인돼 있어야 해요.

## 안 봐도 되는 메일 빼기
- 설정 창의 **브리핑에서 뺄 메일 → 관리** 에서 보낸 사람이나 제목에 들어간 말을 추가하면 다음 브리핑부터 빠져요.

## 정답지 (선택, 패턴 정확도 확인용)
1. 설정 창의 **정답지 만들기** → 엑셀이 열리면 노란 칸만 채우고 저장 (약 40통, 10~15분)
   - 정답_요청: 나에게 회신이나 확인을 요청한 메일이면 `Y`, 아니면 `N`
   - 정답_종류: `Y` 인 메일만. 답장·제출처럼 보내야 끝나면 `회신`, 읽고 챙기면 되면 `검토`
   - 정답_기한: 기한이 있으면 `2026-11-20` 처럼, 없으면 비움
2. **채점하기** → 아래 진행 기록에 나온 숫자 줄들을 복사해서 관리자에게 보내기 (메일 내용 없음)

## 안심하세요
- 메일은 **읽기만** 해요. 보내거나 지우거나 옮기지 않아요. 안 읽은 메일은 다시 '읽지 않음'으로 돌려놓아요.
- 비밀번호는 이 PC 의 Windows 자격 증명 관리자에만 저장돼요.
- 메일 요약은 이 PC 안의 로컬 AI(Ollama)가 해요. 메일 내용은 PC 밖으로 나가지 않아요.
- 오류가 나면 '에러 종류와 실패 단계'만 관리자에게 전송돼요 (메일 제목·본문·보낸 사람 없음).
- 브리핑, 정답지 등 데이터는 `%LOCALAPPDATA%\\mailbrief` 에 있어요.
"""

if __name__ == "__main__":
    main()
