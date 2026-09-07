/* SQL 血缘查看器前端逻辑 */
/* global monaco, G6, sqlFormatter */
"use strict";

/* ============================ 常量与状态 ============================ */

const DIALECTS = [
  ["mysql", "MySQL"], ["postgresql", "PostgreSQL"], ["oracle", "Oracle"],
  ["sqlserver", "SQL Server"], ["hive", "Hive"], ["spark", "Spark SQL"],
  ["clickhouse", "ClickHouse"], ["sqlite", "SQLite"], ["mariadb", "MariaDB"],
  ["trino", "Trino/Presto"], ["duckdb", "DuckDB"], ["bigquery", "BigQuery"],
  ["db2", "DB2"], ["ansi", "ANSI (通用)"],
];

const FORMATTER_LANG = {
  mysql: "mysql", postgresql: "postgresql", oracle: "plsql", sqlserver: "transactsql",
  hive: "hive", spark: "spark", clickhouse: "clickhouse", sqlite: "sqlite",
  mariadb: "mariadb", trino: "trino", duckdb: "duckdb", bigquery: "bigquery",
  db2: "db2", ansi: "sql",
};

const ROLE_COLORS = { source: "#1677ff", target: "#52c41a", intermediate: "#fa8c16", unknown: "#8c8c8c" };
const ROLE_LABELS = { source: "源表", target: "目标表", intermediate: "中间表", unknown: "未知" };

// 自定义表节点布局常量（getAnchorPoints 与 draw 必须一致）
const TITLE_H = 38, ROW_H = 24, PAD_B = 10, NODE_MIN_W = 170, NODE_MAX_W = 380;
const EDGE_COLOR = "#94a3b8";
const DEFAULT_BORDER = "#cbd5e1";

const SAMPLES = {
  join: `INSERT INTO dw.orders_agg
SELECT o.order_id,
       o.user_id,
       u.name,
       o.amount * 1.1 AS amt
FROM ods.orders o
LEFT JOIN dim.users u ON o.user_id = u.id
WHERE o.dt = '2026-01-01'`,
  cte: `WITH active AS (
  SELECT id, name FROM dim.users WHERE status = 1
)
INSERT INTO dw.vip_users
SELECT a.id, a.name, p.total
FROM active a
JOIN agg.pay p ON a.id = p.uid;

INSERT INTO tmp.x
SELECT id FROM dim.users;`,
  star: `INSERT INTO db.tgt
SELECT t.* FROM db.src1 t
JOIN db.src2 s ON t.id = s.id`,
  bad: `THIS IS NOT SQL AT ALL !!!`,
};

const state = {
  dialect: localStorage.getItem("sv_dialect") || "mysql",
  parser: "auto",
  mode: localStorage.getItem("sv_mode") || "table",
  theme: localStorage.getItem("sv_theme") || "vs-dark",
  hlColor: localStorage.getItem("sv_hlColor") || "#fa541c",
  wmOn: localStorage.getItem("sv_wmOn") === "1",
  wmText: localStorage.getItem("sv_wmText") || "SQL 血缘图 · 仅供内部使用",
  result: null,
};

let editor = null;
let graph = null;
let hlState = null; // { edges:[], nodes:[], dimEdges:[], dimNodes:[], label }
let hlPlain = null; // 高亮态的纯数据记录 { edges:[], nodes:[], color }，供导出复现

/* ============================ 工具函数 ============================ */

const $ = (id) => document.getElementById(id);

function textWidth(s, fontSize) {
  let w = 0;
  for (const ch of String(s)) w += ch.charCodeAt(0) > 255 ? fontSize : fontSize * 0.62;
  return w;
}

