"""AI 接口配置：多家厂商，按顺序故障转移。存在本地 settings.json（含 key，已加入 .gitignore）。

三种协议：openai（OpenAI 兼容，绝大多数国内外厂商）、anthropic（Claude 原生）、gemini（Gemini 原生）。
旧版 .env 里的 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY 仍然有效：settings.json 里没有对应厂商时自动补上。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# id: (显示名, 协议, base_url, 默认模型, 备选模型, 是否需要 key, 申请 key 的地址)
PRESETS: dict[str, dict] = {
    "deepseek": {"name": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com",
                 "model": "deepseek-chat", "models": ["deepseek-chat", "deepseek-reasoner"],
                 "env": "DEEPSEEK_API_KEY", "key_url": "https://platform.deepseek.com/api_keys"},
    "qwen": {"name": "通义千问", "protocol": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "model": "qwen-plus", "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
             "env": "DASHSCOPE_API_KEY", "key_url": "https://bailian.console.aliyun.com/?apiKey=1"},
    "kimi": {"name": "Kimi (Moonshot)", "protocol": "openai", "base_url": "https://api.moonshot.cn/v1",
             "model": "moonshot-v1-32k", "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
             "key_url": "https://platform.moonshot.cn/console/api-keys"},
    "zhipu": {"name": "智谱 GLM", "protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "model": "glm-4-flash", "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"],
              "key_url": "https://open.bigmodel.cn/usercenter/apikeys"},
    "doubao": {"name": "豆包 (火山方舟)", "protocol": "openai", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
               "model": "", "models": [], "key_url": "https://console.volcengine.com/ark",
               "hint": "模型一栏填你在方舟创建的接入点 ID（ep-xxxx）"},
    "openai": {"name": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4o-mini", "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
               "key_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"name": "Claude (Anthropic)", "protocol": "anthropic", "base_url": "https://api.anthropic.com",
                  "model": "claude-haiku-4-5", "models": ["claude-haiku-4-5", "claude-sonnet-4-5"],
                  "key_url": "https://console.anthropic.com/settings/keys"},
    "gemini": {"name": "Google Gemini", "protocol": "gemini",
               "base_url": "https://generativelanguage.googleapis.com/v1beta",
               "model": "gemini-2.0-flash", "models": ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
               "key_url": "https://aistudio.google.com/apikey"},
    "openrouter": {"name": "OpenRouter", "protocol": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "model": "deepseek/deepseek-chat", "models": ["deepseek/deepseek-chat", "openai/gpt-4o-mini"],
                   "key_url": "https://openrouter.ai/keys"},
    "ollama": {"name": "Ollama (本地)", "protocol": "openai", "base_url": "http://localhost:11434/v1",
               "model": "qwen2.5:7b", "models": [], "no_key": True, "key_url": "https://ollama.com/download"},
    "custom": {"name": "自定义 (OpenAI 兼容)", "protocol": "openai", "base_url": "", "model": "", "models": []},
}
PROTOCOLS = {"openai": "OpenAI 兼容", "anthropic": "Claude 原生", "gemini": "Gemini 原生"}


def settings_path() -> Path:
    return Path(os.environ.get("AUTOFILL_SETTINGS") or ROOT / "settings.json")


def _read() -> dict:
    p = settings_path()
    if not p.exists():
        return {"providers": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"providers": []}
    return data if isinstance(data, dict) and isinstance(data.get("providers"), list) else {"providers": []}


def needs_key(p: dict) -> bool:
    return not PRESETS.get(p.get("preset", ""), {}).get("no_key", False)


def usable(p: dict) -> bool:
    """启用、有地址和模型，且（需要的话）有 key。"""
    return bool(p.get("enabled", True) and p.get("base_url") and p.get("model") and (p.get("api_key") or not needs_key(p)))


def load_providers() -> list[dict]:
    """settings.json 里的厂商，加上 .env 里有 key 但 settings 里还没配的旧版厂商（排在后面）。"""
    providers = [dict(p) for p in _read()["providers"] if isinstance(p, dict)]
    have = {p.get("preset") for p in providers}
    for pid, preset in PRESETS.items():
        env = preset.get("env")
        if env and os.environ.get(env) and pid not in have:
            providers.append({"id": pid, "preset": pid, "protocol": preset["protocol"], "base_url": preset["base_url"],
                              "model": preset["model"], "api_key": os.environ[env], "enabled": True, "from_env": True})
    return providers


def save_providers(providers: list[dict]) -> None:
    clean = []
    for p in providers:
        clean.append({k: p.get(k) for k in ("id", "preset", "protocol", "base_url", "model", "api_key", "enabled")
                      if p.get(k) is not None})
    settings_path().write_text(json.dumps({"providers": clean}, ensure_ascii=False, indent=2), encoding="utf-8")


def new_id(preset: str) -> str:
    return f"{preset}-{uuid.uuid4().hex[:6]}"


def mask(key: str | None) -> str:
    if not key:
        return ""
    return key[:3] + "…" + key[-4:] if len(key) > 10 else "…"


def public_view(p: dict) -> dict:
    """给界面看的版本：key 打码，不回传明文。"""
    out = {k: p.get(k) for k in ("id", "preset", "protocol", "base_url", "model", "enabled", "from_env")}
    out["key_mask"] = mask(p.get("api_key"))
    out["usable"] = usable(p)
    out["label"] = PRESETS.get(p.get("preset", ""), {}).get("name", p.get("preset", ""))
    return out
