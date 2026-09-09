"""DataX 解析器：解析 DataX 同步任务的 JSON 配置，提取 reader -> writer 血缘。

能力：
- 自动识别输入是否为 DataX JSON（job.content 结构）；普通 SQL 会快速失败并交给解析链下一个引擎
- 宽容解析：支持 JSON 中的 // 与 /* */ 注释、尾逗号、单引号字符串（DataX 线上配置常见写法）
- 表提取：connection[].table、collection / index 等插件参数、jdbcUrl 中提取库名、
  HDFS 路径提取 Hive 库表（/user/hive/warehouse/db.db/table）
- 字段级血缘：reader.column 与 writer.column 按位置对齐；reader.querySql 交给
  sqlglot 解析器的作用域分析，提取查询真实来源表与输出列映射
"""

from __future__ import annotations

import json
import re

import sqlglot

from ..dialects import get_dialect
from .base import BaseParser, ParseError, normalize_graph
from .sqlglot_parser import SqlglotParser, _Scope

# 不遵循 connection[].table 规范的插件会在 parameter 顶层用这些键
_TABLE_KEYS = ("table", "collection", "index", "tableName", "topic")
_JDBC_DB_RE = re.compile(r"jdbc:[a-z0-9]+://[^/]+/([^/?;#]+)", re.IGNORECASE)
_HIVE_PATH_RE = re.compile(r"/([^/]+?)\.db/([^/?*]+)")
_VAR_RE = re.compile(r"\$\{[^}]*}")


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _strip_comments_and_trailing_commas(text: str) -> str:
    """移除字符串字面量之外的注释与尾逗号，容忍单引号字符串。"""

    def skip_ws_comments(i: int) -> int:
        while i < len(text):
            if text[i] in " \t\r\n":
                i += 1
            elif text[i] == "/" and i + 1 < len(text) and text[i + 1] in "/*":
                if text[i + 1] == "/":
                    while i < len(text) and text[i] != "\n":
                        i += 1
                else:
                    i += 2
                    while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                        i += 1
                    i += 2
            else:
                break
        return i

    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
        elif c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            out.append('"' + text[i + 1:j].replace('"', '\\"') + '"')
            i = j + 1
        elif c == "/" and i + 1 < n and text[i + 1] in "/*":
            i = skip_ws_comments(i)
        elif c == ",":
            k = skip_ws_comments(i + 1)
            if k < n and text[k] in "}]":
                i += 1  # 尾逗号，丢弃
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _load_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return json.loads(_strip_comments_and_trailing_commas(text))
    except Exception as e:
        raise ParseError(f"JSON 解析失败: {e}") from e


def _db_from_jdbc(urls) -> str:
    for u in urls:
        m = _JDBC_DB_RE.search(str(u))
        if m and m.group(1).strip() not in ("", "*"):
            return m.group(1).strip()
    return ""


def _table_from_hdfs_path(path: str) -> str:
    m = _HIVE_PATH_RE.search(path)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    segs = [s for s in path.replace("\\", "/").split("/") if s and s not in ("*", "**")]
    if not segs:
        return ""
    return re.sub(r"[*?].*$", "", segs[-1]) or segs[-1]


def _clean_name(t) -> str:
    return str(t).strip().strip("`\"'").strip()


def _extract_columns(param: dict) -> list[str]:
    cols = []
    for c in _as_list(param.get("column")):
        name = _clean_name(c.get("name")) if isinstance(c, dict) else _clean_name(c)
        if name:
            cols.append(name)
    return cols


def _extract_side(side) -> dict:
    """提取 reader/writer 一侧的表、字段与 querySql。"""
    info = {"name": "", "db": "", "tables": [], "cols": [], "sqls": []}
    if not isinstance(side, dict):
        return info
    info["name"] = str(side.get("name") or "")
    param = side.get("parameter") if isinstance(side.get("parameter"), dict) else {}
    conns = [c for c in _as_list(param.get("connection")) if isinstance(c, dict)]
    for conn in conns:
        info["db"] = info["db"] or _db_from_jdbc(_as_list(conn.get("jdbcUrl")))
        for t in _as_list(conn.get("table")):
            t = _clean_name(t)
            if t and t not in info["tables"]:
                info["tables"].append(t)
        info["sqls"] += [str(q).strip() for q in _as_list(conn.get("querySql")) if str(q).strip()]
    if not info["sqls"]:  # 部分封装工具会把 querySql 提到 parameter 层
        info["sqls"] = [str(q).strip() for q in _as_list(param.get("querySql")) if str(q).strip()]
    if not info["db"]:
        info["db"] = _db_from_jdbc(_as_list(param.get("jdbcUrl")))
    if not info["tables"] and not info["sqls"]:
        for key in _TABLE_KEYS:
            vals = [t for t in (_clean_name(v) for v in _as_list(param.get(key))) if t]
            if vals:
                info["tables"] = vals
                break
    if not info["tables"] and not info["sqls"] and param.get("path"):
        t = _table_from_hdfs_path(str(param["path"]))
        if t:
            info["tables"].append(t)
    info["cols"] = _extract_columns(param)
    return info


def _qualify(t: str, db: str) -> str:
    t = _clean_name(t)
    if not t:
        return ""
    return f"{db}.{t}".lower() if db and "." not in t else t.lower()