function nodeHeight(cfg) {
  if (cfg.mode === "field") {
    const rows = Math.max(cfg.columns.length, 1);
    return TITLE_H + rows * ROW_H + PAD_B;
  }
  return 40; // 表级模式仅头部色带
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function setStatus(kind, lines) {
  const el = $("status");
  if (!lines || !lines.length) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "status " + kind;
  el.innerHTML = lines.map(escHtml).join("\n");
}

/* ============================ Monaco 编辑器 ============================ */

function initMonaco() {
  // 0.45 的 AMD 构建不含独立 worker 文件，用空 worker 兜底（SQL 高亮在主线程完成）
  window.MonacoEnvironment = {
    getWorkerUrl: () => URL.createObjectURL(new Blob(["self.onmessage=function(){}"], { type: "text/javascript" })),
  };
  require.config({ paths: { vs: "vendor/monaco/vs" } });
  require(["vs/editor/editor.main"], () => {
    editor = monaco.editor.create($("editor"), {
      value: SAMPLES.join,
      language: "sql",
      theme: state.theme,
      automaticLayout: true,
      fontSize: 13,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      tabSize: 2,
    });

    // 注册文档格式化（Shift+Alt+F 与「美化」按钮共用）
    monaco.languages.registerDocumentFormattingEditProvider("sql", {
      provideDocumentFormattingEdits(model) {
        const formatted = formatSql(model.getValue());
        if (formatted === model.getValue()) return [];
        return [{ range: model.getFullModelRange(), text: formatted }];
      },
    });

    // SQL 关键字补全（不依赖 worker）
    monaco.languages.registerCompletionItemProvider("sql", {
      provideCompletionItems() {
        const kw = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "JOIN", "LEFT JOIN",
          "RIGHT JOIN", "INNER JOIN", "FULL JOIN", "ON", "UNION ALL", "UNION", "INSERT INTO", "UPDATE",
          "DELETE FROM", "CREATE TABLE", "CREATE VIEW", "DROP TABLE", "ALTER TABLE", "WITH", "AS",
          "CASE WHEN", "THEN", "ELSE", "END", "AND", "OR", "NOT", "NULL", "IN", "BETWEEN", "LIKE",
          "DISTINCT", "LIMIT", "COUNT(*)", "SUM", "AVG", "MAX", "MIN", "COALESCE", "CAST", "OVER"];
        return {
          suggestions: kw.map((k) => ({
            label: k, kind: monaco.languages.CompletionItemKind.Keyword,
            insertText: k, detail: "SQL 关键字",
          })),
        };
      },
    });

    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, doParse);
  });
}

function formatSql(sql) {
  try {
    return sqlFormatter.format(sql, {
      language: FORMATTER_LANG[state.dialect] || "sql",
      tabWidth: 2,
      keywordCase: "upper",
    });
  } catch (e) {
    setStatus("err", ["SQL 美化失败（不影响解析）: " + e.message]);
    return sql;
  }
}

/* ============================ G6 血缘图 ============================ */

function registerTableNode() {
  G6.registerNode("table-node", {
    draw(cfg, group) {
      const columns = cfg.columns || [];
      const style = cfg.style || {};
      const baseOpacity = style.opacity !== undefined ? style.opacity : 1;
      const titleW = textWidth(cfg.title, 12) + 46;
      const colW = columns.length ? Math.max(...columns.map((c) => textWidth(c, 11))) + 44 : 0;
      const W = Math.max(NODE_MIN_W, Math.min(NODE_MAX_W, Math.max(titleW, colW)));
      const H = nodeHeight(cfg);
      const roleColor = ROLE_COLORS[cfg.role] || ROLE_COLORS.unknown;
      cfg._w = W; cfg._h = H;

      // 主体（高亮/置灰样式在重绘时统一应用）
      const key = group.addShape("rect", {
        attrs: {
          x: 0, y: 0, width: W, height: H, fill: "#fff",
          stroke: style.stroke || DEFAULT_BORDER,
          lineWidth: style.lineWidth || 1,
          shadowColor: style.shadowColor || null,
          shadowBlur: style.shadowColor ? (style.shadowBlur || 0) : 0,
          radius: 6, opacity: baseOpacity,
        },
        name: "body",
      });
      // 头部色带
      group.addShape("rect", {
        attrs: { x: 0, y: 0, width: W, height: TITLE_H, fill: roleColor, radius: [6, 6, 0, 0],
                 cursor: "move", opacity: baseOpacity },
        name: "header",
      });
      group.addShape("text", {
        attrs: { x: 12, y: TITLE_H / 2 + 1, text: cfg.title, fill: "#fff", fontSize: 12, fontWeight: 600,
                 textBaseline: "middle", cursor: "move", opacity: baseOpacity },
        name: "title",
      });
      // 字段行
      if (cfg.mode === "field") {
        const rows = columns.length ? columns : null;
        const rowCount = rows ? rows.length : 1;
        for (let i = 0; i < rowCount; i++) {
          const y = TITLE_H + i * ROW_H;
          const label = rows ? rows[i] : "（无字段信息）";
          const hit = "col-hit-" + i;
          group.addShape("rect", {
            attrs: { x: 1, y, width: W - 2, height: ROW_H, fill: "transparent",
                     cursor: rows ? "pointer" : "default", opacity: baseOpacity },
            name: hit,
          });
          group.addShape("text", {
            attrs: { x: 14, y: y + ROW_H / 2 + 1, text: label, fill: rows ? "#334155" : "#94a3b8",
                     fontSize: 11, textBaseline: "middle", cursor: rows ? "pointer" : "default",
                     opacity: baseOpacity },
            name: hit,
          });
          if (rows && i > 0) {
            group.addShape("rect", {
              attrs: { x: 8, y, width: W - 16, height: 1, fill: "#f1f5f9", opacity: baseOpacity },
              name: "sep-" + i,
            });
          }
        }
      }
      return key;
    },

    getAnchorPoints(cfg) {
      if (cfg.mode === "field") {
        const H = nodeHeight(cfg);
        const pts = [[1, TITLE_H / 2 / H], [0, TITLE_H / 2 / H]];
        const cols = cfg.columns && cfg.columns.length ? cfg.columns : null;
        const n = cols ? cols.length : 0;
        for (let i = 0; i < n; i++) {
          const fy = (TITLE_H + i * ROW_H + ROW_H / 2) / H;
          pts.push([1, fy]); // 2 + 2i  右侧
          pts.push([0, fy]); // 3 + 2i  左侧
        }
        return pts;
      }
      return [[1, 0.5], [0, 0.5]];
    },
    // 不定义 update：updateItem 时 G6 会重新执行 draw，保证模式切换/高亮都能正确重绘
  }, "single-node");
}

