"""运行配置：AI 兜底解析器的参数。

优先级：环境变量 > backend/ai_config.json > 默认值。
ai_config.json 示例：
{
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "你的APIKey",
    "model": "glm-4-flash",
    "timeout": 60
}
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "ai_config.json"


def load_ai_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}
    api_key = os.getenv("AI_API_KEY", "") or str(cfg.get("api_key", "") or "")
    return {
        "enabled": bool(api_key),
        "base_url": os.getenv("AI_API_BASE", "") or str(cfg.get("base_url", "") or "https://open.bigmodel.cn/api/paas/v4"),
        "api_key": api_key,
        "model": os.getenv("AI_MODEL", "") or str(cfg.get("model", "") or "glm-4-flash"),
        "timeout": int(os.getenv("AI_TIMEOUT", "") or cfg.get("timeout", 60) or 60),
    }
