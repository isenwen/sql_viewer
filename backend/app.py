"""SQL 血缘查看器后端服务。

启动: python run.py  或  uvicorn backend.app:app --port 8866
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import load_ai_config
from .dialects import DIALECTS
from .parsers import AllParsersFailed, parser_info, run_chain

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="SQL 血缘查看器", version="1.0.0")


class ParseRequest(BaseModel):
    sql: str = Field(..., description="SQL 或 DataX JSON 文本，支持多语句 / 多 content 组")
    dialect: str = Field("mysql", description="数据库方言 key")
    parser: str = Field("auto", description="解析器: auto / datax / sqllineage / sqlglot / druid / ai")
    options: dict = Field(default_factory=dict, description="会话级配置（如 ai 连接参数），仅当次请求有效，服务端不落盘")


class AiTestRequest(BaseModel):
    base_url: str = Field("", description="OpenAI 兼容接口地址")
    api_key: str = Field(..., description="API Key")
    model: str = Field("", description="模型名")
    timeout: int = Field(60, ge=5, le=120)


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    sql = (req.sql or "").strip()
    if not sql:
        return {"ok": False, "error": "SQL 内容为空"}
    try:
        graph, parser_name, attempts = run_chain(sql, req.dialect, req.parser, req.options)
    except AllParsersFailed as e:
        return {"ok": False, "error": "所有解析器均未能解析该 SQL", "attempts": e.attempts}
    return {
        "ok": True,
        "parser": parser_name,
        "dialect": req.dialect,
        "graph": graph,
        "attempts": attempts,
    }


@app.post("/api/ai/test")
def api_ai_test(req: AiTestRequest):
    """测试网页端填写的 AI 配置连通性（配置只随请求传递，不落盘）。"""
    import requests

    url = (req.base_url or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": req.model or "glm-4-flash",
        "messages": [{"role": "user", "content": "回复两个字：成功"}],
        "max_tokens": 8,
    }
    try:
        resp = requests.post(url, json=payload,
                             headers={"Authorization": f"Bearer {req.api_key}"},
                             timeout=min(max(req.timeout, 5), 120))
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return {"ok": True, "message": f"连接成功，模型返回: {content[:50]}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}


@app.get("/api/dialects")
def api_dialects():
    return {"dialects": DIALECTS}


@app.get("/api/parsers")
def api_parsers():
    return {
        "parsers": parser_info(),
        "ai_configured": load_ai_config()["enabled"],
    }


@app.get("/api/health")
def api_health():
    return {"ok": True}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