function edgeArrow(color) {
  return { path: G6.Arrow.triangle(9, 10, 11), fill: color };
}

function initGraph() {
  registerTableNode();
  graph = new G6.Graph({
    container: "graphContainer",
    renderer: "canvas",
    width: $("graphContainer").clientWidth,
    height: $("graphContainer").clientHeight,
    // 布局由 layeredLayout 预先计算好坐标，不使用 G6 内置布局
    modes: { default: ["drag-canvas", "zoom-canvas", "drag-node"] },
    minZoom: 0.05,
    maxZoom: 4,
    defaultNode: { type: "table-node" },
    defaultEdge: {
      type: "polyline",
      style: { stroke: EDGE_COLOR, lineWidth: 1.4, radius: 10, endArrow: edgeArrow(EDGE_COLOR), cursor: "pointer" },
    },
    plugins: [
      new G6.Minimap({
        size: [170, 118],
        type: "delegate",
        delegateStyle: { fill: "#1677ff", stroke: "none" },
        viewportClassName: "g6-minimap-viewport",
      }),
    ],
  });

  const resize = () => {
    if (!graph || graph.get("destroyed")) return;
    const w = $("graphContainer").clientWidth, h = $("graphContainer").clientHeight;
    if (w > 0 && h > 0) graph.changeSize(w, h);
  };
  new ResizeObserver(resize).observe($("graphContainer"));

  graph.on("viewportchange", updateZoomLabel);
  graph.on("canvas:click", clearHighlight);
  graph.on("node:click", onNodeClick);
  graph.on("edge:click", onEdgeClick);
}

function updateZoomLabel() {
  $("zoomPct").textContent = Math.round(graph.getZoom() * 100) + "%";
}

/* ---------- 数据构建 ---------- */

function colIdx(columns, col) {
  const i = columns.findIndex((c) => c.toLowerCase() === String(col).toLowerCase());
  return i >= 0 ? i : null;
}

function nodeEstHeight(n) {
  return n.mode === "field" ? TITLE_H + Math.max(n.columns.length, 1) * ROW_H + PAD_B : 40;
}

function nodeEstWidth(n) {
  const titleW = textWidth(n.title, 12) + 46;
  const colW = n.columns.length ? Math.max(...n.columns.map((c) => textWidth(c, 11))) + 44 : 0;
  return Math.max(NODE_MIN_W, Math.min(NODE_MAX_W, Math.max(titleW, colW)));
}

