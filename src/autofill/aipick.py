"""选项对不上时，让 AI 从站点给出的选项里挑意思最接近的一项。

隐私约定（比「AI 只看字段名」多发了一样东西，所以收得很紧）：
  · 只在确定性匹配（精确 / 包含 / 最接近）都失败、且选项是封闭列表时才问
  · 只发三样：题目文字、这一个字段对应的那一条资料值、选项列表；不发整份资料
  · 只对白名单里的「选项型」资料项问；姓名、手机、邮箱、证件号、出生日期等永不发送
  · AI 只能返回选项原文或 null；返回的不在选项里就当没选
  · 结果一律标黄（待确认），由人核对
"""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import ChatClient

# 这些资料项本来就是拿来在选项里选的（求职方向、专业、学历、城市…），可以发给模型
ALLOWED_KEYS = {
    "basic.job_intention", "basic.expected_city", "basic.current_city", "basic.hometown", "basic.available",
    "basic.english", "basic.political_status", "basic.ethnicity", "basic.languages", "basic.foreign_language",
    "basic.work_authorization", "basic.nationality", "basic.country",
    "education.degree", "education.major", "education.major_category", "education.study_type",
    "education.research_area", "work.title", "work.department", "work.kind", "project.role",
}
MAX_OPTIONS = 80
MAX_VALUE_CHARS = 200

SYSTEM_PROMPT = """你是网申表单的选项选择器。
给你一道题（题目文字）、用户对这一项的真实资料值，以及网站给出的选项列表。
从选项里选出与资料值意思最接近的一项；如果没有意思相近的，返回 null。
只能返回选项列表里的原文，禁止改写、禁止编造、禁止解释。
只输出一个 JSON 对象：{"choice": "选项原文或null"}。"""


def pick_option(client: ChatClient | None, label: str, key: str | None, value: Any, options: list[str]) -> dict:
    """返回 {"choice": 选项原文 | None, "reason": 说明}。任何异常、越权、对不上都返回 choice=None。"""
    if client is None:
        return {"choice": None, "reason": "没有可用的 AI 接口"}
    if key not in ALLOWED_KEYS:
        return {"choice": None, "reason": "这项资料不在允许发给 AI 的范围内"}
    text = str(value).strip() if value not in (None, "") else ""
    if not text:
        return {"choice": None, "reason": "资料里没有这一项，AI 没有依据，不猜"}
    opts = [o.strip() for o in options if o and o.strip()]
    if not opts or len(opts) > MAX_OPTIONS:
        return {"choice": None, "reason": "选项为空或太多（可能是远程搜索列表），不交给 AI"}
    payload = {"题目": (label or "")[:120], "资料值": text[:MAX_VALUE_CHARS], "选项": opts}
    try:
        reply = client.complete(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M).strip())
    except Exception as e:  # 网络、配置、坏 JSON：退回不猜
        return {"choice": None, "reason": f"AI 调用失败：{type(e).__name__}"}
    choice = data.get("choice") if isinstance(data, dict) else None
    if isinstance(choice, str) and choice.strip() in opts:
        return {"choice": choice.strip(), "reason": "AI 从选项里选的"}
    return {"choice": None, "reason": "AI 没有选出意思相近的一项"}
