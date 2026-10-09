"""命令行引擎（Playwright 真实点击）在真实网申页上实测。

用法：
  python tools/live/live.py <站点> shot           截图 + 字段概览（不填）
  python tools/live/live.py <站点> dry            只读预演：每个字段打算填什么
  python tools/live/live.py <站点> run [选项]     真实填写，打印没填上 / 待确认的字段
      --save        填完点各分区文字恰好是「保存」的按钮（OPPO 这类；永远不点提交 / 投递）
      --overwrite   页面上和资料不一致的值改成资料值
  python tools/live/live.py <站点> js "<表达式>"   注入页面脚本后执行一段 JS 并打印结果
通用：--show 把浏览器窗口放在屏幕上（默认在屏幕外）

报告：runs/live/run-*.json，截图：runs/live/<站点>-<模式>-<时间>.png
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, launch, open_form, out_dir, site, summarize  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

from autofill.pipeline import annotate, extract, page_script, run  # noqa: E402
from autofill.profile import load_profile  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("mode", choices=["shot", "dry", "run", "js"])
    ap.add_argument("expr", nargs="?", default="")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--resume", help="用哪一份简历（变体 id），缺省为默认简历")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    s = site(a.site)
    stamp = time.strftime("%H%M%S")
    with sync_playwright() as p:
        ctx = launch(p, show=a.show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: (print("页面弹出原生对话框（已取消）：", d.message), d.dismiss()))
        open_form(page, s["url"])
        print("URL:", page.url[:100], "| title:", page.title())
        if a.mode == "shot":
            page.screenshot(path=str(out_dir() / f"{a.site}-shot-{stamp}.png"), full_page=True)
            data = extract(page)
            fs = annotate(data["fields"])
            print("fields:", len(fs), "addButtons:", [b["text"] for b in data["addButtons"]])
            for f in fs:
                print(f"  {f['id']:5} {f['kind']:13} {f['section_kind']:10} #{f['index']} {f['label'][:22]:22} "
                      f"| {f['placeholder'][:14]:14} | {f['value'][:20]}")
        elif a.mode in ("dry", "run"):
            prof = load_profile(ROOT / "profile.yaml", a.resume)
            rep = run(page, prof, dry_run=(a.mode == "dry"), report_dir=out_dir(),
                      ctx={"referral": s.get("referral", "")}, save=a.save, overwrite=a.overwrite)
            if a.mode == "dry":
                for it in rep["items"]:
                    print(f"  {it['status'][:4]} {it['section'][:8]:8} {it['label'][:20]:20} | {it['note'][:90]}")
            print("\n".join(summarize(rep)))
            page.wait_for_timeout(800)
            page.screenshot(path=str(out_dir() / f"{a.site}-{a.mode}-{stamp}.png"), full_page=True)
            print("report:", rep.get("file"))
        else:
            page.evaluate(page_script())
            print(json.dumps(page.evaluate(a.expr), ensure_ascii=False, indent=1)[:6000])
        ctx.close()


if __name__ == "__main__":
    main()
