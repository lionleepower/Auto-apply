"""浏览器插件在真实网申页上的端到端实测（真实插件代码 + 真实点击 + 已登录的页面）。

用法：
  python tools/live/ext_run.py oppo                 一个页面
  python tools/live/ext_run.py oppo iflytek         同时填多个页面（并行测试）
  选项：--no-save（不点分区「保存」）  --llm（规则认不出时用 AI）  --show（窗口放在屏幕上）

做法：把 extension/ 复制一份改连测试端口 8766，在 8766 起一个本地服务（报告写到 runs/live/），
用已登录的 .browser-data-msedge 打开各网申页，再打开插件侧边栏页面，调用侧边栏自己的 start()——
和你在侧边栏里点「填写本页」走的是同一段代码。几个页面依次点「填写本页」、不等前一个填完，
过程中来回切换「当前标签页」，核对侧边栏是否各显示各的进度。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import extension_copy, extension_id, launch, out_dir, site, summarize, test_server  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

STATE_JS = """async (ids) => {
  const hosts = Object.keys(ids);
  const h = hosts[Math.floor(Date.now() / 6000) % hosts.length];  // 每 6 秒「切」到另一个标签页
  activeTab = async () => chrome.tabs.get(ids[h]);
  await refreshContext();
  const $ = (id) => document.getElementById(id);
  return { showing: h, progress: $('progress').hidden ? '' : $('progress').textContent,
           others: $('jobs').hidden ? '' : $('jobs').textContent, result: !$('result').hidden,
           done: Object.keys(window.__done), busy: hosts.filter((x) => jobOf(ids[x]).busy),
           waiting: hosts.filter((x) => /切到这个标签页/.test((jobOf(ids[x]).progress || {}).text || '')) };
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sites", nargs="+")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--debug", action="store_true", help="记录各页面的 [autofill] 调试日志到 runs/live/ext-trace-*.txt")
    a = ap.parse_args()
    cfg = {name: site(name) for name in a.sites}
    ext = extension_copy()
    with test_server(), sync_playwright() as p:
        ctx = launch(p, show=a.show, extension=ext)
        ext_id = extension_id(ctx)
        pages = {}
        page_logs: dict[str, list[str]] = {name: [] for name in cfg}
        for i, name in enumerate(cfg):
            pg = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
            pg.on("dialog", lambda d: (print("页面弹出原生对话框（已取消）：", d.message), d.dismiss()))
            pages[name] = pg
            if a.debug:
                pg.on("console", lambda m, n=name: page_logs[n].append(m.text[:400]) if "[autofill]" in m.text else None)
            pg.goto(cfg[name]["url"], wait_until="commit", timeout=60000)
        for pg in pages.values():  # 等表单都渲染出来
            for _ in range(40):
                if pg.evaluate("() => [...document.querySelectorAll('input, textarea')]"
                               ".filter(e => e.getClientRects().length).length") >= 5:
                    break
                pg.wait_for_timeout(1000)
        if a.debug:
            for pg in pages.values():
                pg.evaluate("() => { window.__autofillDebug = true; }")
        panel = ctx.new_page()
        errors = []
        panel.on("console", lambda m: errors.append(m.text[:300]) if m.type == "error" else None)
        panel.goto(f"chrome-extension://{ext_id}/sidepanel.html")
        panel.wait_for_timeout(2000)
        print("插件状态:", panel.inner_text("#status-text"))
        panel.evaluate("([save, llm]) => { document.getElementById('opt-save').checked = save;"
                       " document.getElementById('opt-llm').checked = llm; }", [not a.no_save, a.llm])
        hosts = {name: cfg[name]["url"].split("/")[2] for name in cfg}
        ids = panel.evaluate("""async (hosts) => {
            const out = {};
            for (const [name, h] of Object.entries(hosts)) {
              const [t] = await chrome.tabs.query({ url: '*://' + h + '/*' }); out[name] = t.id; }
            return out; }""", hosts)
        t0 = time.time()
        # 依次「切到」每个标签页点「填写本页」，不等前一个填完
        panel.evaluate("""async ([ids, refs]) => {
            window.__done = {};
            for (const [name, id] of Object.entries(ids)) {
              activeTab = async () => chrome.tabs.get(id);
              await refreshContext();
              if (refs[name]) document.getElementById('referral').value = refs[name];
              start(false).then(() => { window.__done[name] = Date.now(); });
              await new Promise((r) => setTimeout(r, 300));
            }
        }""", [ids, {name: c.get("referral", "") for name, c in cfg.items()}])
        last = None
        while time.time() - t0 < 900:
            panel.wait_for_timeout(3000)
            st = panel.evaluate(STATE_JS, ids)
            line = json.dumps(st, ensure_ascii=False)
            if line != last:
                print(f"[{round(time.time() - t0):>4}s]", line[:300])
                last = line
            for name in st["waiting"]:  # 模拟你切到这个标签页：后台没选上的字段这时补填
                print(f"[{round(time.time() - t0):>4}s] {name} 在等页面显示 → 切到这个标签页")
                pages[name].bring_to_front()
            if len(st["done"]) == len(ids):
                break
        res = panel.evaluate("""(ids) => Object.fromEntries(Object.entries(ids).map(([name, id]) => {
            const j = jobOf(id); return [name, j.report || { error: j.progress && j.progress.text }]; }))""", ids)
        print(f"\n全部完成，用时 {round(time.time() - t0)}s")
        for name, rep in res.items():
            print(f"\n=== {name}")
            if "error" in rep:
                print("  出错：", rep["error"])
                continue
            print("\n".join(summarize(rep)))
            print("  report:", rep.get("file"))
        if errors:
            print("\n侧边栏控制台报错：", *errors[:10], sep="\n  ")
        stamp = time.strftime("%H%M%S")
        if a.debug:
            for name, lines in page_logs.items():
                f = out_dir() / f"ext-trace-{name}-{stamp}.txt"
                f.write_text("\n".join(lines), encoding="utf-8")
                print("调试日志：", f)
        for name, pg in pages.items():
            pg.bring_to_front()
            pg.screenshot(path=str(out_dir() / f"ext-{name}-{stamp}.png"), full_page=True)
        ctx.close()


if __name__ == "__main__":
    main()
