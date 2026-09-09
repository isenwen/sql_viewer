// -*- coding: utf-8 -*-
// Druid 血缘提取助手：stdin 读入 SQL，stdout 输出统一血缘 JSON。
// 由 backend/parsers/druid_parser.py 以子进程方式调用：
//   java -cp "<backend/jars/*>" DruidLineage <dbType> < sql
// 输出结构: {"tables":[{"id","role"}], "table_edges":[{"source","target"}],
//            "column_edges":[{"source_table","source_column","target_table","target_column"}],
//            "warnings":[...]}  或  {"error":"..."}
// 解析失败以 {"error":...} 返回（退出码 0），由 Python 侧转换为 ParseError。
import com.alibaba.druid.sql.SQLUtils;
import com.alibaba.druid.sql.ast.SQLExpr;
import com.alibaba.druid.sql.ast.SQLExprImpl;
import com.alibaba.druid.sql.ast.SQLName;
import com.alibaba.druid.sql.ast.SQLObject;
import com.alibaba.druid.sql.ast.SQLStatement;
import com.alibaba.druid.sql.ast.expr.SQLAllColumnExpr;
import com.alibaba.druid.sql.ast.expr.SQLIdentifierExpr;
import com.alibaba.druid.sql.ast.expr.SQLPropertyExpr;
import com.alibaba.druid.sql.ast.expr.SQLQueryExpr;
import com.alibaba.druid.sql.ast.statement.SQLCreateTableStatement;
import com.alibaba.druid.sql.ast.statement.SQLDeleteStatement;
import com.alibaba.druid.sql.ast.statement.SQLExprTableSource;
import com.alibaba.druid.sql.ast.statement.SQLInsertStatement;
import com.alibaba.druid.sql.ast.statement.SQLJoinTableSource;
import com.alibaba.druid.sql.ast.statement.SQLMergeStatement;
import com.alibaba.druid.sql.ast.statement.SQLSelect;
import com.alibaba.druid.sql.ast.statement.SQLSelectItem;
import com.alibaba.druid.sql.ast.statement.SQLSelectQuery;
import com.alibaba.druid.sql.ast.statement.SQLSelectQueryBlock;
import com.alibaba.druid.sql.ast.statement.SQLSelectStatement;
import com.alibaba.druid.sql.ast.statement.SQLSubqueryTableSource;
import com.alibaba.druid.sql.ast.statement.SQLTableSource;
import com.alibaba.druid.sql.ast.statement.SQLUnionQuery;
import com.alibaba.druid.sql.ast.statement.SQLUnionQueryTableSource;
import com.alibaba.druid.sql.ast.statement.SQLUpdateSetItem;
import com.alibaba.druid.sql.ast.statement.SQLUpdateStatement;
import com.alibaba.druid.sql.ast.statement.SQLWithSubqueryClause;
import com.alibaba.druid.sql.visitor.SQLASTVisitorAdapter;

