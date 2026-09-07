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
    sql: str = Field(..., description="SQL 文本，支持多语句")
    dialect: str = Field("mysql", description="数据库方言 key")
    parser: str = Field("auto", description="解析器: auto / sqllineage / sqlglot / ai")


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    sql = (req.sql or "").strip()
    if not sql:
        return {"ok": False, "error": "SQL 内容为空"}
    try:
        graph, parser_name, attempts = run_chain(sql, req.dialect, req.parser)
    except AllParsersFailed as e:
        return {"ok": False, "error": "所有解析器均未能解析该 SQL", "attempts": e.attempts}
    return {
        "ok": True,
        "parser": parser_name,
        "dialect": req.dialect,
        "graph": graph,
        "attempts": attempts,
    }


@app.get("/api/dialects")
def api_dialects():
    return {"dialects": DIALECTS}


@app.get("/api/parsers")
def api_parsers():
    return {"parsers": parser_info(), "ai_configured": load_ai_config()["enabled"]}


@app.get("/api/health")
def api_health():
    return {"ok": True}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