/* 确定性分层布局：血缘图为 DAG，按最长路径分层，列内按前驱重心排序并垂直居中 */
function layeredLayout(nodes, edges, mode) {
  const byId = {};
  nodes.forEach((n) => (byId[n.id] = n));
  const succ = {}, preds = {}, indeg = {};
  nodes.forEach((n) => { succ[n.id] = []; preds[n.id] = []; indeg[n.id] = 0; });
  edges.forEach((e) => {
    if (e.source === e.target || !byId[e.source] || !byId[e.target]) return; // 自环与异常边不参与分层
    succ[e.source].push(e.target);
    preds[e.target].push(e.source);
    indeg[e.target]++;
  });
  // 拓扑 + 最长路径分层
  const rank = {};
  let q = nodes.filter((n) => indeg[n.id] === 0).map((n) => n.id);
  q.forEach((id) => (rank[id] = 0));
  const deg = { ...indeg };
  while (q.length) {
    const cur = q.shift();
    for (const nx of succ[cur]) {
      rank[nx] = Math.max(rank[nx] ?? 0, rank[cur] + 1);
      if (--deg[nx] === 0) q.push(nx);
    }
  }
  nodes.forEach((n) => { if (rank[n.id] === undefined) rank[n.id] = 0; }); // 环上节点兜底

  const layers = [];
  nodes.forEach((n) => {
    (layers[rank[n.id]] = layers[rank[n.id]] || []).push(n);
  });

  // 层内排序：按前驱在上一层的序号重心，减少交叉
  const orderIn = {};
  for (let r = 0; r < layers.length; r++) {
    const layer = layers[r] || [];
    if (r > 0) {
      layer.sort((a, b) => {
        const bc = (n) => {
          const ps = preds[n.id].map((p) => orderIn[p]).filter((v) => v !== undefined);
          return ps.length ? ps.reduce((x, y) => x + y, 0) / ps.length : 1e9;
        };
        return bc(a) - bc(b) || a.id.localeCompare(b.id);
      });
    }
    layer.forEach((n, i) => (orderIn[n.id] = i));
  }

  // 列几何：列宽 = 层内最大宽度，x 累加；层内节点垂直居中
  const ranksep = mode === "field" ? 90 : 80;
  const nodesep = mode === "field" ? 30 : 22;
  const colW = layers.map((layer) => (layer || []).length ? Math.max(...layer.map(nodeEstWidth)) : 0);
  const xs = [];
  let x = 0;
  for (let r = 0; r < layers.length; r++) { xs[r] = x; x += (colW[r] || 0) + ranksep; }
  const maxRows = Math.max(...layers.map((l) => (l || []).length));
  const colH = layers.map((layer) => {
    const hs = (layer || []).map(nodeEstHeight);
    return hs.reduce((a, b) => a + b, 0) + (hs.length - 1) * nodesep;
  });
  const totalH = Math.max(...colH, 0);
  layers.forEach((layer, r) => {
    if (!layer) return;
    let y = (totalH - colH[r]) / 2;
    layer.forEach((n) => {
      const h = nodeEstHeight(n);
      n.x = xs[r];
      n.y = y + h / 2; // G6 节点坐标为中心点
      y += h + nodesep;
    });
  });
  void maxRows;
}

function buildGraphData(result, mode) {
  const nodes = result.tables.map((t) => ({
    id: t.id,
    // 不用 label 字段：G6 内置 update 会据 label 生成多余的 text-shape
    title: t.id,
    role: t.role || "source",
    columns: t.columns || [],
    mode,
  }));
  let edges = [];
  if (mode === "table") {
    edges = result.table_edges.map((e) => ({ source: e.source, target: e.target, kind: "table", sourceAnchor: 0, targetAnchor: 1 }));
  } else {
    const colMap = {};
    nodes.forEach((n) => (colMap[n.id] = n.columns));
    edges = result.column_edges.map((e) => {
      const si = colIdx(colMap[e.source_table] || [], e.source_column);
      const ti = colIdx(colMap[e.target_table] || [], e.target_column);
      return {
        source: e.source_table, target: e.target_table, kind: "column",
        sourceColumn: e.source_column, targetColumn: e.target_column,
        sourceAnchor: si === null ? 0 : 2 + 2 * si,
        targetAnchor: ti === null ? 1 : 3 + 2 * ti,
      };
    });
  }
  layeredLayout(nodes, edges, mode);
  return { nodes, edges };
}

function centerCanvasKeepZoom() {
  // G6 v4 无 center()：getGraphCenterPoint 返回图坐标系位置，需转成画布坐标再平移
  const gc = graph.getGraphCenterPoint();
  const c = graph.getCanvasByPoint(gc.x, gc.y);
  const w = graph.get("width"), h = graph.get("height");
  graph.translate(w / 2 - c.x, h / 2 - c.y);
}

function fitViewSafely() {
  graph.fitView(30);
  if (graph.getZoom() > 1) graph.zoomTo(1); // 内容较少时不要过度放大
  centerCanvasKeepZoom();
  updateZoomLabel();
}

function renderGraph() {
  if (!graph || !state.result) return;
  clearHighlight();
  const data = buildGraphData(state.result.graph, state.mode);
  const hasData = data.nodes.length > 0;
  $("placeholder").style.display = hasData ? "none" : "flex";
  // 先清空再导入，强制所有节点重新 draw，避免 update 路径跳过结构变化
  graph.clear();
  graph.changeData(hasData ? data : { nodes: [], edges: [] });
  if (hasData) fitViewSafely();
  updateStats();
}

function updateStats() {
  if (!state.result) { $("statText").textContent = ""; return; }
  const g = state.result.graph;
  $("statText").textContent =
    `表 ${g.tables.length} · 表级边 ${g.table_edges.length} · 字段级边 ${g.column_edges.length} · 引擎 ${state.result.parser}`;
}

/* ---------- 高亮 ---------- */

