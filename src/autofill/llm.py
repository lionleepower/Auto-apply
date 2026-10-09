"""LLM 兜底映射：只把「字段标签 / 分区 / 选项」和资料键目录发给模型，不发资料值。

模型只负责回答「这个字段对应哪个资料键」，值由本地代码取。
三类异常（坏 JSON、键不在目录里、没回答）一律收敛为 None，不猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from .rules import IGNORE, KEY_CATALOG
from .settings import PRESETS, load_providers, usable

# 旧版 CLI 的 --llm 取值仍然可用；新的配置（多家、故障转移）在 settings.py
PROVIDERS = {
    pid: (p["base_url"], p["env"], p["model"]) for pid, p in PRESETS.items() if p.get("env")
}

SYSTEM_PROMPT = """你是招聘网申表单的字段分类器。
给你一组表单字段（标签、所在分区、控件类型、可选项），以及一份「资料键目录」。
为每个字段选出它对应的资料键；没有合适的键、或字段是开放式问答（如「为什么选择我们」）、
紧急联系人、验证码、协议勾选、附件上传时，返回 null。
只能从目录里选键，禁止编造新键。
只输出一个 JSON 对象：{"字段id": "资料键或null", ...}，不要输出任何其他内容。"""


class ChatClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class LLMError(RuntimeError):
    pass


def _http_error(resp) -> str:
    try:
        detail = resp.json()
        detail = detail.get("error", detail) if isinstance(detail, dict) else detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
    except ValueError:
        detail = resp.text[:200]
    return f"HTTP {resp.status_code}：{str(detail)[:200]}"


@dataclass
class ProviderClient:
    """一家厂商。cfg 形如 settings.load_providers() 里的一项；三种协议都只用 httpx，不依赖各家 SDK。"""

    cfg: dict
    timeout: float = 60

    @property
    def label(self) -> str:
        return PRESETS.get(self.cfg.get("preset", ""), {}).get("name", self.cfg.get("preset", "?"))

    def complete(self, system: str, user: str, json_mode: bool = True) -> str:
        import httpx

        c = self.cfg
        base = c["base_url"].rstrip("/")
        key = c.get("api_key") or ""
        proto = c.get("protocol", "openai")
        try:
            if proto == "anthropic":
                resp = httpx.post(f"{base}/v1/messages", timeout=self.timeout,
                                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                  json={"model": c["model"], "max_tokens": 4096, "system": system, "temperature": 0,
                                        "messages": [{"role": "user", "content": user}]})
                if resp.status_code >= 400:
                    raise LLMError(_http_error(resp))
                return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
            if proto == "gemini":
                body = {"systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": {"temperature": 0,
                                             **({"responseMimeType": "application/json"} if json_mode else {})}}
                resp = httpx.post(f"{base}/models/{c['model']}:generateContent", timeout=self.timeout,
                                  headers={"x-goog-api-key": key}, json=body)
                if resp.status_code >= 400:
                    raise LLMError(_http_error(resp))
                parts = resp.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            body = {"model": c["model"], "temperature": 0,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            resp = httpx.post(f"{base}/chat/completions", timeout=self.timeout, headers=headers, json=body)
            if resp.status_code >= 400 and json_mode and resp.status_code in (400, 422):
                body.pop("response_format")  # 个别兼容接口不认 response_format：去掉再试一次
                resp = httpx.post(f"{base}/chat/completions", timeout=self.timeout, headers=headers, json=body)
            if resp.status_code >= 400:
                raise LLMError(_http_error(resp))
            return resp.json()["choices"][0]["message"]["content"] or ""
        except httpx.HTTPError as e:
            raise LLMError(f"连不上 {base}：{e.__class__.__name__} {e}") from e
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"返回格式不认识：{e.__class__.__name__}") from e


@dataclass
class FailoverClient:
    """按顺序试每一家，前一家报错（限流、欠费、断网）就换下一家。last_used / errors 供界面展示。"""

    clients: list[ProviderClient]
    last_used: str | None = None
    errors: list[str] = field(default_factory=list)

    def complete(self, system: str, user: str, json_mode: bool = True) -> str:
        self.errors = []
        for c in self.clients:
            try:
                out = c.complete(system, user, json_mode)
                self.last_used = c.label
                return out
            except LLMError as e:
                self.errors.append(f"{c.label}：{e}")
        raise LLMError("；".join(self.errors) or "没有可用的 AI 接口")


def OpenAICompatClient(provider: str, model: str | None = None):  # noqa: N802  旧入口，仅 CLI 的 --llm 用
    client = build_client(provider)
    if client is None:
        raise RuntimeError(f"没有可用的 AI 接口：{provider}（在界面里配置，或把 key 写进 .env）")
    if model and isinstance(client, FailoverClient):
        client.clients[0].cfg["model"] = model
    return client


def build_client(name: str = "auto") -> FailoverClient | None:
    """name: none → None；auto → 所有可用厂商按顺序；某个厂商 id / 预设名 → 只用它。"""
    if name in ("", "none", None):
        return None
    ok = [p for p in load_providers() if usable(p)]
    if name != "auto":
        ok = [p for p in ok if name in (p.get("id"), p.get("preset"))]
    return FailoverClient([ProviderClient(p) for p in ok]) if ok else None


def check_provider(cfg: dict) -> dict:
    """测试连接：发一句极短的话，返回耗时或错误。cfg 可以是还没保存的。"""
    import time

    t0 = time.time()
    try:
        out = ProviderClient(cfg, timeout=30).complete("只回复 ok", "ping", json_mode=False)
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": out.strip()[:40]}


def build_user_prompt(fields: list[dict]) -> str:
    payload = {
        "资料键目录": KEY_CATALOG,
        "字段": [
            {k: f[k] for k in ("id", "label", "section", "kind", "placeholder", "options") if f.get(k)}
            for f in fields
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_mapping(text: str, field_ids: set[str]) -> dict[str, str | None]:
    """解析模型输出；任何不合法的部分都当作 None。"""
    try:
        data = json.loads(text.strip().removeprefix("```json").removesuffix("```"))
    except (json.JSONDecodeError, AttributeError):
        return {fid: None for fid in field_ids}
    if not isinstance(data, dict):
        return {fid: None for fid in field_ids}
    out: dict[str, str | None] = {}
    for fid in field_ids:
        key = data.get(fid)
        out[fid] = key if isinstance(key, str) and (key in KEY_CATALOG or key == IGNORE) else None
    return out


def llm_map(client: ChatClient, fields: list[dict]) -> dict[str, str | None]:
    if not fields:
        return {}
    text = client.complete(SYSTEM_PROMPT, build_user_prompt(fields))
    return parse_mapping(text, {f["id"] for f in fields})