def _analyze_query_sql(sql_text: str, dialect: str):
    """用 sqlglot 分析 querySql，返回 (输出列, 每列来源 {(表, 列)}, 物理表集合) 或 None。"""
    read = get_dialect(dialect)["sqlglot"]
    candidates = ([read] if read else []) + [None]
    stmts = None
    cleaned = _VAR_RE.sub("1", sql_text)  # ${bdp.system.bizdate} 等调度变量对血缘无意义
    for text in (cleaned, sql_text):
        for r in candidates:
            try:
                got = [s for s in sqlglot.parse(text, read=r) or [] if s is not None]
            except Exception:
                continue
            if got:
                stmts = got
                break
        if stmts:
            break
    if not stmts:
        return None

    p = SqlglotParser()  # 独立实例，避免污染解析链中的状态
    # _analyze_query 内部会经 _add_table 写入这些实例属性，parse() 之外需自行初始化
    p._tables, p._table_edges, p._col_edges, p._warnings = {}, [], [], []
    out_cols: list[str] = []
    col_sources: list[set] = []
    tables: set = set()
    try:
        for st in stmts:
            rel = p._analyze_query(st, _Scope())
            tables |= p._relation_sources(rel)
            for name, sources in rel.columns.items():
                out_cols.append(name)
                col_sources.append(set(sources))
    except Exception:
        return None
    if not out_cols:
        return None
    return out_cols, col_sources, tables


class DataxParser(BaseParser):
    name = "datax"
    label = "DataX"

    def parse(self, sql: str, dialect: str, options: dict | None = None) -> dict:
        text = (sql or "").strip().lstrip("\ufeff")
        if not text.startswith("{"):
            raise ParseError("输入不是 JSON，不是 DataX 配置")
        cfg = _load_json(text)
        job = cfg.get("job") if isinstance(cfg, dict) else None
        contents = job.get("content") if isinstance(job, dict) else None
        if not isinstance(contents, list):
            raise ParseError("JSON 中缺少 job.content 结构，不是 DataX 配置")

        warnings: list[str] = []
        all_tables: list[tuple[str, str]] = []
        table_edges: list[dict] = []
        col_edges: list[dict] = []

        for idx, content in enumerate(contents, 1):
            if not isinstance(content, dict):
                warnings.append(f"第 {idx} 组 content 结构异常，已跳过")
                continue
            reader = _extract_side(content.get("reader"))
            writer = _extract_side(content.get("writer"))

            src_tables: list[str] = []
            col_sources: list[set] | None = None
            src_cols: list[str] | None = None
            if reader["sqls"]:
                result = None
                for q in reader["sqls"]:
                    result = _analyze_query_sql(q, dialect)
                    if result:
                        break
                if result:
                    src_cols, col_sources, tabs = result
                    src_tables = sorted(tabs)
                else:
                    warnings.append(f"第 {idx} 组 querySql 解析失败，无法确定来源表与字段")
            if not src_tables:
                src_tables = [_qualify(t, reader["db"]) for t in reader["tables"]]
                src_tables = [t for t in src_tables if t]
                if not col_sources and reader["cols"]:
                    src_cols = reader["cols"]
            src_tables = [t for t in src_tables if t != "*"]

            tgt_tables = [_qualify(t, writer["db"]) for t in writer["tables"]]
            tgt_tables = [t for t in tgt_tables if t and t != "*"]
            tgt_cols = writer["cols"] or None

            if not src_tables and not tgt_tables:
                warnings.append(f"第 {idx} 组未识别到任何表（reader={reader['name'] or '?'}, writer={writer['name'] or '?'}）")
                continue
            if not src_tables:
                warnings.append(f"第 {idx} 组 reader 未识别到来源表，仅登记目标表")
            if not tgt_tables:
                warnings.append(f"第 {idx} 组 writer 未识别到目标表，仅登记来源表")

            all_tables += [(t, "source") for t in src_tables] + [(t, "target") for t in tgt_tables]
            table_edges += [{"source": s, "target": t} for s in src_tables for t in tgt_tables]

            if not src_tables or not tgt_tables:
                continue
            if src_cols and tgt_cols and "*" not in src_cols and "*" not in tgt_cols:
                n = min(len(src_cols), len(tgt_cols))
                if len(src_cols) != len(tgt_cols):
                    warnings.append(
                        f"第 {idx} 组 reader/writer 字段数不一致（{len(src_cols)} vs {len(tgt_cols)}），仅对齐前 {n} 个"
                    )
                for i in range(n):
                    tc = tgt_cols[i]
                    sources = col_sources[i] if col_sources and i < len(col_sources) and col_sources[i] \
                        else {(s, src_cols[i]) for s in src_tables}
                    for st, sc in sources:
                        for tt in tgt_tables:
                            col_edges.append({
                                "source_table": st, "source_column": sc,
                                "target_table": tt, "target_column": tc,
                            })
            elif "*" in (src_cols or []) and "*" in (tgt_cols or []):
                for s in src_tables:
                    for t in tgt_tables:
                        col_edges.append({
                            "source_table": s, "source_column": "*",
                            "target_table": t, "target_column": "*",
                        })
            else:
                warnings.append(f"第 {idx} 组无法确定字段级映射（字段配置缺失或为 *），仅生成表级血缘")

        if not all_tables:
            raise ParseError("未能从 DataX 配置中提取到任何表（请检查 reader/writer 配置）")
        return normalize_graph(all_tables, table_edges, col_edges, warnings)