function tableClosure(startId) {
  const visited = new Set([startId]);
  const edges = [];
  const pickedKeys = new Set();
  let frontier = [startId];
  const adj = {};
  state.result.graph.table_edges.forEach((e) => {
    (adj[e.source] = adj[e.source] || []).push({ other: e.target, edge: e });
    (adj[e.target] = adj[e.target] || []).push({ other: e.source, edge: e });
  });
  while (frontier.length) {
    const next = [];
    for (const cur of frontier) {
      for (const { other, edge } of adj[cur] || []) {
        const k = edge.source + ">" + edge.target;
        if (!pickedKeys.has(k)) { pickedKeys.add(k); edges.push(edge); }
        if (!visited.has(other)) { visited.add(other); next.push(other); }
      }
    }
    frontier = next;
  }
  return { nodes: visited, edges };
}

function colKey(t, c) { return t + "::" + String(c).toLowerCase(); }

function columnClosure(startTable, startCol) {
  const edges = state.result.graph.column_edges;
  const adj = {};
  edges.forEach((e) => {
    const sk = colKey(e.source_table, e.source_column), tk = colKey(e.target_table, e.target_column);
    (adj[sk] = adj[sk] || []).push({ other: tk, edge: e });
    (adj[tk] = adj[tk] || []).push({ other: sk, edge: e });
  });
  const start = colKey(startTable, startCol);
  const visited = new Set([start]);
  const picked = [];
  const pickedKeys = new Set();
  let frontier = [start];
  while (frontier.length) {
    const next = [];
    for (const cur of frontier) {
      for (const { other, edge } of adj[cur] || []) {
        const k = colKey(edge.source_table, edge.source_column) + ">" + colKey(edge.target_table, edge.target_column);
        if (!pickedKeys.has(k)) { pickedKeys.add(k); picked.push(edge); }
        if (!visited.has(other)) { visited.add(other); next.push(other); }
      }
    }
    frontier = next;
  }
  // 涉及的表
  const tables = new Set();
  visited.forEach((k) => tables.add(k.split("::")[0]));
  return { keys: visited, tables, edges: picked };
}

function onNodeClick(evt) {
  if (!state.result) return;
  const node = evt.item;
  const m = node.getModel();
  const name = evt.target.get("name");
  // 字段级模式下点击字段行 -> 字段链路高亮
  if (state.mode === "field" && name && name.startsWith("col-hit-")) {
    const idx = +name.slice(8);
    const col = (m.columns || [])[idx];
    if (!col) return;
    const clo = columnClosure(m.id, col);
    if (!clo.edges.length) { flashNoLineage(); return; }
    applyHighlight({
      edges: clo.edges,
      nodes: [...clo.tables],
      label: `字段链路 ${m.id}.${col}（${clo.edges.length} 条边）`,
    });
    return;
  }
  // 表级高亮（表级模式点击节点 / 字段级模式点击表头）
  if (state.mode === "table") {
    const clo = tableClosure(m.id);
    applyHighlight({
      edges: clo.edges,
      nodes: [...clo.nodes],
      label: `表链路 ${m.id}（${clo.edges.length} 条边）`,
    });
  } else {
    // 字段级模式点击表头：高亮该表相关的所有字段链路
    const edges = state.result.graph.column_edges.filter((e) => e.source_table === m.id || e.target_table === m.id);
    if (!edges.length) { flashNoLineage(); return; }
    const tables = new Set([m.id]);
    edges.forEach((e) => { tables.add(e.source_table); tables.add(e.target_table); });
    applyHighlight({ edges, nodes: [...tables], label: `表 ${m.id} 相关字段链路（${edges.length} 条边）` });
  }
}

function onEdgeClick(evt) {
  if (!state.result) return;
  const e = evt.item.getModel();
  if (state.mode === "table") {
    applyHighlight({ edges: [e], nodes: [e.source, e.target], label: `边 ${e.source} → ${e.target}` });
  } else {
    const clo = columnClosure(e.source, e.sourceColumn);
    const nodes = [...clo.tables];
    if (!clo.tables.has(e.target_table)) nodes.push(e.target_table);
    applyHighlight({ edges: clo.edges.length ? clo.edges : [e], nodes, label: `字段链路 ${e.source}.${e.sourceColumn}` });
  }
}

function flashNoLineage() {
  setStatus("err", ["该节点没有可展示的血缘链路"]);
  clearTimeout(flashNoLineage._t);
  flashNoLineage._t = setTimeout(() => setStatus("ok", []), 1800);
}

/* 边匹配键：兼容 G6 边模型(source/target/sourceColumn)与后端原始结构(source_table/source_column) */
function edgeKey(e) {
  return (e.source || e.source_table || "") + "|" + (e.target || e.target_table || "") + "|" +
    (e.sourceColumn || e.source_column || "") + "|" + (e.targetColumn || e.target_column || "");
}

