"""진단 파일. 메일 정보가 필요한 에러는 Sentry 대신 이 PC 안에만 저장한다.

Sentry 에는 diag_id 만 보낸다. 파일을 공유할지는 사람이 직접 열어 보고 정한다.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from mailbrief.config import DATA_DIR

DIAG_DIR = DATA_DIR / "diagnostics"


def save(stage_name: str, payload: dict[str, Any]) -> str:
    """진단 정보를 로컬 파일로 저장하고 diag_id 를 돌려준다."""
    diag_id = secrets.token_hex(3)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "diag_id": diag_id,
        "stage": stage_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    path = DIAG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{diag_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return diag_id


def remove(diag_id: str) -> None:
    for path in DIAG_DIR.glob(f"*_{diag_id}.json"):
        path.unlink()


def pending() -> list[Path]:
    """아직 확인하지 않은 진단 파일 목록. 브리핑 화면에 안내할 때 쓴다."""
    if not DIAG_DIR.exists():
        return []
    return sorted(DIAG_DIR.glob("*.json"))
