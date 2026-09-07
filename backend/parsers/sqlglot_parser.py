"""sqlglot 解析器（二级兜底）：基于 AST 的自定义血缘提取。

覆盖常见模式：INSERT INTO ... SELECT、CREATE TABLE/VIEW AS SELECT、UPDATE ... SET ... FROM、
CTE、子查询/内联视图、UNION、JOIN、别名、t.* 展开等。
复杂表达式会将表达式内引用到的所有输入列都连到输出列（保守策略）。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from ..dialects import get_dialect
from .base import BaseParser, ParseError, normalize_graph

MAX_CTE_DEPTH = 20


def _qualified(table: exp.Table) -> str:
    parts = [table.catalog, table.db, table.name]
    return ".".join(p for p in parts if p).lower()


class _Relation:
    """FROM 中可引用的关系：物理表或派生（子查询/CTE）。"""

    def __init__(self, kind, table=None):
        self.kind = kind                  # 'table' | 'derived'
        self.table = table                # 物理表限定名
        self.columns: dict[str, set] = {} # 输出列名 -> {(表, 列)}
        self.sources: set = set()         # 底层物理表集合


class _Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.ctes: dict[str, _Relation] = {}
        self.aliases: dict[str, _Relation] = {}

    def find_cte(self, name: str):
        s = self
        while s:
            r = s.ctes.get(name.lower())
            if r:
                return r
            s = s.parent
        return None

    def find_alias(self, name: str):
        s = self
        while s:
            r = s.aliases.get(name.lower())
            if r:
                return r
            s = s.parent
        return None


class SqlglotParser(BaseParser):
    name = "sqlglot"
    label = "sqlglot"

    def parse(self, sql: str, dialect: str) -> dict:
        d = get_dialect(dialect)
        read = d["sqlglot"]
        try:
            statements = sqlglot.parse(sql, read=read) if read else sqlglot.parse(sql)
        except Exception as e:
            raise ParseError(f"sqlglot 解析失败: {type(e).__name__}: {e}") from e
        statements = [s for s in statements or [] if s is not None]
        if not statements:
            raise ParseError("sqlglot 未能解析出任何语句")

        self._tables: dict[str, str] = {}
        self._table_edges: list[dict] = []
        self._col_edges: list[dict] = []
        self._warnings: list[str] = []

        for idx, stmt in enumerate(statements, 1):
            self._process_statement(stmt, idx)

        if not self._tables:
            raise ParseError("sqlglot 未能从 SQL 中提取到表级血缘")
        return normalize_graph(list(self._tables.items()), self._table_edges, self._col_edges, self._warnings)

    # ------------------------------------------------------------------ #
    def _add_table(self, name: str, role: str):
        if name:
            self._tables[name] = role

    def _process_statement(self, stmt: exp.Expression, idx: int):
        if isinstance(stmt, exp.Insert):
            self._process_insert(stmt, idx)
        elif isinstance(stmt, exp.Create):
            self._process_create(stmt, idx)
        elif isinstance(stmt, exp.Update):
            self._process_update(stmt, idx)
        elif isinstance(stmt, exp.Merge):
            self._process_merge(stmt, idx)
        elif isinstance(stmt, (exp.Select, exp.Union)):
            # 纯查询：仅列出来源表
            scope = _Scope()
            rel = self._analyze_query(stmt, scope)
            for t in rel.sources:
                self._add_table(t, "source")
        elif isinstance(stmt, exp.Command):
            return
        else:
            for t in stmt.find_all(exp.Table):
                self._add_table(_qualified(t), "source")

    # ------------------------------------------------------------------ #
    def _target_table(self, node) -> tuple[str, list[str]]:
        """返回 (限定表名, 显式列清单)。"""
        cols: list[str] = []
        if isinstance(node, exp.Schema):
            cols = [c.name for c in node.expressions if isinstance(c, exp.Column) or c.name]
            node = node.this
        return _qualified(node), cols

    def _process_insert(self, stmt: exp.Insert, idx: int):
        target, target_cols = self._target_table(stmt.this)
        if not target:
            return
        self._add_table(target, "target")
        query = stmt.expression
        if query is None:
            return
        scope = _Scope()
        self._register_ctes(stmt, scope)  # WITH 可挂在 INSERT 语句上
        rel = self._analyze_query(query, scope)
        self._emit_lineage(rel, target, target_cols, idx)

    def _process_create(self, stmt: exp.Create, idx: int):
        if str(stmt.args.get("kind", "")).upper() not in ("TABLE", "VIEW", "MATERIALIZED VIEW"):
            return
        target, _ = self._target_table(stmt.this)
        query = stmt.expression
        if not target or query is None:
            # CREATE TABLE t (col defs) 没有 AS SELECT，无法提取血缘
            if target and query is None:
                self._add_table(target, "target")
            return
        self._add_table(target, "target")
        scope = _Scope()
        rel = self._analyze_query(query, scope)
        self._emit_lineage(rel, target, [], idx)

    def _process_update(self, stmt: exp.Update, idx: int):
        target, _ = self._target_table(stmt.this)
        if not target:
            return
        self._add_table(target, "target")
        scope = _Scope()
        self._collect_from_joins(stmt, scope)
        src_tables = set()
        for r in scope.aliases.values():
            src_tables |= self._relation_sources(r)
        for t in src_tables:
            if t != target:
                self._add_table(t, "source")
                self._table_edges.append({"source": t, "target": target})
        for e in stmt.expressions:
            if not isinstance(e, exp.EQ) or not isinstance(e.left, exp.Column):
                continue
            for st, sc in self._expr_sources(e.right, scope):
                self._col_edges.append({
                    "source_table": st, "source_column": sc,
                    "target_table": target, "target_column": e.left.name,
                })

    def _process_merge(self, stmt: exp.Merge, idx: int):
        target, _ = self._target_table(stmt.this)
        if not target:
            return
        self._add_table(target, "target")
        scope = _Scope()
        using = stmt.args.get("using")
        if using is not None:
            self._resolve_from_item(using, scope)
        src_tables = set()
        for r in scope.aliases.values():
            src_tables |= self._relation_sources(r)
        for t in src_tables:
            if t != target:
                self._add_table(t, "source")
                self._table_edges.append({"source": t, "target": target})
        for when in stmt.find_all(exp.When):
            then = when.args.get("then")
            if isinstance(then, exp.Update):
                for e in then.expressions:
                    if isinstance(e, exp.EQ) and isinstance(e.left, exp.Column):
                        for st, sc in self._expr_sources(e.right, scope):
                            self._col_edges.append({
                                "source_table": st, "source_column": sc,
                                "target_table": target, "target_column": e.left.name,
                            })
            elif isinstance(then, exp.Insert):
                cols = [c.name for c in then.args.get("this").expressions] if isinstance(then.args.get("this"), exp.Schema) else []
                vals = then.args.get("expression")
                rows = vals.expressions[0].expressions if isinstance(vals, exp.Values) and vals.expressions else []
                for i, c in enumerate(cols):
                    if i < len(rows):
                        for st, sc in self._expr_sources(rows[i], scope):
                            self._col_edges.append({
                                "source_table": st, "source_column": sc,
                                "target_table": target, "target_column": c,
                            })
        if src_tables:
            self._warnings.append("MERGE 语句的字段级血缘为近似结果")

    # ------------------------------------------------------------------ #
    def _emit_lineage(self, rel: _Relation, target: str, target_cols: list[str], idx: int):
        for t in rel.sources:
            if t != target:
                self._add_table(t, "source")
                self._table_edges.append({"source": t, "target": target})
        if not rel.columns:
            self._warnings.append(f"第 {idx} 条语句未能提取字段级血缘")
            return
        for i, (name, sources) in enumerate(rel.columns.items()):
            tcol = target_cols[i] if i < len(target_cols) else name
            for st, sc in sources:
                self._col_edges.append({
                    "source_table": st, "source_column": sc,
                    "target_table": target, "target_column": tcol,
                })

    # ------------------------------------------------------------------ #
    def _analyze_query(self, query: exp.Expression, scope: _Scope) -> _Relation:
        """分析 SELECT / UNION，返回派生关系（输出列映射 + 底层物理表）。"""
        rel = _Relation("derived")
        if isinstance(query, (exp.Select,)):
            self._register_ctes(query, scope)
            self._collect_from_joins(query, scope)
            for i, p in enumerate(query.expressions):
                name = p.alias_or_name or (p.this.name if isinstance(p, exp.Column) else "")
                if isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)):
                    name = "*"
                if not name:
                    name = f"col_{i + 1}"
                rel.columns[name] = self._expr_sources(p, scope)
        elif isinstance(query, exp.Union):
            left = self._analyze_query(query.this, scope)
            right = self._analyze_query(query.expression, scope)
            rel.columns = {k: set(v) for k, v in left.columns.items()}
            for k, v in right.columns.items():
                rel.columns.setdefault(k, set()).update(v)
        elif isinstance(query, exp.Subquery):
            return self._analyze_query(query.this, scope)
        else:
            # VALUES 等其他查询体：无字段映射
            pass
        for r in scope.aliases.values():
            rel.sources |= self._relation_sources(r)
        return rel

    def _register_ctes(self, node: exp.Expression, scope: _Scope):
        with_clause = node.args.get("with_") or node.args.get("with")
        if not with_clause:
            return
        depth = 0
        for cte in with_clause.expressions:
            depth += 1
            if depth > MAX_CTE_DEPTH:
                break
            scope.ctes[cte.alias_or_name.lower()] = self._analyze_query(cte.this, _Scope(parent=scope))

    def _collect_from_joins(self, node: exp.Expression, scope: _Scope):
        frm = node.args.get("from_") or node.args.get("from")
        roots = []
        if frm is not None:
            roots.append(frm)
        roots += list(node.args.get("joins") or [])
        # 第一步：解析主 FROM 项 / JOIN（可处理子查询、CTE 引用等）
        for root in roots:
            item = root.this if isinstance(root, (exp.Join, exp.From)) else root
            self._resolve_from_item(item, scope)
        # 第二步：兜底扫描逗号分隔的表（sqlglot 对 UPDATE FROM a, b 有特殊嵌套结构）
        for root in roots:
            for t in root.find_all(exp.Table):
                anc, nested = t.parent, False
                while anc is not None and anc is not root:
                    if isinstance(anc, (exp.Select, exp.Subquery, exp.Union, exp.Values)):
                        nested = True
                        break
                    anc = anc.parent
                if nested:
                    continue  # 子查询内部的表由子查询自己的 scope 处理
                alias = t.alias_or_name.lower()
                if alias not in scope.aliases:
                    self._resolve_from_item(t, scope)

    def _resolve_from_item(self, node: exp.Expression, scope: _Scope) -> _Relation | None:
        if node is None:
            return None
        if isinstance(node, exp.Table):
            # 函数表（UNNEST 等）与嵌套子查询表
            if isinstance(node.this, (exp.Func, exp.Select, exp.Subquery)):
                if isinstance(node.this, exp.Select):
                    rel = self._analyze_query(node.this, _Scope(parent=scope))
                else:
                    return None
            else:
                cte = scope.find_cte(node.name)
                if cte is not None:
                    rel = cte
                else:
                    q = _qualified(node)
                    rel = _Relation("table", table=q)
                    self._add_table(q, "source")
            alias = node.alias_or_name
            scope.aliases[alias.lower()] = rel
            return rel
        if isinstance(node, exp.Subquery):
            inner = node.this
            rel = self._analyze_query(inner, _Scope(parent=scope))
            scope.aliases[node.alias_or_name.lower()] = rel
            return rel
        if isinstance(node, (exp.Select, exp.Union)):
            # 极少见的裸查询 FROM 项：返回派生关系但不注册别名
            return self._analyze_query(node, _Scope(parent=scope))
        # 逗号连接的普通表等
        for t in node.find_all(exp.Table):
            self._resolve_from_item(t, scope)
        return None

    def _relation_sources(self, rel: _Relation) -> set:
        if rel.kind == "table":
            return {rel.table}
        return set(rel.sources)

    # ------------------------------------------------------------------ #
    def _expr_sources(self, node: exp.Expression, scope: _Scope) -> set:
        """求值一个输出表达式引用到的所有 (物理表, 列)。"""
        if node is None:
            return set()
        if isinstance(node, exp.Alias):
            return self._expr_sources(node.this, scope)
        if isinstance(node, exp.Column):
            if isinstance(node.this, exp.Star):
                return self._expand_star(scope, node.table)
            return self._resolve_column(node, scope)
        if isinstance(node, exp.Star):
            return self._expand_star(scope, None)
        # 表达式（函数/运算/CASE 等）：收集其中所有列引用
        out = set()
        for col in node.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                out |= self._expand_star(scope, col.table)
            else:
                out |= self._resolve_column(col, scope)
        return out

    def _expand_star(self, scope: _Scope, qualifier: str | None) -> set:
        out = set()
        rels = []
        if qualifier:
            r = scope.find_alias(qualifier)
            if r:
                rels = [r]
        else:
            rels = list(scope.aliases.values())
        for r in rels:
            if r.kind == "table":
                out.add((r.table, "*"))
            else:
                for sources in r.columns.values():
                    out |= set(sources)
        return out

    def _resolve_column(self, col: exp.Column, scope: _Scope) -> set:
        name = col.name.lower()
        qualifier = col.table
        if qualifier:
            rel = scope.find_alias(qualifier)
            if rel is None:
                rel = scope.find_cte(qualifier)
            if rel is None:
                return set()
            return self._resolve_from_rel(rel, name)
        # 无限定：优先在派生关系里精确匹配，其次物理表
        matched = set()
        for rel in scope.aliases.values():
            if rel.kind == "derived" and name in rel.columns:
                matched |= set(rel.columns[name])
        if matched:
            return matched
        table_rels = [r for r in scope.aliases.values() if r.kind == "table"]
        if len(table_rels) == 1:
            return {(table_rels[0].table, name)}
        return {(r.table, name) for r in table_rels}

    def _resolve_from_rel(self, rel: _Relation, name: str) -> set:
        if rel.kind == "table":
            return {(rel.table, name)}
        if name in rel.columns:
            return set(rel.columns[name])
        # 派生关系中找不到该列 -> 近似连到其所有物理表的同名列
        return {(t, name) for t in rel.sources}
