"""로컬 AI(Ollama) 요약. 메일 내용은 이 PC 의 localhost 로만 간다.

Ollama 가 없거나 실패하면 요약 칸만 비우고 브리핑은 계속한다 (Sentry 에는 '요약 단계 실패'만).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from mailbrief.monitoring import SafeError

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"  # 블라인드 비교 5:4 로 품질 비슷, 더 빠르고 작음 (2026-11-17)
MAX_BODY_CHARS = 4000

_PROMPT = """다음은 회사 메일이다. 받는 사람이 빠르게 파악할 수 있게 한국어로 요약해라.

규칙:
- summary: 무슨 메일인지 한 문장 (40자 안팎). 인사말, 서명은 무시.
- todo: 받는 사람에게 요청한 일이 있으면 '언제까지 무엇을 누구에게' 형태로 한 문장. 없으면 빈 문자열.
- 메일에 없는 내용을 지어내지 마라.
- JSON 으로만 답해라: {{"summary": "...", "todo": "..."}}

[제목] {subject}
[보낸 사람] {sender}
[본문]
{body}
"""


@dataclass
class Summary:
    summary: str
    todo: str


def _post(path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL + path, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def available(model: str = DEFAULT_MODEL) -> bool:
    try:
        tags = json.loads(urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=5).read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return any(m.get("name") == model for m in tags.get("models", []))


def summarize(subject: str, sender: str, body: str, *, model: str = DEFAULT_MODEL, timeout: float = 180) -> Summary:
    prompt = _PROMPT.format(subject=subject, sender=sender, body=body[:MAX_BODY_CHARS])
    try:
        data = _post(
            "/api/chat",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "think": False,  # qwen3 의 생각 과정 출력 끄기
                "options": {"temperature": 0.2},
            },
            timeout,
        )
    except (urllib.error.URLError, OSError) as exc:
        raise SafeError("로컬 AI 연결 실패 (Ollama 실행 확인)") from exc
    try:
        parsed = json.loads(data["message"]["content"])
        return Summary(str(parsed.get("summary", "")).strip(), str(parsed.get("todo", "")).strip())
    except (KeyError, ValueError, AttributeError) as exc:
        raise SafeError("로컬 AI 응답 형식이 맞지 않음") from exc
