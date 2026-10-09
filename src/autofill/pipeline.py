"""抽取 → 补齐重复段落 → 映射 → 填写 → 高亮。全程不点击提交。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from .coverage import unused_content
from .llm import ChatClient, llm_map
from .profile import Profile
from .rules import (DATE_KEYS, IGNORE, KEY_CATALOG, LIST_SECTIONS, SKIP_EXPAND, candidates_for, classify_section,
                    classify_trail, format_date, match_label, parse_hint, split_alternatives, split_region)

JS_DIR = Path(__file__).parent / "js"


def page_script() -> str:
    """页面脚本 = 通用核心 + js/adapters/ 下各招聘系统的适配器（按文件名顺序拼进核心的挂载点）。
    版本号是拼好后全文的内容哈希，页面里的旧版本会被整个替换。"""
    src = (JS_DIR / "autofill.js").read_text(encoding="utf-8")
    adapters = "\n".join(f.read_text(encoding="utf-8") for f in sorted((JS_DIR / "adapters").glob("*.js")))
    src = src.replace("/*@@ADAPTERS@@*/", adapters)
    return src.replace("__AUTOFILL_VERSION__", hashlib.sha1(src.encode("utf-8")).hexdigest()[:12])


JS = page_script()
DATE_KINDS = ("date_picker", "date", "month", "text")
PICKER_KINDS = ("date_picker", "date", "month")
GROUP_KINDS = PICKER_KINDS + ("custom_select",)
YES_NO = {"是": ["有"], "否": ["没有", "无"], "有": ["是"], "没有": ["否", "无"], "无": ["否", "没有"]}
YES_NO_OPTIONS = {"是", "否", "有", "没有", "无"}
LIST_VALUE_KEYS = {"basic.expected_city", "basic.languages"}


@dataclass
class Item:
    id: str
    label: str
    section: str
    section_kind: str
    kind: str
    index: int = 0
    key: str | None = None
    source: str | None = None  # rule / llm
    candidates: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # 日期控件需要的 ISO 日期等
    profile_index: int = 0  # 取的是资料里第几段
    status: str = "unfilled"  # filled / uncertain / unfilled / sensitive / kept
    note: str = ""
    chosen: str | None = None
    action: str = ""  # "fill"：需要在页面上填写
    replacing: str | None = None  # 要覆盖的页面原值
    risk: str = ""  # "high"：猜了一个填错代价大的值（如选了最接近的学校），用红色标出


def _norm(label: str) -> str:
    return re.sub(r"\s+", "", label or "")


def extract(page: Page) -> dict:
    page.evaluate(JS)
    return page.evaluate("() => window.__autofill.extract()")


PROMPT_ONLY = re.compile(r"^(请?(输入|选择|填写)|)$")
PLACEHOLDER_LABEL = re.compile(r"^([^\s请]{2,14})\s+\S")


def derive_label(f: dict) -> None:
    """很多下拉没有单独的标签，而是把标签写在控件里：「学历 请选择 请选择」「国籍/地区 中国」。"""
    if f.get("label") or f["kind"] in ("file", "checkbox", "radio"):
        return
    ph = (f.get("placeholder") or "").strip()
    # 「证件号码」一行是「证件类型下拉 + 号码输入框」，标签只在行首：没有标签的输入框用分区标题
    if (f["kind"] == "text" and PROMPT_ONLY.match(ph)
            and match_label(f.get("section", ""), f["section_kind"]) == "basic.id_number"):
        f["label"], f["_label_from_ph"] = f["section"], True
        return
    m = PLACEHOLDER_LABEL.match((f.get("placeholder") or "").strip())
    if m:
        f["label"], f["_label_from_ph"] = m.group(1), True


def mark_unlabeled_date_pairs(fields: list[dict]) -> None:
    """列表分区里连续两个没有标签、没有占位文字的输入框，是一段经历的起止时间（飞书招聘：日期范围框）。"""
    i = 0
    while i < len(fields) - 1:
        a, b = fields[i], fields[i + 1]
        if all(x["kind"] == "text" and not x.get("label") and not x.get("placeholder")
               and x["section_kind"] in LIST_SECTIONS and x.get("section") == a.get("section") for x in (a, b)):
            nxt = fields[i + 2] if i + 2 < len(fields) else None
            if not (nxt and nxt["kind"] == "text" and not nxt.get("label") and not nxt.get("placeholder")
                    and nxt.get("section") == a.get("section")):  # 三个连着的不是日期对，不猜
                for x in (a, b):
                    x["label"], x["_datebox"] = "起止时间", True
                i += 2
                continue
        i += 1


def annotate(fields: list[dict]) -> list[dict]:
    """给每个字段加 section_kind 和段落序号（同一分区里同名标签第几次出现）。"""
    seen: dict[tuple[str, str], int] = {}
    prev: dict | None = None
    last: dict | None = None
    for f in fields:
        trail = f.get("sectionTrail") or [f["section"]]
        kind = classify_trail(trail, None)
        # 有的站点把字段标签也当成了分区标题（百度：「工作描述」之前的标题全是别的字段的标签），整个标题链都认不出时，
        # 链里出现了上一个字段的分区名 / 标签，就是同一个分区
        if kind is None and last and last["section_kind"] != "basic":
            anchors = {last.get("section"), (last.get("label") or "").strip()}
            if any(t in anchors for t in trail[1:]):
                kind = last["section_kind"]
        f["section_kind"] = kind or "basic"
        derive_label(f)
        last = f
    mark_unlabeled_date_pairs(fields)
    for f in fields:
        # 同一个「起止时间」标签下并排的两个日期框是同一段经历（开始 / 结束），不是下一段（OPPO Element Plus）
        if (prev and (f["kind"] in GROUP_KINDS or f.get("_datebox")) and prev["kind"] == f["kind"]
                and f["label"] and f["label"] == prev["label"] and f["section_kind"] == prev["section_kind"]
                and f.get("placeholder", "") == prev.get("placeholder", "")
                and (f["kind"] != "date_picker" or not prev.get("_paired"))):
            f["index"], f["_paired"] = prev["index"], True
            f["pos"] = prev.get("pos", 0) + 1
            prev["_grouped"] = True
            prev = f
            continue
        prev = f
        # 共用「起止时间」标签的两个日期框靠占位文字区分，否则第二个会被当成下一段经历
        ph = f.get("placeholder", "") if f["kind"] in DATE_KINDS else ""
        k = (f["section_kind"], _norm(f["label"]) or f.get("hint", ""), ph)
        f["index"] = seen.get(k, 0)
        seen[k] = f["index"] + 1
        # id 里带了段落序号（AntD 的 Form 路径）就以它为准
        _, hint_idx, _ = parse_hint(f.get("hint", ""))
        if hint_idx is not None and f["section_kind"] in LIST_SECTIONS:
            f["index"] = hint_idx
    return fields


def section_counts(fields: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in fields:
        if f["section_kind"] in LIST_SECTIONS:
            counts[f["section_kind"]] = max(counts.get(f["section_kind"], 0), f["index"] + 1)
    return counts


def next_add_click(data: dict, profile: Profile, stuck: set[str]) -> tuple[dict, str, int] | None:
    """还缺段落的分区里，下一个该点的「添加」按钮：(按钮, 分区, 点之前的段落数)。插件和 Playwright 共用。"""
    fields = annotate(data["fields"])
    counts = section_counts(fields)
    for sec in sorted(LIST_SECTIONS - stuck):
        need = len(profile.section_items(sec))
        if counts.get(sec, 0) >= need:
            continue
        btn = next(
            (b for b in data["addButtons"]
             if (classify_section(b["text"], None) or classify_trail(b.get("sectionTrail") or [b["section"]])) == sec),
            None,
        )
        if btn:
            return btn, sec, counts.get(sec, 0)
    return None


def expand_sections(page: Page, profile: Profile, settle_ms: int = 300) -> dict:
    """资料里有几段经历，就点几次「添加」，直到页面上的段落数够用。"""
    stuck: set[str] = set()
    data = extract(page)
    for _ in range(20):
        nxt = next_add_click(data, profile, stuck)
        if not nxt:
            break
        btn, sec, before = nxt
        page.evaluate("id => window.__autofill.clickAdd(id)", btn["id"])
        page.wait_for_timeout(settle_ms)
        data = extract(page)
        if section_counts(annotate(data["fields"])).get(sec, 0) <= before:
            stuck.add(sec)  # 点了没反应，别死循环
    data["fields"] = annotate(data["fields"])
    return data


def plan(fields: list[dict], profile: Profile, llm: ChatClient | None = None) -> tuple[list[Item], str | None]:
    items: list[Item] = []
    for f in fields:
        it = Item(id=f["id"], label=f["label"], section=f["section"], section_kind=f["section_kind"],
                  kind=f["kind"], index=f["index"])
        if f["kind"] in ("file", "checkbox"):
            it.note = "附件/勾选项请手动处理"
        elif f["kind"] == "date_range" and f["section_kind"] in LIST_SECTIONS:
            # 「在校时间」「起止时间」这类一个控件填两个日期
            it.key, it.source = f"{f['section_kind']}.period", "rule"
        else:
            # 单选组常常没有自己的标签，用分区标题代替（如「是否服从调剂」）
            label = f["label"] or (f["section"] if f["kind"] == "radio" else "")
            # 资料里写好的固定答案（亲属、国籍、信息来源…）优先于一切规则
            pat = next((p for p in profile.answers if label and re.search(p, label, re.I)), None)
            gate = classify_section(label, None) if (f["kind"] == "radio" and len(label) <= 12
                                                      and "是否" not in label and f.get("options")
                                                      and all(o in YES_NO_OPTIONS for o in f["options"])) else None
            if pat:
                it.key = f"answer:{pat}"
            elif gate in LIST_SECTIONS:
                it.key = f"has.{gate}"  # OPPO：选「有」才会展开对应分区的表单
            else:
                it.key = match_label(label, f["section_kind"])
            sec = f["section_kind"]
            if it.key is None and f["kind"] in DATE_KINDS and sec in LIST_SECTIONS:
                # 「起止时间」下面并排两个日期框，共用一个 label：依次看占位文字、id、前后顺序
                ph_key = match_label(f.get("placeholder", ""), sec)
                _, _, attr = parse_hint(f.get("hint", ""))
                prev = items[-1] if items else None
                if ph_key and ph_key.rpartition(".")[2] in ("start", "end"):
                    it.key = ph_key
                elif prev and prev.key == f"{sec}.start" and prev.label == f["label"] and prev.index == f["index"] \
                        and prev.kind in DATE_KINDS:
                    it.key = f"{sec}.end"  # 同一标签下的第二个日期框
                elif attr:
                    it.key = f"{sec}.{attr}"
                elif re.search(r"起止|时间段|在校时间|就读时间", f["label"]):
                    it.key = f"{sec}.start"
            if it.key in ("basic.phone", "basic.id_number") and f["kind"] in ("select", "custom_select"):
                it.key = IGNORE  # 和号码共用 label 的区号 / 证件类型下拉
            it.source = "rule" if it.key else None
        items.append(it)

    llm_error = None
    # 没有标签的字段不交给模型：上次实测里模型把无标签的「证件类型」「区号」猜成了证件号和籍贯
    todo = [f for f, it in zip(fields, items)
            if it.key is None and f["kind"] not in ("file", "checkbox") and f["label"].strip()]
    if llm and todo:
        try:
            mapping = llm_map(llm, todo)
        except Exception as e:  # 网络或配置问题：退回纯规则，并在报告里写明
            mapping, llm_error = {}, f"{type(e).__name__}: {e}"
        for it in items:
            if mapping.get(it.id):
                it.key, it.source = mapping[it.id], "llm"
    return items, llm_error


def _present(v):
    """结束时间写「至今 / 现在 / present」的，换成当前年月。"""
    if isinstance(v, str) and re.fullmatch(r"\s*(至今|今|现在|目前|present|now)\s*", v, re.I):
        return datetime.now().strftime("%Y-%m")
    return v


def resolve(it: Item, f: dict, profile: Profile, ctx: dict | None = None) -> None:
    if it.key and it.key.startswith("ctx."):
        # 投递相关、不属于个人资料的值（内推码），由批量模式 / 命令行 / URL 参数提供
        v = (ctx or {}).get(it.key[4:])
        if v:
            it.candidates = [str(v)]
        else:
            it.note = "没有内推码（--referral 指定，或在投递表里填）"
        return
    if it.key is None:
        if not it.note:
            it.note = "没识别出对应的资料项"
        return
    if it.key == IGNORE:
        it.note = "该字段不自动填写"
        return
    if it.key.startswith("has."):
        has = bool(profile.section_items(it.key[4:]))
        it.candidates = ["有", "是"] if has else ["没有", "否", "无"]
        return
    if it.key.startswith("answer:"):
        # 答案可以写多个说法，用「、」分隔：「内部推荐、内推」（OPPO 的选项叫「内部推荐」）
        text = str(profile.answers[it.key[7:]]).strip()
        variants = [v.strip() for v in re.split(r"\n\s*-{3,}\s*\n", text) if v.strip()]
        if len(variants) > 1:  # 单独一行 --- 分隔：长版在前，短版在后；站点有字数上限时页面脚本会换用放得下的那版
            it.candidates, it.note = variants, "资料里的固定答案（有长短两个版本）"
            return
        # 长文本 / 带标点的整句是一份完整回答（开放题），不能拆；短词才按「、」拆成多个说法
        long_text = len(text) > 24 or "\n" in text or bool(re.search(r"[，。；：,;:]", text))
        answers = [text] if long_text else [a for a in re.split(r"[、/]", text) if a]
        it.candidates = answers + [y for a in answers for y in YES_NO.get(a, [])]
        it.note = "资料里的固定答案"
        return
    if it.key in profile.never_fill:
        it.status, it.note = "sensitive", "敏感字段，请手动填写"
        return
    head, _, attr = it.key.partition(".")
    idx = it.index if head in LIST_SECTIONS and head == it.section_kind else 0
    ph = f.get("placeholder", "")
    if attr == "period":
        start, end = profile.get(f"{head}.start", idx), _present(profile.get(f"{head}.end", idx))
        if start and end:
            it.candidates = [format_date(start, "text", ph), format_date(end, "text", ph)]
            it.meta = {"range": [start, end]}
        else:
            it.note = "资料里起止时间不全"
        return
    it.profile_index = idx
    if it.key == "basic.available" and it.kind in ("date_picker", "date", "month") or (
            it.key == "basic.available" and it.kind == "date_range"):
        # 日期框要的是具体日期；资料里写「随时到岗」这类文字时不硬填
        raw_av = profile.basic.available or ""
        m_av = re.search(r"\d{4}\s*[-/.年]\s*\d{1,2}(?:\s*[-/.月]\s*\d{1,2})?", raw_av)
        if not m_av:
            it.note = f"资料里的到岗时间是「{raw_av[:12]}」，不是具体日期；日期框请手动选，或在资料里写成 2026-11-01"
            return
        it.candidates = [format_date(re.sub(r"\s*[/.年月]\s*", "-", m_av.group()).strip("-"), it.kind, ph)]
        return
    if it.key == "basic.portfolio":
        link = profile.basic.portfolio or profile.basic.github or profile.basic.homepage
        if not link:
            it.note = "资料里没有作品集链接（可填 GitHub）"
        else:
            it.candidates = [link]
        return
    if it.key == "portfolio.desc":
        repos = [f"{p.name}（{p.url}）" for p in profile.projects if p.url]
        if repos:
            it.candidates = ["代表作品（GitHub 公开仓库）：" + "；".join(repos)]
        else:
            it.note = "资料里的项目没有链接，写不出作品说明"
        return
    if it.key == "education.gpa_scale":
        it.candidates = candidates_for(it.key, profile.get("education.gpa", idx), it.kind, ph)
        if not profile.get("education.gpa", idx):
            it.candidates, it.note = [], "资料里没有 GPA"
        return
    raw = profile.get(it.key, idx)
    if it.key == "education.gpa" and raw and "/" in str(raw) and re.search(r"绩点|^gpa成绩|成绩", f["label"].lower() + ph):
        raw = str(raw).split("/")[0].strip()  # 另有「GPA 类型」下拉的站点，成绩框只收数字（百度校招）
    if attr == "end" and _present(raw) != raw:
        raw = _present(raw)  # 「至今」：日期框里填当前月份
        it.note = "资料写的「至今」，按当前月份填"
    if it.key == "basic.english_score":
        m = re.search(r"\d+(\.\d+)?", profile.basic.english or "")
        raw = m.group(0) if m else None
    if it.key == "education.is_highest" and idx < len(profile.education):
        raw = idx == 0  # 资料里教育经历按学历从高到低排
    if it.key == "basic.github" and it.kind == "textarea" and raw:
        # 「请输入所有 Github 链接」：把各项目仓库一起列上，表上没有项目栏时这是项目唯一的露出口
        repos = [f"{pr.name}：{pr.url}" for pr in profile.projects if pr.url and "github" in pr.url.lower()]
        it.candidates = ["\n".join([str(raw)] + repos)]
        return
    if it.key == "education.city" and it.kind == "custom_select":
        loc = profile.get("education.location", idx) or " ".join(
            x for x in (profile.get("education.country", idx), raw) if x)
        parts = [x for x in re.split(r"[\s/／,，]+", loc or "") if x]
        parts += [x.removesuffix("省").removesuffix("市") for x in parts if x[-1:] in "省市"]
        # 从最具体的一级往上试：城市框里也可能有带「Scotland」字样的选项，先试城市才不会选错级
        it.candidates = parts[::-1]
        if not it.candidates:
            it.note = "资料里没有「学校所在地」"
        return
    if it.key == "education.school" and it.kind in ("custom_select", "text") and raw:
        it.candidates = [c for c in (raw, profile.get("education.school_en", idx)) if c]
        return
    if it.kind == "cascader" and it.key == "education.school" and raw:
        # 学校级联通常是「国家/省份 → 学校」
        it.candidates = [c for c in (profile.get("education.country", idx), raw) if c]
    else:
        it.candidates = candidates_for(it.key, raw, it.kind, ph)
    if it.key in LIST_VALUE_KEYS and len(it.candidates) > 1 and f.get("occurrence", 0) > 0:
        # 「其他意向城市1」「擅长的其他开发语言2」：同一个资料项的第 n 个框取第 n 个值。
        # 按出现顺序算，不按标签里的编号算（讯飞的编号是接着主字段数的，OPPO 的不是）
        n = f["occurrence"]
        it.candidates = it.candidates[n:n + 1]
    opts = f.get("options") or []
    if (it.candidates and opts and all(o in YES_NO_OPTIONS for o in opts)
            and not any(c in opts for c in it.candidates) and raw not in (False, None, "")):
        it.candidates = ["有", "是"]  # 「语言能力：有 / 没有」这类，资料里有值就是「有」
    if attr in DATE_KEYS and raw:
        it.meta = {"date": str(raw)}
    if it.key in ("basic.hometown", "basic.current_city") and raw:
        alts = split_alternatives(str(raw))  # 「新加坡 / 苏州」：前一个选不到就用后一个
        if it.kind == "custom_select" and (f.get("_grouped") or f.get("_paired")):
            # 国家 / 省 / 市分成几个下拉（OPPO）：从最具体的一级往上试，备选依次排在后面
            it.candidates = [x for a in alts for x in split_region(a)[::-1]]
        elif it.kind == "custom_select":
            it.meta = {"paths": [split_region(a) for a in alts]}  # 一个地区选择器里逐级点（北森），逐个备选试
            it.candidates = [x for a in alts for x in split_region(a)]
        elif it.kind in ("text", "textarea"):
            it.candidates = [alts[0]]
    if not it.candidates:
        it.note = f"资料里没有「{KEY_CATALOG.get(it.key, it.key)}」"


def _squash(v: str) -> str:
    return re.sub(r"[\s\-/.年月日:：,，、]", "", v).lower()


def _same_value(page_value: str, candidates: list[str], key: str = "") -> bool:
    """页面上已有的值是否等于资料值。日期只比到页面给出的精度；手机号脱敏（188****6898）按通配比较。"""
    pv = _squash(page_value)
    if key.endswith(".period"):  # 起止时间：开始、结束都要一致，只对上一个不算
        want = [_squash(c) for c in candidates if c]
        return bool(want) and bool(pv) and all(w[:7] in pv for w in want)
    is_date = key.rpartition(".")[2] in DATE_KEYS
    for c in candidates:
        cv = _squash(c)
        if not cv or not pv:
            continue
        if is_date:
            n = min(len(pv), len(cv))
            if n >= 6 and pv[:n] == cv[:n]:  # 「2003-06」和「2003-06-24」算一致
                return True
            continue
        if "*" in pv and re.fullmatch(re.escape(pv).replace(r"\*", "."), cv):
            return True
        if cv == pv:
            return True
        if len(cv) >= 2 and cv in pv and not (cv in YES_NO_OPTIONS or pv in YES_NO_OPTIONS):
            return True  # 「硕士」在「硕士研究生」里；但「有」在「没有」里不算
        if key == "basic.english" and len(pv) >= 2 and cv.startswith(pv):
            return True  # 证书框只选「雅思」，分数另有一栏
    return False


def pick_resume_field(fields: list[dict]) -> dict | None:
    """「简历」上传框（不碰作品 / 其他附件 / 证明材料框）。"""
    for f in fields:
        text = f"{f['label']} {f['section']}"
        if f["kind"] == "file" and "简历" in text and not re.search(r"作品|其他附件|证明", text):
            return f
    return None


def pick_portfolio_field(fields: list[dict]) -> dict | None:
    """「作品集」上传框（不碰简历 / 其他附件 / 证明材料框）。"""
    for f in fields:
        text = f"{f['label']} {f['section']}"
        if f["kind"] == "file" and re.search(r"作品集?|portfolio", text, re.I) and "简历" not in text \
                and not re.search(r"其他附件|证明|论文", text):
            return f
    return None


def upload_portfolio(page: Page, fields: list[dict], path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        return f"作品集文件不存在：{path}"
    f = pick_portfolio_field(fields)
    if not f:
        return "没找到作品集上传框"
    page.set_input_files(f'[data-af-id="{f["id"]}"]', str(path))
    page.wait_for_timeout(1500)
    return f"已上传作品集：{path.name}"


def upload_resume(page: Page, fields: list[dict], path: str | Path) -> str:
    """把简历传进简历上传框。上传会触发站点的简历解析，所以要在填写之前做。"""
    path = Path(path)
    if not path.exists():
        return f"简历文件不存在：{path}"
    f = pick_resume_field(fields)
    if not f:
        return "没找到简历上传框"
    page.set_input_files(f'[data-af-id="{f["id"]}"]', str(path))
    page.wait_for_timeout(5000)  # 等站点解析简历
    return f"已上传简历：{path.name}"


def prepare(fields: list[dict], profile: Profile, llm: ChatClient | None = None, ctx: dict | None = None,
            overwrite: bool = False, resume_uploaded: bool = False, dry_run: bool = False) -> tuple[list[Item], str | None]:
    """决定每个字段填什么、要不要填（action="fill"）。不碰浏览器，插件和 Playwright 共用。"""
    items, llm_error = plan(fields, profile, llm)
    seen_keys: dict[str, int] = {}
    for it, f in zip(items, fields):
        if it.key in LIST_VALUE_KEYS:
            f["occurrence"] = seen_keys.get(it.key, 0)
            seen_keys[it.key] = f["occurrence"] + 1
    for it, f in zip(items, fields):
        if it.kind in ("file", "checkbox"):
            continue
        resolve(it, f, profile, ctx)
        if not it.candidates:
            continue
        if f["value"]:
            # 页面已有值（账号预填、站点解析简历）：一致就保留
            if _same_value(f["value"], it.candidates, it.key or ""):
                it.status, it.note = "kept", "页面已有值，与资料一致"
                continue
            # 「有 / 没有」单选里预先选好的「没有」是网站默认值，不是你填的：资料说「有」就改（OPPO 语言能力）
            site_default_no = (it.kind == "radio" and f["value"] in ("没有", "否", "无")
                               and it.candidates[0] in ("有", "是"))
            # 不一致：本次刚上传过简历（值来自站点解析）或显式要求覆盖时改成资料值，否则只标黄
            if not (overwrite or resume_uploaded or site_default_no):
                it.status, it.note = "uncertain", f"页面已有「{f['value'][:30]}」，资料是「{it.candidates[0][:30]}」，未覆盖"
                continue
            it.replacing = f["value"]
        if dry_run:
            it.status = "planned"
            it.note = it.note or (f"将把「{it.replacing[:20]}」改为「{it.candidates[0][:20]}」" if it.replacing
                                  else f"将填：{' | '.join(it.candidates[:3])}")
            continue
        it.action = "fill"
    return items, llm_error


def apply_result(it: Item, res: dict) -> None:
    """页面里 fill() 的返回值 → 字段状态与说明。"""
    if res.get("ok"):
        it.chosen = res.get("chosen")
        it.status = "filled" if it.source == "rule" else "uncertain"
        if it.source == "llm":
            it.note = f"模型判断为「{KEY_CATALOG.get(it.key, it.key)}」，请确认"
        elif it.replacing:
            it.note = f"已把页面上的「{it.replacing[:20]}」改为资料值"
        if res.get("note"):
            it.note = "；".join(x for x in (it.note, res["note"]) if x)
        if res.get("guessed"):  # 没有完全对得上的选项，选了最接近的：标黄让人确认
            it.status = "uncertain"
            if res.get("risk"):
                it.risk = res["risk"]
        if res.get("truncated"):
            it.status = "uncertain"
        if res.get("unverified"):
            it.status = "uncertain"
            it.note = "；".join(x for x in (it.note, f"点了「{it.chosen}」，但控件上没看到选中的值，请确认") if x)
    else:
        it.note = f"填写失败：{res.get('reason')}"
        if res.get("options"):
            it.note += f"（可选项：{'、'.join(res['options'][:8])}）"


def build_report(url: str, items: list[Item], profile: Profile, *, t0: float, llm_error: str | None = None,
                 dry_run: bool = False, ctx: dict | None = None, upload_note: str | None = None,
                 report_dir: str | Path | None = "runs", adapters: list[str] | None = None) -> dict:
    report = {
        "url": url,
        "adapters": adapters or [],  # 本页命中的招聘系统适配器（空 = 纯通用逻辑），排查问题先看这个
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 2),
        "llm_error": llm_error,
        "dry_run": dry_run,
        "ctx": ctx or {},
        "upload": upload_note,
        "summary": {s: sum(it.status == s for it in items)
                    for s in ("filled", "uncertain", "kept", "unfilled", "sensitive", "planned")},
        "items": [asdict(it) for it in items],
        "unused": unused_content(profile, {
            (it.key, it.profile_index) for it in items
            if it.key and it.status in ("filled", "kept", "uncertain", "planned")}),
    }
    if report_dir:
        d = Path(report_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
        report["file"] = str(out)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def page_job(page: Page, target: str, kind: str, candidates=None, meta=None, timeout_s: float = 45) -> dict:
    """在页面里执行一个任务（填字段 / 打开分区编辑 / 保存）。页面要点击时由这里用 page.mouse 做真实点击：
    Element Plus 等组件库不响应脚本模拟的点击。不能在 expose_function 回调里点（同步接口会死锁），所以用轮询。"""
    page.evaluate("(a) => window.__autofill.startJob(...a)", [target, kind, candidates or [], meta or {}, True])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = page.evaluate("() => window.__autofill.pollJob()")
        if st.get("click"):
            page.mouse.click(st["click"]["x"], st["click"]["y"])
            page.evaluate("() => window.__autofill.ackClick()")
            continue
        if st.get("key"):
            page.keyboard.press(st["key"])
            page.evaluate("() => window.__autofill.ackClick()")
            continue
        if st.get("text") is not None:
            page.keyboard.type(st["text"], delay=25)  # 真实键盘输入：远程搜索框只认这种
            page.evaluate("() => window.__autofill.ackClick()")
            continue
        if st.get("done"):
            return st.get("result") or {"ok": False, "reason": "页面没有返回结果"}
        page.wait_for_timeout(25)
    return {"ok": False, "reason": f"超过 {timeout_s:.0f} 秒没有完成"}


def fill_in_page(page: Page, it: Item) -> dict:
    return page_job(page, it.id, it.kind, it.candidates, it.meta)


def fill_with_ai_fallback(page: Page, it: Item, profile: Profile, llm: ChatClient | None) -> dict:
    """先按规则填；选项对不上（封闭列表）时，让 AI 从选项里挑最接近的一项再填一次。"""
    res = fill_in_page(page, it)
    if res.get("ok") or not llm or not res.get("options"):
        return res
    from .aipick import pick_option

    picked = pick_option(llm, it.label, it.key, profile.get(it.key or "", it.profile_index), res["options"])
    if not picked["choice"]:
        return res
    it.candidates = [picked["choice"]]
    again = fill_in_page(page, it)
    if not again.get("ok"):
        return res
    again.update(guessed=True, note=f"AI 从选项里选了「{picked['choice']}」，请确认")
    return again


def _wanted_section(text: str, profile: Profile) -> bool:
    """这个分区值得打开编辑吗：认得出是哪类分区，且资料里有对应内容。"""
    if re.search(SKIP_EXPAND, text or ""):
        return False
    sec = classify_section(text, None)
    return sec == "basic" or (sec in LIST_SECTIONS and bool(profile.section_items(sec)))


def run(page: Page, profile: Profile, llm: ChatClient | None = None, overwrite: bool = False,
        report_dir: str | Path | None = "runs", dry_run: bool = False, upload: bool = False,
        ctx: dict | None = None, save: bool = False) -> dict:
    """命令行 / 测试用的 Playwright 驱动。dry_run=True：只识别与规划，不填写、不点「添加」。

    分区编辑式表单（OPPO：分区默认只读，点铅笔才出现输入框）：逐个打开资料里有内容的分区 → 填 →
    save=True 时点这个分区的「保存」并核对是否保存成功 → 下一个分区。只点文字恰好是「保存」的按钮。"""
    t0 = time.time()
    upload_note = None
    if upload and not dry_run and profile.resume_file:
        upload_note = upload_resume(page, extract(page)["fields"], profile.resume_file)
    resume_uploaded = (upload_note or "").startswith("已上传")
    if upload and not dry_run and profile.portfolio_file:
        pnote = upload_portfolio(page, extract(page)["fields"], profile.portfolio_file)
        upload_note = "；".join(x for x in (upload_note, pnote) if x)
    merged: dict[str, Item] = {}
    llm_error = None
    sections: list[dict] = []
    opened: set[str] = set()
    for _ in range(12):
        if dry_run:
            data = extract(page)
            data["fields"] = annotate(data["fields"])
        else:
            data = expand_sections(page, profile)
        items, err = prepare(data["fields"], profile, llm, ctx, overwrite=overwrite,
                             resume_uploaded=resume_uploaded, dry_run=dry_run)
        llm_error = llm_error or err
        for it in items:
            if it.action == "fill":
                apply_result(it, fill_with_ai_fallback(page, it, profile, llm))
            old = merged.get(it.id)
            # 上一轮填上的字段这一轮会显示为「原值一致」，保留上一轮更有信息量的那条
            if not (old and old.status == "filled" and it.status == "kept"):
                merged[it.id] = it
        if dry_run:
            break
        # 刚才的填写可能让新字段冒出来（OPPO：「工作经历」选了「有」才展开表单），先把它们也填了再保存
        fresh = [f for f in annotate(extract(page)["fields"]) if f["id"] not in merged]
        if fresh:
            continue
        if save and page.evaluate("() => window.__autofill.saveButtons().length"):
            res = page_job(page, "", "__save")
            ok = res.get("sectionSaved", res.get("remaining", 1) == 0)
            rec = {"saved": ok, "errors": res.get("errors", []), "dialogs": res.get("dialogs", [])}
            if sections and sections[-1]["saved"] is None:
                sections[-1].update(rec)
            else:
                sections.append({"section": "页面", **rec})
            # 保存没成功（多半是资料里缺必填项）：这一分区保持编辑状态、内容不丢，记下原因，接着处理下一个分区
        expanders = page.evaluate("() => window.__autofill.findExpanders()")
        nxt = next((e for e in expanders
                    if e["section"] not in opened and _wanted_section(e["section"], profile)), None)
        if not nxt:
            break
        opened.add(nxt["section"])
        page_job(page, nxt["id"], "__expand")
        page.wait_for_timeout(1200)
        sections.append({"section": nxt["section"], "saved": None, "errors": []})
    items = list(merged.values())
    if not dry_run:
        for it in items:
            page.evaluate("([id, s, n, r]) => window.__autofill.mark(id, s, n, r)", [it.id, it.status, it.note, it.risk])
        page.evaluate("items => window.__autofill.panel(items)", [asdict(it) for it in items])
    report = build_report(page.url, items, profile, t0=t0, llm_error=llm_error, dry_run=dry_run, ctx=ctx,
                          upload_note=upload_note, report_dir=None,
                          adapters=page.evaluate("() => window.__autofill.adapters()"))
    report["sections"] = [x for x in sections if x["saved"] is not None or not save]
    if report_dir:
        d = Path(report_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
        report["file"] = str(out)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
