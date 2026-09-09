"""AI 兜底解析器：调用 OpenAI 兼容接口，让大模型输出血缘 JSON。

未配置 API Key 时视为"未配置"失败，由解析链跳过。
"""

from __future__ import annotations

import json
import re

import requests

from ..config import load_ai_config
from .base import BaseParser, ParseError, normalize_graph

VALID_ROLES = {"source", "target", "intermediate"}


class AiParser(BaseParser):
    name = "ai"
    label = "AI 兜底"

    @staticmethod
    def session_config(options: dict | None) -> dict | None:
        """从会话级 options 提取有效 AI 配置（网页配置，仅当次请求有效）。"""
        ai = (options or {}).get("ai") or {}
        api_key = str(ai.get("api_key", "") or "").strip()
        if not api_key:
            return None
        try:
            timeout = int(ai.get("timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60
        return {
            "enabled": True,
            "base_url": str(ai.get("base_url") or "").strip().rstrip("/") or "https://open.bigmodel.cn/api/paas/v4",
            "api_key": api_key,
            "model": str(ai.get("model") or "").strip() or "glm-4-flash",
            "timeout": max(5, min(timeout, 300)),
        }

    def parse(self, sql: str, dialect: str, options: dict | None = None) -> dict:
        cfg = self.session_config(options) or load_ai_config()
        if not cfg["enabled"]:
            raise ParseError(
                "AI 解析器未配置：请在页面右上角「AI 配置」中填写（仅当前浏览器会话有效），"
                "或设置环境变量 AI_API_KEY/AI_API_BASE/AI_MODEL，或编辑 backend/ai_config.json"
            )

        payload = {
            "model": cfg["model"],
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "你是一个资深的SQL血缘分析引擎，只输出严格JSON，不要输出任何解释或markdown代码块。"},
                {"role": "user", "content": self._prompt(sql, dialect)},
            ],
        }
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                timeout=cfg["timeout"],
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise ParseError(f"AI 接口调用失败: {e}") from e

        data = self._extract_json(content)
        if not data:
            raise ParseError("AI 返回内容无法解析为 JSON")

        tables = []
        for t in data.get("tables", []) or []:
            name = str(t.get("name", "")).strip()
            if not name:
                continue
            role = str(t.get("role", "source")).strip().lower()
            tables.append({"id": name, "role": role if role in VALID_ROLES else "source",
                           "columns": [str(c) for c in (t.get("columns") or [])]})
        table_edges = [{"source": str(e.get("source", "")).strip(), "target": str(e.get("target", "")).strip()}
                       for e in (data.get("table_edges") or [])]
        column_edges = [{"source_table": str(e.get("source_table", "")).strip(),
                         "source_column": str(e.get("source_column", "")).strip(),
                         "target_table": str(e.get("target_table", "")).strip(),
                         "target_column": str(e.get("target_column", "")).strip()}
                        for e in (data.get("column_edges") or [])]

        if not tables:
            raise ParseError("AI 未能识别出任何表")
        return normalize_graph(tables, table_edges, column_edges,
                               ["血缘由 AI 推断生成，仅供参考，请人工复核"])

    def _prompt(self, sql: str, dialect: str) -> str:
        sql = sql if len(sql) <= 8000 else sql[:8000] + "\n...(过长已截断)"
        return f"""请分析以下 {dialect} SQL 的数据血缘，输出严格 JSON，格式如下：
{{
  "tables": [{{"name": "库.表名", "role": "source|target|intermediate", "columns": ["列名"]}}],
  "table_edges": [{{"source": "源表全名", "target": "目标表全名"}}],
  "column_edges": [{{"source_table": "源表全名", "source_column": "源列名", "target_table": "目标表全名", "target_column": "目标列名"}}]
}}
要求：
1. tables 列出 SQL 中出现的所有物理表及其在 SQL 中涉及的列（columns）；CTE/临时别名不是表。
2. role：仅被读取为 source；被写入为 target；既被写入又被读取为 intermediate。
3. column_edges 描述"源表.源列 -> 目标表.目标列"；表达式输出列请关联其全部输入列；SELECT * 无法确定列名时源列用 * 表示。
4. 只输出 JSON 本身。

SQL:
{sql}"""

    def _extract_json(self, text: str) -> dict | None:
        text = text.strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        # 兜底：截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return None
        return None
