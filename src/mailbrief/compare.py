"""요약 모델 블라인드 비교. 결과 HTML 과 정답표는 data/compare/ 에만 저장한다.

각 메일마다 두 모델의 요약을 A/B 로 무작위 배치한다. 사용자는 HTML 에서 고르고,
화면 아래에 나오는 선택 코드(예: ABBA...)만 전달한다. 코드에는 메일 내용이 없다.
"""

from __future__ import annotations

import html
import json
import random
import time
from datetime import datetime

from mailbrief.config import DATA_DIR, SITE
from mailbrief.summarize import summarize

COMPARE_DIR = DATA_DIR / "compare"


def build(alias: str, models: tuple[str, str], count: int = 10, seed: int | None = None) -> tuple[str, dict[str, float]]:
    cache_path = DATA_DIR / "answers" / f"cache_{alias}.json"
    mails = [m for m in json.loads(cache_path.read_text(encoding="utf-8")) if m["body"] and m["sender_name"] not in SITE.exclude_senders]
    mails.sort(key=lambda m: m["received_at"], reverse=True)
    mails = mails[:count]
    rng = random.Random(seed)

    rows, key, seconds = [], [], {m: 0.0 for m in models}
    for i, m in enumerate(mails, start=1):
        outs = {}
        for model in models:
            start = time.perf_counter()
            outs[model] = summarize(m["subject"], m["sender_name"], m["body"], model=model)
            seconds[model] += time.perf_counter() - start
        order = list(models)
        rng.shuffle(order)
        key.append(order)  # key[i][0] 이 A
        cells = "".join(
            f"<td><b>{label}</b><p>{html.escape(outs[model].summary)}</p>"
            f"<p class=todo>{html.escape(outs[model].todo) or '<i>(할 일 없음)</i>'}</p>"
            f"<label><input type=radio name=q{i} value={label}> {label}가 더 좋음</label></td>"
            for label, model in zip("AB", order)
        )
        rows.append(
            f"<tr><td class=mail><b>{i}. {html.escape(m['subject'])}</b><br><small>{html.escape(m['sender_name'])} · "
            f"{m['received_at'][:16].replace('T', ' ')}</small><details><summary>본문</summary><pre>{html.escape(m['body'][:3000])}</pre></details></td>{cells}</tr>"
        )

    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (COMPARE_DIR / f"key_{stamp}.json").write_text(json.dumps({"models": models, "key": key}, ensure_ascii=False), encoding="utf-8")
    page = COMPARE_DIR / f"비교_{stamp}.html"
    page.write_text(_PAGE.format(count=len(mails), rows="".join(rows), stamp=stamp), encoding="utf-8")
    return str(page), {m: s / max(len(mails), 1) for m, s in seconds.items()}


def tally(code: str) -> dict[str, int]:
    """선택 코드 'stamp:ABBA..' → 모델별 승수."""
    stamp, picks = code.strip().split(":")
    data = json.loads((COMPARE_DIR / f"key_{stamp}.json").read_text(encoding="utf-8"))
    wins = {m: 0 for m in data["models"]}
    for pick, order in zip(picks, data["key"]):
        if pick in "AB":
            wins[order["AB".index(pick)]] += 1
    return wins


_PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8><title>요약 비교</title>
<style>
body{{font-family:'Malgun Gothic',sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%}} td{{border:1px solid #ddd;padding:10px;vertical-align:top;width:33%}}
td.mail{{background:#fafafa}} .todo{{color:#b03a00}} pre{{white-space:pre-wrap;font-size:12px;max-height:240px;overflow:auto}}
#code{{font-size:20px;font-weight:bold;padding:12px;background:#fff4cc;display:inline-block;margin-top:16px}}
</style></head><body>
<h2>요약 비교 ({count}통)</h2>
<p>각 메일마다 더 나은 요약을 고르세요. A/B 는 메일마다 무작위라 어느 모델인지 알 수 없습니다.<br>
판단 기준: 사실과 맞는지, 할 일을 정확히 짚는지, 읽기 쉬운지.</p>
<table>{rows}</table>
<p>다 고르면 아래 코드를 Claude 에게 알려 주세요 (메일 내용은 들어 있지 않습니다).</p>
<div id=code>-</div>
<script>
const n={count};
function update(){{let s='';for(let i=1;i<=n;i++){{const c=document.querySelector('input[name=q'+i+']:checked');s+=c?c.value:'-';}}
document.getElementById('code').textContent='{stamp}:'+s;}}
document.addEventListener('change',update);update();
</script></body></html>"""