function applyHighlight(clo) {
  clearHighlight();
  const color = state.hlColor;
  const edges = new Set(), nodes = new Set(), dimE = new Set(), dimN = new Set();
  const want = new Set(clo.edges.map(edgeKey));
  graph.getEdges().forEach((item) => {
    const m = item.getModel();
    if (want.has(edgeKey(m))) edges.add(item); else dimE.add(item);
  });
  graph.getNodes().forEach((item) => {
    const m = item.getModel();
    if (clo.nodes.includes(m.id)) nodes.add(item); else dimN.add(item);
  });

  edges.forEach((item) => graph.updateItem(item, { style: { stroke: color, lineWidth: 2.4, opacity: 1, endArrow: edgeArrow(color) } }));
  nodes.forEach((item) => graph.updateItem(item, { style: { opacity: 1, stroke: color, lineWidth: 2, shadowColor: color, shadowBlur: 10 } }));
  dimE.forEach((item) => graph.updateItem(item, { style: { opacity: 0.1 } }));
  dimN.forEach((item) => graph.updateItem(item, { style: { opacity: 0.15 } }));

  hlState = { edges, nodes, dimE, dimN, label: clo.label };
  hlPlain = { edges: clo.edges, nodes: clo.nodes, color };
  setStatus("ok", ["已高亮: " + clo.label + "（点击空白处或「清除高亮」恢复）"]);
}

function clearHighlight() {
  if (!hlState || !graph) return;
  const resetEdge = { style: { stroke: EDGE_COLOR, lineWidth: 1.4, opacity: 1, endArrow: edgeArrow(EDGE_COLOR) } };
  const resetNode = { style: { opacity: 1, stroke: DEFAULT_BORDER, lineWidth: 1, shadowColor: "", shadowBlur: 0 } };
  hlState.edges.forEach((i) => graph.updateItem(i, resetEdge));
  hlState.nodes.forEach((i) => graph.updateItem(i, resetNode));
  hlState.dimE.forEach((i) => graph.updateItem(i, { style: { opacity: 1 } }));
  hlState.dimN.forEach((i) => graph.updateItem(i, { style: { opacity: 1 } }));
  hlState = null;
  hlPlain = null;
  setStatus("ok", []);
}

/* ---------- 水印 ---------- */

function applyWatermark() {
  const wm = $("watermark");
  $("wmToggle").checked = state.wmOn;
  $("wmText").value = state.wmText;
  if (!state.wmOn) { wm.hidden = true; return; }
  const t = escHtml(state.wmText || "SQL 血缘图");
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='300' height='190'>` +
    `<text x='26' y='105' font-size='15' fill='rgba(15,23,42,0.07)' font-family='Microsoft YaHei' transform='rotate(-18 150 95)'>${t}</text></svg>`;
  wm.style.backgroundImage = `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}")`;
  wm.hidden = false;
}

/* ---------- 解析请求 ---------- */

async function doParse() {
  const sql = editor.getValue();
  if (!sql.trim()) { setStatus("err", ["SQL 内容为空"]); return; }
  setStatus("ok", ["正在解析…"]);
  try {
    const resp = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql, dialect: state.dialect, parser: state.parser }),
    });
    const data = await resp.json();
    if (!data.ok) {
      const lines = ["解析失败: " + (data.error || "未知错误")];
      (data.attempts || []).forEach((a) => lines.push(`  · ${a.parser}: ${a.message || "未使用"}`));
      setStatus("err", lines);
      return;
    }
    state.result = data;
    renderGraph();
    const g = data.graph;
    const lines = [
      `解析成功 · 引擎: ${data.parser} · 表 ${g.tables.length} · 表级边 ${g.table_edges.length} · 字段级边 ${g.column_edges.length}`,
    ];
    (data.attempts || []).forEach((a) => {
      if (!a.ok && a.message) lines.push(`  · ${a.parser} 失败: ${a.message}`);
    });
    (g.warnings || []).forEach((w) => lines.push("  ⚠ " + w));
    setStatus("ok", lines);
  } catch (e) {
    setStatus("err", ["请求失败: " + e.message + "（请确认后端服务已启动）"]);
  }
}

/* ---------- 图片下载 ---------- */

/* ---------- 导出（SVG 矢量图 / 高清 PNG） ---------- */

function exportFileName(ext) {
  return "sql_lineage_" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + "." + ext;
}

function downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* 用离屏 SVG 渲染器重建整张图，序列化为独立 SVG 字符串（矢量，无限清晰） */
function buildSvgString() {
  if (!graph || !state.result) { setStatus("err", ["请先解析生成血缘图"]); return null; }
  const data = buildGraphData(state.result.graph, state.mode);

  // 复现当前高亮状态
  if (hlPlain) {
    const want = new Set(hlPlain.edges.map(edgeKey));
    const idset = new Set(hlPlain.nodes);
    data.edges.forEach((e) => {
      e.style = want.has(edgeKey(e))
        ? { stroke: hlPlain.color, lineWidth: 2.4, endArrow: edgeArrow(hlPlain.color) }
        : { opacity: 0.1 };
    });
    data.nodes.forEach((n) => {
      n.style = idset.has(n.id)
        ? { stroke: hlPlain.color, lineWidth: 2, shadowColor: hlPlain.color, shadowBlur: 10 }
        : { opacity: 0.15 };
    });
  }

  // 内容包围盒 + 边距
  const PAD = 30;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  data.nodes.forEach((n) => {
    const w = nodeEstWidth(n), h = nodeEstHeight(n);
    minX = Math.min(minX, n.x - w / 2); maxX = Math.max(maxX, n.x + w / 2);
    minY = Math.min(minY, n.y - h / 2); maxY = Math.max(maxY, n.y + h / 2);
  });
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 100; maxY = 100; }
  const W = Math.ceil(maxX - minX) + PAD * 2;
  const H = Math.ceil(maxY - minY) + PAD * 2;
  const dx = PAD - minX, dy = PAD - minY;
  data.nodes.forEach((n) => { n.x += dx; n.y += dy; });

  const holder = document.createElement("div");
  holder.style.cssText = "position:fixed;left:-99999px;top:0;";
  document.body.appendChild(holder);
  let str = null;
  try {
    const temp = new G6.Graph({
      container: holder,
      renderer: "svg",
      width: W,
      height: H,
      defaultNode: { type: "table-node" },
      defaultEdge: {
        type: "polyline",
        style: { stroke: EDGE_COLOR, lineWidth: 1.4, radius: 10, endArrow: edgeArrow(EDGE_COLOR) },
      },
    });
    temp.data(data);
    temp.render();

    const svg = holder.querySelector("svg");
    svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    const NS = "http://www.w3.org/2000/svg";
    // 背景与水印
    const wmId = "wm-pattern";
    const bg = document.createElementNS(NS, "rect");
    bg.setAttribute("x", 0); bg.setAttribute("y", 0);
    bg.setAttribute("width", W); bg.setAttribute("height", H);
    bg.setAttribute("fill", "#ffffff");
    svg.insertBefore(bg, svg.firstChild);
    if (state.wmOn && state.wmText) {
      const defs = document.createElementNS(NS, "defs");
      defs.innerHTML =
        `<pattern id="${wmId}" width="300" height="190" patternUnits="userSpaceOnUse" patternTransform="rotate(-18)">` +
        `<text x="26" y="105" font-size="15" fill="rgba(15,23,42,0.07)" font-family="Microsoft YaHei,PingFang SC,sans-serif">${escHtml(state.wmText)}</text>` +
        `</pattern>`;
      const wmRect = document.createElementNS(NS, "rect");
      wmRect.setAttribute("x", 0); wmRect.setAttribute("y", 0);
      wmRect.setAttribute("width", W); wmRect.setAttribute("height", H);
      wmRect.setAttribute("fill", `url(#${wmId})`);
      wmRect.setAttribute("pointer-events", "none");
      svg.insertBefore(defs, svg.firstChild.nextSibling);
      svg.appendChild(wmRect);
    }
    str = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg);
    temp.destroy();
  } catch (e) {
    setStatus("err", ["SVG 导出失败: " + e.message]);
  } finally {
    holder.remove();
  }
  return str ? { str, w: W, h: H } : null;
}

function exportSvg() {
  const out = buildSvgString();
  if (!out) return;
  downloadBlob(new Blob([out.str], { type: "image/svg+xml;charset=utf-8" }), exportFileName("svg"));
  setStatus("ok", [`已下载 SVG 矢量图（${out.w}×${out.h}，可无限放大）`]);
}

function exportPng() {
  const out = buildSvgString();
  if (!out) return;
  const scale = 3; // 3 倍分辨率，打印级清晰度
  const blob = new Blob([out.str], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    const cv = document.createElement("canvas");
    cv.width = out.w * scale;
    cv.height = out.h * scale;
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
    URL.revokeObjectURL(url);
    cv.toBlob((b) => {
      if (b) {
        downloadBlob(b, exportFileName("png"));
        setStatus("ok", [`已下载高清 PNG（${cv.width}×${cv.height}）`]);
      }
    }, "image/png");
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    setStatus("err", ["PNG 导出失败：SVG 光栅化异常"]);
  };
  img.src = url;
}

/* ============================ 事件绑定与初始化 ============================ */

