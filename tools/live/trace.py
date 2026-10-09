"""排查用：带页面调试日志跑一遍，看某个字段为什么填不上。

用法：
  python tools/live/trace.py <站点>                       完整跑一遍（含分区保存），记录每一步
  python tools/live/trace.py <站点> --field 发表时间       只填这一个字段（先打开它所在的分区）
  python tools/live/trace.py <站点> --field 发表时间 --value 2024-07   指定要填的值（默认按资料规划）

日志写到 runs/live/trace-<站点>-<时间>.txt，每一步包括：
  === JOB <字段 id> <控件类型> <候选值> <meta>   驱动发给页面的任务
  [autofill] tap ... <命中的元素 class>           每次真实点击落在哪个元素上（落在遮罩 / 导航栏上就是被挡了）
  [autofill] step ... header 年 月                 日期面板每一步读到的年月
  === RES {...}                                    页面返回的结果
  === OVERLAY [...]                                分区保存 / 展开后页面上残留的弹框
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, launch, open_form, out_dir, site, summarize  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

import autofill.pipeline as pl  # noqa: E402
from autofill.profile import load_profile  # noqa: E402

OVERLAY_JS = r"""() => [...document.querySelectorAll('.el-overlay, .el-message-box, .el-dialog, .ant-modal, [role="alertdialog"]')]
  .filter((e) => e.getClientRects().length).map((e) => e.className.slice(0, 40) + ' || ' + e.innerText.replace(/\s+/g, ' ').slice(0, 120))"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--field", help="只测这个标签的字段（取第一个匹配的）")
    ap.add_argument("--value", help="和 --field 一起用：直接填这个值")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    s = site(a.site)
    logs: list[str] = []
    orig = pl.page_job

    def job(page, target, kind, candidates=None, meta=None, timeout_s=45):
        logs.append(f"=== JOB {target} {kind} {candidates} {meta}")
        page.evaluate("() => { window.__autofillDebug = true; }")
        r = orig(page, target, kind, candidates, meta, timeout_s)
        logs.append(f"=== RES {r}")
        if kind in ("__save", "__expand"):
            page.wait_for_timeout(500)
            logs.append(f"=== OVERLAY {page.evaluate(OVERLAY_JS)}")
        return r

    pl.page_job = job
    with sync_playwright() as p:
        ctx = launch(p, show=a.show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("console", lambda m: logs.append(m.text[:400]) if "[autofill]" in m.text else None)
        open_form(page, s["url"])
        prof = load_profile(ROOT / "profile.yaml")
        if not a.field:
            rep = pl.run(page, prof, report_dir=out_dir(), ctx={"referral": s.get("referral", "")}, save=not a.no_save)
            print("\n".join(summarize(rep)))
        else:
            page.evaluate(pl.page_script())
            target, opened = None, set()
            for _ in range(15):  # 分区编辑式表单：依次打开分区，直到这个字段出现
                fields = pl.annotate(pl.extract(page)["fields"])
                target = next((f for f in fields if f["label"] == a.field), None)
                if target:
                    break
                exps = page.evaluate("() => window.__autofill.findExpanders()")
                nxt = next((e for e in exps if e["section"] not in opened), None)
                if not nxt:
                    break
                opened.add(nxt["section"])
                job(page, nxt["id"], "__expand")
                page.wait_for_timeout(1200)
            if not target:
                sys.exit(f"页面上没找到标签为「{a.field}」的字段")
            items, _ = pl.prepare(fields, prof, None, {"referral": s.get("referral", "")}, overwrite=True)
            it = next(i for i in items if i.id == target["id"])
            cands, meta = ([a.value], {"date": a.value} if re.match(r"^\d{4}-\d{2}", a.value) else {}) \
                if a.value else (it.candidates, it.meta)
            print(f"字段 {target['id']} {target['kind']} 「{target['label']}」 key={it.key} 候选={cands} meta={meta}")
            print("结果：", job(page, target["id"], target["kind"], cands, meta))
        out = out_dir() / f"trace-{a.site}-{time.strftime('%H%M%S')}.txt"
        out.write_text("\n".join(logs), encoding="utf-8")
        print("日志：", out)
        ctx.close()


if __name__ == "__main__":
    main()
