"""PC 환경 확인과 설치 도우미: 작업 스케줄러, 그래픽카드, Ollama."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mailbrief.config import FROZEN, PROFILE, PROJECT_ROOT
from mailbrief.summarize import DEFAULT_MODEL, OLLAMA_URL, available

TASK_NAME = "mailbrief 메일 브리핑"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _ps(script: str, timeout: float = 60) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300])
    return out.stdout.strip()


# ── 작업 스케줄러 ────────────────────────────────────────────────────────


def run_command() -> tuple[str, str, str]:
    """(실행 파일, 인자, 작업 폴더). exe 배포본이면 exe 자신, 개발 환경이면 venv 의 pythonw."""
    if FROZEN:
        exe = Path(sys.executable)
        return str(exe), "run --scheduled", str(exe.parent)
    pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return str(pythonw), "-m mailbrief.cli run --scheduled", str(PROJECT_ROOT)


def register_task() -> str:
    if PROFILE:
        raise RuntimeError("시험 프로필은 자동 실행을 등록할 수 없습니다.")
    exe, args, workdir = run_command()
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
    _ps(
        f"$a = New-ScheduledTaskAction -Execute {q(exe)} -Argument {q(args)} -WorkingDirectory {q(workdir)};"
        "$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday -At 07:50;"
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 40)"
        " -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName {q(TASK_NAME)} -Action $a -Trigger $t -Settings $s -Force"
        " -Description '월~토 07:50 메일 브리핑' | Out-Null"
    )
    return _ps(f"(Get-ScheduledTaskInfo -TaskName {q(TASK_NAME)}).NextRunTime.ToString('yyyy-MM-dd HH:mm')")


def task_registered() -> bool:
    """자동 실행이 이미 등록돼 있는지 (설정 창이 '처음 설정 끝'을 판단할 때)."""
    if PROFILE:
        return False
    try:
        return _ps(f"[bool](Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue)", timeout=20) == "True"
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


# ── 그래픽카드 ───────────────────────────────────────────────────────────


@dataclass
class Gpu:
    name: str
    vram_gb: float | None  # NVIDIA 만 정확히 알 수 있다

    @property
    def usable(self) -> bool:
        return "NVIDIA" in self.name.upper()


def gpus() -> list[Gpu]:
    found: list[Gpu] = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        ).stdout
        for line in out.strip().splitlines():
            name, mem = [x.strip() for x in line.split(",")]
            found.append(Gpu(name, round(int(mem) / 1024, 1)))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        names = _ps("(Get-CimInstance Win32_VideoController).Name").splitlines()
    except (RuntimeError, subprocess.SubprocessError):
        names = []
    for n in names:
        if n.strip() and not any(n.strip() == g.name for g in found):
            found.append(Gpu(n.strip(), None))
    return found


# ── Ollama ──────────────────────────────────────────────────────────────


def ollama_exe() -> Path | None:
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return path if path.exists() else None


def ollama_running() -> bool:
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3)
        return True
    except OSError:
        return False


def start_ollama() -> None:
    exe = ollama_exe()
    if exe and not ollama_running():
        subprocess.Popen([str(exe), "serve"], creationflags=_NO_WINDOW)


def model_ready(model: str = DEFAULT_MODEL) -> bool:
    return available(model)


def pull_model(progress: Callable[[str], None], model: str = DEFAULT_MODEL) -> None:
    """모델을 내려받는다. progress 에 '내려받는 중 45%' 같은 문장을 계속 넘긴다."""
    req = urllib.request.Request(
        OLLAMA_URL + "/api/pull", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"}
    )
    last = ""
    with urllib.request.urlopen(req, timeout=3600) as resp:
        for raw in resp:
            msg = json.loads(raw.decode("utf-8") or "{}")
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            total, done = msg.get("total"), msg.get("completed")
            text = f"모델 내려받는 중 {done * 100 // total}%" if total and done else msg.get("status", "")
            if text != last:
                progress(text)
                last = text
