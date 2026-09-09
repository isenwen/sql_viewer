"""解析器公共基类与统一血缘图结构的归一化。"""

from __future__ import annotations

MAX_COLUMNS_PER_TABLE = 80


class ParseError(Exception):
    """单个解析器失败时抛出，解析链会尝试下一个解析器。"""


class BaseParser:
    name: str = "base"
    label: str = "基础解析器"

    @classmethod
    def is_available(cls) -> bool:
        """解析引擎当前是否可用（用于前端展示就绪状态）。"""
        return True

    def parse(self, sql: str, dialect: str, options: dict | None = None) -> dict:
        """返回统一结构 {"tables", "table_edges", "column_edges", "warnings"}，失败抛 ParseError。

        options: 会话级配置（如 {"ai": {...}}），仅当次解析有效，服务端不持久化。
        """
        raise NotImplementedError

    def parse_safe(self, sql: str, dialect: str, options: dict | None = None):
        """永不抛异常：返回 (graph | None, message)。"""
        try:
            graph = self.parse(sql, dialect, options)
            if not graph.get("tables"):
                raise ParseError("未能从 SQL 中提取到任何表")
            return graph, ""
        except ParseError as e:
            return None, str(e)
        except Exception as e:  # 任何第三方库异常都视为该解析器失败
            return None, f"{type(e).__name__}: {e}"


def normalize_graph(tables, table_edges, column_edges, warnings=None) -> dict:
    """归一化各解析器的输出。

    tables: iterable of (id, role) 或 dict(id, role, columns)
    table_edges / column_edges: iterable of dict
    """
    warnings = list(warnings or [])
    table_map: dict[str, dict] = {}

    def ensure(tid: str, role: str | None = None) -> dict:
        tid = str(tid).strip()
        if not tid:
            return None
        if tid not in table_map:
            table_map[tid] = {"id": tid, "role": role or "source", "columns": []}
        elif role:
            old = table_map[tid]["role"]
            if old != role:
                # 同时作为来源与目标 -> intermediate
                table_map[tid]["role"] = "intermediate" if {old, role} >= {"source", "target"} else ("target" if role == "target" else old)
        return table_map[tid]

    for t in tables or []:
        if isinstance(t, dict):
            ensure(t.get("id"), t.get("role") or "source")
            for c in t.get("columns") or []:
                col = str(c).strip()
                if col and col not in table_map[t["id"]]["columns"]:
                    table_map[t["id"]]["columns"].append(col)
        else:
            ensure(t[0], t[1] or "source")

    edge_set = set()
    clean_table_edges = []
    for e in table_edges or []:
        s, t = str(e.get("source", "")).strip(), str(e.get("target", "")).strip()
        if not s or not t:
            continue
        if s == t:
            continue  # 自环在图中渲染意义不大，直接丢弃
        if (s, t) in edge_set:
            continue
        edge_set.add((s, t))
        ensure(s, "source")
        ensure(t, "target")
        clean_table_edges.append({"source": s, "target": t})

    col_edge_set = set()
    clean_col_edges = []
    for e in column_edges or []:
        st, sc = str(e.get("source_table", "")).strip(), str(e.get("source_column", "")).strip()
        tt, tc = str(e.get("target_table", "")).strip(), str(e.get("target_column", "")).strip()
        if not (st and sc and tt and tc):
            continue
        key = (st.lower(), sc.lower(), tt.lower(), tc.lower())
        if key in col_edge_set:
            continue
        col_edge_set.add(key)
        ensure(st, "source")
        ensure(tt, "target")
        clean_col_edges.append({"source_table": st, "source_column": sc, "target_table": tt, "target_column": tc})

    # 字段级血缘补齐角色信息与列清单
    for e in clean_col_edges:
        for tid, col in ((e["source_table"], e["source_column"]), (e["target_table"], e["target_column"])):
            cols = table_map[tid]["columns"]
            if col not in cols:
                cols.append(col)

    # 列排序：* 放最后，其余不区分大小写排序；超长截断
    for t in table_map.values():
        cols = t["columns"]
        cols.sort(key=lambda c: (c == "*", c.lower()))
        if len(cols) > MAX_COLUMNS_PER_TABLE:
            warnings.append(f"表 {t['id']} 字段数超过 {MAX_COLUMNS_PER_TABLE}，仅展示前 {MAX_COLUMNS_PER_TABLE} 个")
            del cols[MAX_COLUMNS_PER_TABLE:]

    order = {"target": 0, "intermediate": 1, "source": 2}
    table_list = sorted(table_map.values(), key=lambda t: (order.get(t["role"], 3), t["id"].lower()))
    return {
        "tables": table_list,
        "table_edges": clean_table_edges,
        "column_edges": clean_col_edges,
        "warnings": warnings,
    }
