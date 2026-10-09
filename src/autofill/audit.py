"""离线审计：拿「导出诊断」存下来的页面结构（runs/diag-*.json）重跑一遍字段识别，不需要打开真实网站。

用途：改了规则之后，一次性检查所有已经见过的网站有没有变好 / 变坏；新网站的诊断文件放进来就能看到哪些字段还认不出。
"""

from __future__ import annotations

import json
from pathlib import Path

from .pipeline import annotate, plan
from .profile import load_profile
from .rules import IGNORE

SKIP_KINDS = ("file", "checkbox")


def audit_file(path: Path, profile) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = annotate([dict(f) for f in data.get("fields", [])])
    items, _ = plan(fields, profile, None)
    rows = []
    for f, it in zip(fields, items):
        if f["kind"] in SKIP_KINDS:
            continue
        rows.append({"label": f["label"], "section": f["section"], "section_kind": f["section_kind"],
                     "kind": f["kind"], "key": it.key, "placeholder": f.get("placeholder", "")})
    mapped = [r for r in rows if r["key"] and r["key"] != IGNORE]
    ignored = [r for r in rows if r["key"] == IGNORE]
    unmapped = [r for r in rows if not r["key"]]
    return {"file": path.name, "url": data.get("url", ""), "total": len(rows), "mapped": len(mapped),
            "ignored": len(ignored), "unmapped": unmapped, "rows": rows}


def run_audit(files: list[Path], profile_path: Path, verbose: bool = False) -> list[dict]:
    profile = load_profile(profile_path)
    results = []
    for p in files:
        r = audit_file(p, profile)
        results.append(r)
        pct = round(100 * (r["mapped"] + r["ignored"]) / r["total"]) if r["total"] else 100
        print(f"\n{r['file']}  识别 {r['mapped']} + 忽略 {r['ignored']} / {r['total']}（{pct}%）")
        for u in r["unmapped"]:
            tag = f"{u['section']}/{u['section_kind']}"
            print(f"   ✗ [{tag}] {u['kind']} 标签={u['label']!r} 占位={u['placeholder']!r}")
        if verbose:
            for m in r["rows"]:
                if m["key"]:
                    print(f"   ✓ {m['label'] or '(无标签)'!r:40} → {m['key']}")
    return results
