"""解析器注册中心与解析链。

新增解析器：在 parsers/ 下新建文件，继承 BaseParser 并用 @register 装饰，
然后加到 CHAIN_ORDER 中即可被"自动"模式纳入兜底顺序。
"""

from __future__ import annotations

from .base import BaseParser, ParseError
from .datax_parser import DataxParser
from .druid_parser import DruidParser
from .sqllineage_parser import SqlLineageParser
from .sqlglot_parser import SqlglotParser
from .ai_parser import AiParser

PARSERS: dict[str, BaseParser] = {}


def register(cls):
    PARSERS[cls.name] = cls()
    return cls


# 自动模式的兜底顺序：DataX(JSON) -> sqllineage -> sqlglot -> Druid -> AI
# datax 排第一：对 SQL 输入仅做一次前缀检查即失败，几乎零开销；
# 对 JSON 输入则无需再让 SQL 引擎浪费时间报错
CHAIN_ORDER = ["datax", "sqllineage", "sqlglot", "druid", "ai"]


def parser_info() -> list[dict]:
    return [
        {"name": p.name, "label": p.label, "available": p.is_available()}
        for p in (PARSERS[n] for n in CHAIN_ORDER)
    ]


def run_chain(sql: str, dialect: str, preference: str = "auto", options: dict | None = None) -> tuple[dict, str, list[dict]]:
    """运行解析链，返回 (graph, 使用的解析器名, 尝试记录)。

    preference 为 'auto' 时按 CHAIN_ORDER 逐个尝试；指定具体解析器时只尝试该解析器。
    options 传递会话级配置（如 {"ai": {...}}），仅当次请求有效。
    全部失败抛 AllParsersFailed。
    """
    attempts: list[dict] = []

    if preference and preference != "auto":
        p = PARSERS.get(preference)
        if p is None:
            raise AllParsersFailed([{"parser": preference, "ok": False, "message": "解析器不存在"}])
        graph, msg = p.parse_safe(sql, dialect, options)
        attempts.append({"parser": p.name, "ok": graph is not None, "message": msg})
        if graph is None:
            raise AllParsersFailed(attempts)
        return graph, p.name, attempts

    for name in CHAIN_ORDER:
        p = PARSERS[name]
        graph, msg = p.parse_safe(sql, dialect, options)
        attempts.append({"parser": p.name, "ok": graph is not None, "message": msg})
        if graph is not None:
            return graph, p.name, attempts
    raise AllParsersFailed(attempts)


class AllParsersFailed(Exception):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        detail = "; ".join(f"{a['parser']}: {a['message']}" for a in attempts if a.get("message"))
        super().__init__(f"所有解析器均失败。{detail}")


register(DataxParser)
register(SqlLineageParser)
register(SqlglotParser)
register(DruidParser)
register(AiParser)