import java.io.ByteArrayOutputStream;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class DruidLineage {

    // ---------------- 关系与作用域（与 Python 版 sqlglot 解析器同构） ----------------

    /** FROM 中可引用的关系：物理表或派生（子查询/CTE）。 */
    static class Rel {
        String kind;                                    // "table" | "derived"
        String table;                                   // 物理表限定名
        LinkedHashMap<String, Set<Col>> columns = new LinkedHashMap<>();
        Set<String> sources = new LinkedHashSet<>();    // 底层物理表集合
    }

    /** 列引用（表, 列），可哈希。 */
    static class Col {
        final String t, c;
        Col(String t, String c) { this.t = t; this.c = c; }
        @Override public boolean equals(Object o) {
            if (!(o instanceof Col)) return false;
            Col x = (Col) o;
            return t.equals(x.t) && c.equalsIgnoreCase(x.c);
        }
        @Override public int hashCode() { return t.toLowerCase().hashCode() * 31 + c.toLowerCase().hashCode(); }
    }

    static class Scope {
        final Scope parent;
        Map<String, Rel> ctes = new HashMap<>();
        Map<String, Rel> aliases = new LinkedHashMap<>();
        Scope(Scope p) { parent = p; }
        Rel findCte(String n) { Scope s = this; while (s != null) { Rel r = s.ctes.get(n); if (r != null) return r; s = s.parent; } return null; }
        Rel findAlias(String n) { Scope s = this; while (s != null) { Rel r = s.aliases.get(n); if (r != null) return r; s = s.parent; } return null; }
    }

    // ---------------- 结果累积 ----------------

    Map<String, String> tables = new LinkedHashMap<>();          // name -> role
    List<Map<String, String>> tableEdges = new ArrayList<>();
    List<Map<String, String>> colEdges = new ArrayList<>();
    List<String> warnings = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8"));
        String dbType = args.length > 0 ? args[0] : "mysql";
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = System.in.read(buf)) > 0) bos.write(buf, 0, n);
        String sql = new String(bos.toByteArray(), StandardCharsets.UTF_8);
        Map<String, Object> out = new LinkedHashMap<>();
        try {
            DruidLineage engine = new DruidLineage();
            engine.analyze(sql, dbType);
            List<Object> tl = new ArrayList<>();
            for (Map.Entry<String, String> e : engine.tables.entrySet()) {
                Map<String, String> t = new LinkedHashMap<>();
                t.put("id", e.getKey());
                t.put("role", e.getValue());
                tl.add(t);
            }
            out.put("tables", tl);
            out.put("table_edges", engine.tableEdges);
            out.put("column_edges", engine.colEdges);
            out.put("warnings", engine.warnings);
        } catch (Throwable t) {
            out.put("error", String.valueOf(t.getMessage() == null ? t.toString() : t.getMessage()));
        }
        System.out.println(json(out));
    }

    void analyze(String sql, String dbType) {
        List<SQLStatement> stmts;
        try {
            stmts = SQLUtils.parseStatements(sql, dbType);
        } catch (Throwable t) {
            throw new RuntimeException("Druid 解析失败: " + firstLine(t.getMessage()));
        }
        if (stmts == null || stmts.isEmpty()) throw new RuntimeException("Druid 未能解析出任何语句");
        int idx = 1;
        for (SQLStatement st : stmts) {
            if (st != null) processStatement(st, idx++);
        }
        if (tables.isEmpty()) throw new RuntimeException("Druid 未能从 SQL 中提取到表级血缘");
    }

    // ---------------- 语句分发 ----------------

    void processStatement(SQLStatement st, int idx) {
        if (st instanceof SQLInsertStatement) {
            SQLInsertStatement ins = (SQLInsertStatement) st;
            String target = tableSourceName(ins.getTableSource());
            if (target == null) return;
            addTable(target, "target");
            List<String> targetCols = new ArrayList<>();
            if (ins.getColumns() != null) {
                for (SQLExpr c : ins.getColumns()) {
                    String n = simpleName(c);
                    if (n != null) targetCols.add(n);
                }
            }
            SQLSelect q = ins.getQuery();
            if (q == null) return; // INSERT ... VALUES 无查询血缘
            Scope scope = new Scope(null);
            registerCtes(ins.getWith(), scope);
            Rel rel = analyzeSelect(q, scope);
            emitLineage(rel, target, targetCols, idx);
        } else if (st instanceof SQLCreateTableStatement) {
            SQLCreateTableStatement ct = (SQLCreateTableStatement) st;
            String target = cleanName(ct.getName());
            if (target == null || target.isEmpty()) return;
            addTable(target, "target");
            SQLSelect sel = ct.getSelect();
            if (sel == null) return; // 纯建表无血缘
            Rel rel = analyzeSelect(sel, new Scope(null));
            emitLineage(rel, target, new ArrayList<String>(), idx);
        } else if (st instanceof SQLUpdateStatement) {
            SQLUpdateStatement up = (SQLUpdateStatement) st;
            String target = tableSourceName(up.getTableSource());
            if (target == null) return;
            addTable(target, "target");
            Scope scope = new Scope(null);
            collectFrom(up.getTableSource(), scope);
            collectFrom(up.getFrom(), scope); // UPDATE ... FROM（PostgreSQL 语法）
            Set<String> srcs = new LinkedHashSet<>();
            for (Rel r : scope.aliases.values()) srcs.addAll(relSources(r));
            for (String t : srcs) {
                if (!t.equals(target)) { addTable(t, "source"); tableEdges.add(edge(t, target)); }
            }
            if (up.getItems() != null) {
                for (SQLUpdateSetItem item : up.getItems()) {
                    String cn = simpleName(item.getColumn());
                    if (cn == null) continue;
                    for (Col c : exprSources(item.getValue(), scope)) {
                        if (!c.t.equals(target)) colEdges.add(colEdge(c.t, c.c, target, cn));
                    }
                }
            }
        } else if (st instanceof SQLDeleteStatement) {
            String target = tableSourceName(((SQLDeleteStatement) st).getTableSource());
            if (target != null) addTable(target, "target");
        } else if (st instanceof SQLMergeStatement) {
            SQLMergeStatement mg = (SQLMergeStatement) st;
            String target = tableSourceName(mg.getInto());
            if (target == null) return;
            addTable(target, "target");
            Scope scope = new Scope(null);
            collectFrom(mg.getUsing(), scope);
            Set<String> srcs = new LinkedHashSet<>();
            for (Rel r : scope.aliases.values()) srcs.addAll(relSources(r));
            for (String t : srcs) {
                if (!t.equals(target)) { addTable(t, "source"); tableEdges.add(edge(t, target)); }
            }
            if (!srcs.isEmpty()) warnings.add("MERGE 语句的字段级血缘未提取（表级已生成）");
        } else if (st instanceof SQLSelectStatement) {
            SQLSelect sel = ((SQLSelectStatement) st).getSelect();
            Rel rel = analyzeSelect(sel, new Scope(null));
            for (String t : rel.sources) addTable(t, "source");
        } else {
            // 兜底：扫描语句中所有表引用
            for (SQLTableSource ts : findTableSources(st)) {
                String nm = tableSourceName(ts);
                if (nm != null) addTable(nm, "source");
            }
        }
    }

    // ---------------- 查询分析 ----------------

    Rel analyzeSelect(SQLSelect sel, Scope scope) {
        if (sel == null) return new Rel();
        registerCtes(sel.getWithSubQuery(), scope);
        return analyzeSelectQuery(sel.getQuery(), scope);
    }

    void registerCtes(SQLWithSubqueryClause with, Scope scope) {
        if (with == null || with.getEntries() == null) return;
        for (SQLWithSubqueryClause.Entry entry : with.getEntries()) {
            String alias = cleanName(entry.getAlias());
            if (alias == null || alias.isEmpty()) continue;
            scope.ctes.put(alias.toLowerCase(), analyzeSelect(entry.getSubQuery(), new Scope(scope)));
        }
    }

    Rel analyzeSelectQuery(SQLSelectQuery q, Scope scope) {
        Rel rel = new Rel();
        rel.kind = "derived";
        if (q instanceof SQLSelectQueryBlock) {
            SQLSelectQueryBlock qb = (SQLSelectQueryBlock) q;
            collectFrom(qb.getFrom(), scope);
            int i = 1;
            for (SQLSelectItem item : qb.getSelectList()) {
                String name = itemName(item);
                if (name == null || name.isEmpty()) name = "col_" + i;
                rel.columns.put(name, exprSources(item.getExpr(), scope));
                i++;
            }
        } else if (q instanceof SQLUnionQuery) {
            SQLUnionQuery u = (SQLUnionQuery) q;
            Rel l = analyzeSelectQuery(u.getLeft(), scope);
            Rel r = analyzeSelectQuery(u.getRight(), scope);
            for (Map.Entry<String, Set<Col>> en : l.columns.entrySet()) {
                rel.columns.put(en.getKey(), new LinkedHashSet<Col>(en.getValue()));
            }
            for (Map.Entry<String, Set<Col>> en : r.columns.entrySet()) {
                Set<Col> s = rel.columns.get(en.getKey());
                if (s == null) { s = new LinkedHashSet<Col>(en.getValue()); rel.columns.put(en.getKey(), s); }
                else s.addAll(en.getValue());
            }
            rel.sources.addAll(l.sources);
            rel.sources.addAll(r.sources);
        }
        for (Rel r : scope.aliases.values()) rel.sources.addAll(relSources(r));
        return rel;
    }

    // ---------------- FROM 解析 ----------------

    void collectFrom(SQLTableSource ts, Scope scope) {
        if (ts == null) return;
        if (ts instanceof SQLExprTableSource) {
            SQLExpr ex = ((SQLExprTableSource) ts).getExpr();
            String name = (ex instanceof SQLIdentifierExpr || ex instanceof SQLPropertyExpr) ? qualifiedName(ex) : null;
            Rel rel;
            if (name != null) {
                Rel cte = scope.findCte(lastSegment(name));
                rel = cte != null ? cte : newTableRel(name);
            } else {
                rel = new Rel(); // 函数表（UNNEST 等）无法提取物理表
            }
            String alias = cleanName(ts.getAlias());
            String key = (alias == null || alias.isEmpty()) ? (name == null ? "" : lastSegment(name)) : alias;
            if (!key.isEmpty()) scope.aliases.put(key.toLowerCase(), rel);
        } else if (ts instanceof SQLSubqueryTableSource) {
            Rel rel = analyzeSelect(((SQLSubqueryTableSource) ts).getSelect(), new Scope(scope));
            String alias = cleanName(ts.getAlias());
            if (alias != null && !alias.isEmpty()) scope.aliases.put(alias.toLowerCase(), rel);
        } else if (ts instanceof SQLJoinTableSource) {
            SQLJoinTableSource j = (SQLJoinTableSource) ts;
            collectFrom(j.getLeft(), scope);
            collectFrom(j.getRight(), scope);
        } else if (ts instanceof SQLUnionQueryTableSource) {
            Rel rel = analyzeSelectQuery(((SQLUnionQueryTableSource) ts).getUnion(), new Scope(scope));
            String alias = cleanName(ts.getAlias());
            if (alias != null && !alias.isEmpty()) scope.aliases.put(alias.toLowerCase(), rel);
        }
    }

    // ---------------- 表达式 → 列引用 ----------------

    Rel newTableRel(String name) {
        Rel r = new Rel();
        r.kind = "table";
        r.table = name;
        return r;
    }

    Set<Col> exprSources(SQLExpr e, Scope scope) {
        Set<Col> out = new LinkedHashSet<>();
        if (e == null) return out;
        if (e instanceof SQLIdentifierExpr) {
            String name = cleanName(((SQLIdentifierExpr) e).getName());
            if ("*".equals(name)) return expandStar(scope, null);
            out.addAll(resolveColumn(name, null, scope));
        } else if (e instanceof SQLPropertyExpr) {
            SQLPropertyExpr p = (SQLPropertyExpr) e;
            String name = cleanName(p.getName());
            String qualifier = qualifierOf(p.getOwner());
            if ("*".equals(name)) return expandStar(scope, qualifier);
            out.addAll(resolveColumn(name, qualifier, scope));
        } else if (e instanceof SQLAllColumnExpr) {
            return expandStar(scope, null);
        } else if (e instanceof SQLQueryExpr) {
            // 标量子查询：关联其全部输出列的来源（保守策略）
            Rel inner = analyzeSelect(((SQLQueryExpr) e).getSubQuery(), new Scope(scope));
            for (Set<Col> s : inner.columns.values()) out.addAll(s);
        } else if (e instanceof SQLExprImpl) {
            // 通用兜底：运算/函数/CASE 等表达式遍历其子表达式，收集所有列引用
            List<SQLObject> children = ((SQLExprImpl) e).getChildren();
            if (children != null) {
                for (SQLObject child : children) {
                    if (child instanceof SQLExpr) out.addAll(exprSources((SQLExpr) child, scope));
                }
            }
        }
        return out;
    }

    Set<Col> expandStar(Scope scope, String qualifier) {
        Set<Col> out = new LinkedHashSet<>();
        List<Rel> rels = new ArrayList<>();
        if (qualifier != null && !qualifier.isEmpty()) {
            Rel r = scope.findAlias(qualifier);
            if (r != null) rels.add(r);
        } else {
            rels.addAll(scope.aliases.values());
        }
        for (Rel r : rels) {
            if ("table".equals(r.kind)) out.add(new Col(r.table, "*"));
            else for (Set<Col> s : r.columns.values()) out.addAll(s);
        }
        return out;
    }

    Set<Col> resolveColumn(String name, String qualifier, Scope scope) {
        if (qualifier != null && !qualifier.isEmpty()) {
            Rel rel = scope.findAlias(qualifier);
            if (rel == null) rel = scope.findCte(qualifier);
            if (rel == null) return new LinkedHashSet<Col>();
            return resolveFromRel(rel, name);
        }
        Set<Col> matched = new LinkedHashSet<>();
        for (Rel r : scope.aliases.values()) {
            if ("derived".equals(r.kind) && r.columns.containsKey(name)) matched.addAll(r.columns.get(name));
        }
        if (!matched.isEmpty()) return matched;
        for (Rel r : scope.aliases.values()) {
            if ("table".equals(r.kind)) matched.add(new Col(r.table, name));
        }
        return matched;
    }

    Set<Col> resolveFromRel(Rel rel, String name) {
        Set<Col> out = new LinkedHashSet<>();
        if ("table".equals(rel.kind)) { out.add(new Col(rel.table, name)); return out; }
        Set<Col> s = rel.columns.get(name);
        if (s != null) return s;
        for (String t : rel.sources) out.add(new Col(t, name)); // 派生关系中找不到 -> 近似连到其所有物理表
        return out;
    }

    // ---------------- 血缘生成 ----------------

    void emitLineage(Rel rel, String target, List<String> targetCols, int idx) {
        for (String t : rel.sources) {
            if (t.equals(target)) continue;
            addTable(t, "source");
            tableEdges.add(edge(t, target));
        }
        if (rel.columns.isEmpty()) {
            warnings.add("第 " + idx + " 条语句未能提取字段级血缘");
            return;
        }
        int i = 0;
        for (Map.Entry<String, Set<Col>> en : rel.columns.entrySet()) {
            String tcol = i < targetCols.size() ? targetCols.get(i) : en.getKey();
            i++;
            for (Col c : en.getValue()) {
                colEdges.add(colEdge(c.t, c.c, target, tcol));
            }
        }
    }

    void addTable(String name, String role) {
        if (name == null || name.isEmpty()) return;
        String old = tables.get(name);
        if (old == null) { tables.put(name, role); return; }
        if (old.equals(role)) return;
        if ((old.equals("source") && role.equals("target")) || (old.equals("target") && role.equals("source"))) {
            tables.put(name, "intermediate");
        } else if (role.equals("target")) {
            tables.put(name, "target");
        }
    }

    // ---------------- 工具函数 ----------------

    String tableSourceName(SQLTableSource ts) {
        if (ts == null) return null;
        if (ts instanceof SQLExprTableSource) {
            SQLExpr ex = ((SQLExprTableSource) ts).getExpr();
            if (ex instanceof SQLIdentifierExpr || ex instanceof SQLPropertyExpr) return qualifiedName(ex);
            return null;
        }
        if (ts instanceof SQLSubqueryTableSource) {
            String alias = cleanName(ts.getAlias());
            return (alias == null || alias.isEmpty()) ? null : alias.toLowerCase();
        }
        return null;
    }

    List<SQLTableSource> findTableSources(SQLStatement st) {
        final List<SQLTableSource> out = new ArrayList<>();
        st.accept(new SQLASTVisitorAdapter() {
            @Override
            public boolean visit(SQLExprTableSource x) {
                out.add(x);
                return true;
            }
        });
        return out;
    }

    String itemName(SQLSelectItem item) {
        String alias = cleanName(item.getAlias());
        if (alias != null && !alias.isEmpty()) return alias;
        SQLExpr e = item.getExpr();
        if (e instanceof SQLIdentifierExpr) {
            String n = cleanName(((SQLIdentifierExpr) e).getName());
            return "*".equals(n) ? "*" : n;
        }
        if (e instanceof SQLPropertyExpr) {
            String n = cleanName(((SQLPropertyExpr) e).getName());
            return "*".equals(n) ? "*" : n;
        }
        if (e instanceof SQLAllColumnExpr) return "*";
        return null;
    }

    String qualifierOf(SQLExpr owner) {
        if (owner instanceof SQLIdentifierExpr) return cleanName(((SQLIdentifierExpr) owner).getName());
        if (owner instanceof SQLPropertyExpr) return cleanName(((SQLPropertyExpr) owner).getName());
        return null;
    }

    String qualifiedName(SQLExpr e) {
        if (e instanceof SQLIdentifierExpr) return cleanName(((SQLIdentifierExpr) e).getName());
        if (e instanceof SQLPropertyExpr) {
            String owner = qualifiedName(((SQLPropertyExpr) e).getOwner());
            String name = cleanName(((SQLPropertyExpr) e).getName());
            if (owner == null || owner.isEmpty()) return name;
            return owner + "." + name;
        }
        return null;
    }

    String simpleName(SQLExpr e) {
        if (e == null) return null;
        if (e instanceof SQLPropertyExpr) return cleanName(((SQLPropertyExpr) e).getName());
        if (e instanceof SQLIdentifierExpr) return cleanName(((SQLIdentifierExpr) e).getName());
        if (e instanceof SQLName) return cleanName(e.toString());
        String s = cleanName(e.toString());
        return (s == null || s.isEmpty()) ? null : lastSegment(s);
    }

    Set<String> relSources(Rel r) {
        if ("table".equals(r.kind)) {
            Set<String> s = new LinkedHashSet<>();
            s.add(r.table);
            return s;
        }
        return r.sources;
    }

    Map<String, String> edge(String source, String target) {
        Map<String, String> e = new LinkedHashMap<>();
        e.put("source", source);
        e.put("target", target);
        return e;
    }

    Map<String, String> colEdge(String st, String sc, String tt, String tc) {
        Map<String, String> e = new LinkedHashMap<>();
        e.put("source_table", st);
        e.put("source_column", sc);
        e.put("target_table", tt);
        e.put("target_column", tc);
        return e;
    }

    /** 去除反引号/双引号；表名统一小写。 */
    String cleanName(String s) {
        if (s == null) return null;
        s = s.replace("`", "").replace("\"", "").replace("'", "").trim();
        return s;
    }

    String cleanName(SQLName n) {
        return n == null ? null : cleanName(n.toString()).toLowerCase();
    }

    String lastSegment(String qualified) {
        if (qualified == null) return null;
        int i = Math.max(qualified.lastIndexOf('.'), 0);
        return i == 0 ? qualified : qualified.substring(i + 1);
    }

    String firstLine(String s) {
        if (s == null) return "";
        int i = s.indexOf('\n');
        return i > 0 ? s.substring(0, i) : s;
    }

    // ---------------- 极简 JSON 序列化 ----------------

    static String json(Object o) {
        StringBuilder sb = new StringBuilder();
        writeJson(o, sb);
        return sb.toString();
    }

    static void writeJson(Object o, StringBuilder sb) {
        if (o == null) { sb.append("null"); return; }
        if (o instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Object en0 : ((Map<?, ?>) o).entrySet()) {
                Map.Entry<?, ?> en = (Map.Entry<?, ?>) en0;
                if (!first) sb.append(',');
                first = false;
                writeJson(String.valueOf(en.getKey()), sb);
                sb.append(':');
                writeJson(en.getValue(), sb);
            }
            sb.append('}');
        } else if (o instanceof Iterable) {
            sb.append('[');
            boolean first = true;
            for (Object v : (Iterable<?>) o) {
                if (!first) sb.append(',');
                first = false;
                writeJson(v, sb);
            }
            sb.append(']');
        } else if (o instanceof Number || o instanceof Boolean) {
            sb.append(o.toString());
        } else {
            writeString(o.toString(), sb);
        }
    }

    static void writeString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            switch (ch) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (ch < 0x20) sb.append(String.format("\\u%04x", (int) ch));
                    else sb.append(ch);
            }
        }
        sb.append('"');
    }
}