function bindUI() {
  // 方言与引擎
  const ds = $("dialectSelect");
  DIALECTS.forEach(([k, label]) => {
    const o = document.createElement("option");
    o.value = k; o.textContent = label;
    ds.appendChild(o);
  });
  ds.value = state.dialect;
  ds.onchange = () => { state.dialect = ds.value; localStorage.setItem("sv_dialect", ds.value); };

  $("parserSelect").onchange = (e) => (state.parser = e.target.value);

  // 按钮
  $("btnParse").onclick = doParse;
  $("btnFormat").onclick = () => editor && editor.getAction("editor.action.formatDocument").run();
  $("btnClear").onclick = () => editor && editor.setValue("");
  $("btnUpload").onclick = () => $("fileInput").click();
  $("fileInput").onchange = (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { editor.setValue(String(r.result)); setStatus("ok", [`已加载文件: ${f.name}`]); };
    r.readAsText(f, "utf-8");
    e.target.value = "";
  };
  $("sampleSelect").onchange = (e) => {
    if (e.target.value && SAMPLES[e.target.value]) editor.setValue(SAMPLES[e.target.value]);
    e.target.value = "";
  };

  // 编辑器拖放上传
  const panel = $("editorPanel");
  panel.addEventListener("dragover", (e) => e.preventDefault());
  panel.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { editor.setValue(String(r.result)); setStatus("ok", [`已加载文件: ${f.name}`]); };
    r.readAsText(f, "utf-8");
  });

  // 编辑器主题
  const ts = $("themeSelect");
  [["vs-dark", "编辑器: 暗色"], ["vs", "编辑器: 亮色"], ["hc-black", "编辑器: 高对比黑"], ["hc-light", "编辑器: 高对比亮"]].forEach(([v, l]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = l;
    ts.appendChild(o);
  });
  ts.value = state.theme;
  ts.onchange = () => {
    state.theme = ts.value;
    monaco.editor.setTheme(ts.value);
    localStorage.setItem("sv_theme", ts.value);
  };

  // 血缘级别切换
  const setMode = (m) => {
    state.mode = m;
    localStorage.setItem("sv_mode", m);
    $("modeTable").classList.toggle("active", m === "table");
    $("modeField").classList.toggle("active", m === "field");
    renderGraph();
  };
  $("modeTable").onclick = () => setMode("table");
  $("modeField").onclick = () => setMode("field");
  setMode(state.mode);

  // 高亮颜色
  const hl = $("hlColor");
  hl.value = state.hlColor;
  hl.oninput = () => {
    state.hlColor = hl.value;
    localStorage.setItem("sv_hlColor", hl.value);
    if (hlState) { const label = hlState.label; applyHighlight({ edges: [...hlState.edges].map((i) => i.getModel()), nodes: [...hlState.nodes].map((i) => i.getModel().id), label }); }
  };
  $("btnClearHl").onclick = clearHighlight;

  // 水印
  $("wmToggle").onchange = (e) => { state.wmOn = e.target.checked; localStorage.setItem("sv_wmOn", e.target.checked ? "1" : "0"); applyWatermark(); };
  $("wmText").oninput = (e) => { state.wmText = e.target.value; localStorage.setItem("sv_wmText", e.target.value); applyWatermark(); };

  // 缩放
  $("btnZoomIn").onclick = () => graph && graph.zoom(1.25);
  $("btnZoomOut").onclick = () => graph && graph.zoom(0.8);
  $("btnFit").onclick = () => graph && graph.fitView(30);
  $("btnCenter").onclick = () => graph && centerCanvasKeepZoom();
  $("btnDownloadSvg").onclick = exportSvg;
  $("btnDownloadPng").onclick = exportPng;

  // 面板宽度拖拽
  const divider = $("divider");
  let dragging = false;
  divider.addEventListener("mousedown", () => (dragging = true));
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const w = Math.min(window.innerWidth * 0.7, Math.max(320, e.clientX));
    $("editorPanel").style.width = w + "px";
    $("editorPanel").style.maxWidth = "none";
  });
  window.addEventListener("mouseup", () => (dragging = false));

  // AI 状态
  fetch("/api/parsers").then((r) => r.json()).then((d) => {
    const b = $("aiBadge");
    if (d.ai_configured) {
      b.textContent = "AI 兜底: 已配置 ✓";
      b.className = "ai-badge on";
    } else {
      b.textContent = "AI 兜底: 未配置（编辑 backend/ai_config.json 或环境变量 AI_API_KEY）";
      b.className = "ai-badge off";
    }
  }).catch(() => {
    const b = $("aiBadge");
    b.textContent = "后端未连接";
    b.className = "ai-badge off";
  });
}

function init() {
  initMonaco();
  initGraph();
  bindUI();
  applyWatermark();
  updateZoomLabel();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
