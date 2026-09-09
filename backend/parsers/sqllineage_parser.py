"""sqllineage 解析器（首选）：表级血缘精确，字段级血缘由 get_column_lineage 提供。

sqllineage 的字段级血缘链中会混入 CTE 的中间列（如 active.id），
这里借助 sqlglot 提取 CTE 名称，把链路上的 CTE 节点折叠掉。
"""

from __future__ import annotations

from sqllineage.runner import LineageRunner

from ..dialects import get_dialect
from .base import BaseParser, ParseError, normalize_graph


def _collect_cte_names(sql: str, dialect: str) -> set[str]:
    """用 sqlglot 提取所有 CTE 别名，失败则忽略（仅影响链路折叠精度）。"""
    try:
        import sqlglot

        read = get_dialect(dialect)["sqlglot"]
        stmts = sqlglot.parse(sql, read=read) if read else sqlglot.parse(sql)
        names = set()
        for s in stmts or []:
            if s is None:
                continue
            for cte in s.find_all(sqlglot.exp.CTE):
                names.add(cte.alias_or_name.lower())
        return names
    except Exception:
        return set()


class SqlLineageParser(BaseParser):
    name = "sqllineage"
    label = "sqllineage"

    def parse(self, sql: str, dialect: str, options: dict | None = None) -> dict:
        d = get_dialect(dialect)
        warnings: list[str] = []
        runner = None
        try:
            runner = LineageRunner(sql, dialect=d["sqllineage"])
        except Exception:
            # 方言不支持或解析失败 -> 回退 ANSI 再试一次
            try:
                runner = LineageRunner(sql, dialect="ansi")
                warnings.append(f"sqllineage 不支持方言 {d['label']}，已按 ANSI 解析")
            except Exception as e:
                raise ParseError(f"sqllineage 解析失败: {e}") from e

        if not runner.source_tables and not runner.target_tables and not runner.intermediate_tables:
            raise ParseError("sqllineage 未能识别出任何表（SQL 可能为空或语法不完整）")

        tables = []
        for t in runner.intermediate_tables:
            tables.append((str(t), "intermediate"))
        for t in runner.source_tables:
            tables.append((str(t), "source"))
        for t in runner.target_tables:
            tables.append((str(t), "target"))

        # 逐语句重跑以获得精确的 表->表 边（比 source×target 笛卡尔积更准确）
        table_edges = []
        for stmt in runner.statements():
            r = LineageRunner(stmt, dialect=d["sqllineage"]) if d["sqllineage"] else LineageRunner(stmt)
            srcs = [str(t) for t in r.source_tables]
            tgts = [str(t) for t in r.target_tables]
            if srcs and tgts:
                table_edges.extend({"source": s, "target": t} for s in srcs for t in tgts)

        # 字段级血缘链：折叠 CTE 中间列后取相邻对
        cte_names = _collect_cte_names(sql, dialect)
        column_edges = []
        for chain in runner.get_column_lineage():
            real = [c for c in chain if str(c.parent).lower() not in cte_names]
            for a, b in zip(real, real[1:]):
                pa, pb = str(a.parent), str(b.parent)
                if pa.lower() in cte_names or pb.lower() in cte_names:
                    continue
                column_edges.append({
                    "source_table": pa, "source_column": a.raw_name,
                    "target_table": pb, "target_column": b.raw_name,
                })

        if tables and not column_edges:
            warnings.append("sqllineage 未解析到字段级血缘（SQL 可能不含列级写入关系）")

        return normalize_graph(tables, table_edges, column_edges, warnings)
