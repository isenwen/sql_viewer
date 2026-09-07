# SQL 血缘查看器 (SQL Lineage Viewer)

输入 SQL，即时生成交互式**表级 / 字段级血缘图**。后端采用**多引擎解析链**：`sqllineage → sqlglot → AI 兜底`，一个失败自动换下一个，解析器模块化可扩展。

![架构](https://img.shields.io/badge/backend-FastAPI-blue) ![engine](https://img.shields.io/badge/engine-sqllineage%20%7C%20sqlglot%20%7C%20AI-green)

## 功能特性

**后端**
- 解析链兜底：sqllineage（精确）→ sqlglot（AST 自研提取）→ AI 大模型兜底
- 支持 14 种方言：MySQL / PostgreSQL / Oracle / SQL Server / Hive / Spark / ClickHouse / SQLite / MariaDB / Trino / DuckDB / BigQuery / DB2 / ANSI
- 支持 INSERT SELECT、CTE、JOIN、UNION、子查询、CREATE TABLE/VIEW AS、UPDATE、MERGE、SELECT * 等常见模式
- 解析器模块化：新增解析器只需在 `backend/parsers/` 下加一个文件并注册

**前端**
- Monaco 编辑器：SQL 语法高亮、关键字补全、一键美化（sql-formatter，Shift+Alt+F）、4 种主题切换
- 数据库类型选择、SQL 文件上传（按钮 / 拖拽到编辑器）、内置示例
- 表级血缘 ⇄ 字段级血缘一键切换
- 点击表 / 字段行 / 连线 → 高亮上游下游血缘链路，其余元素淡出
- 高亮颜色可自定义（取色器）
- 画布水印（可开关、可自定义文字）
- 画布拖拽、滚轮/按钮缩放、自适应、视图居中
- 小地图（Minimap）+ 视口拖拽导航
- 血缘图导出：**SVG 矢量图**（无限放大不失真，可在 Office/Figma/浏览器中编辑）或 **3 倍分辨率高清 PNG**；画布水印会一并写入导出文件

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载前端静态库（首次运行前，支持 npmmirror/unpkg/jsdelivr 自动切换）
python scripts/download_vendor.py

# 3. 启动（自动打开浏览器）
python run.py
```

访问 http://127.0.0.1:8866/ ，左侧输入 SQL（或上传文件），点击「解析」（快捷键 Ctrl+Enter）。

Windows 下也可直接双击 `start.bat`。

## 配置 AI 兜底（可选）

AI 解析器默认关闭。配置任一 OpenAI 兼容接口即可启用：

方式一：编辑 `backend/ai_config.json`

```json
{
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "你的APIKey",
    "model": "glm-4-flash",
    "timeout": 60
}
```

方式二：设置环境变量 `AI_API_KEY`、`AI_API_BASE`、`AI_MODEL`、`AI_TIMEOUT`。

配置后顶部状态栏显示「AI 兜底: 已配置」。AI 结果仅供参考，界面会附加提示。

## 项目结构

```
viewer/
├── run.py                     # 启动入口（python run.py）
├── start.bat                  # Windows 一键启动
├── requirements.txt
├── backend/
│   ├── app.py                 # FastAPI 服务 + /api/parse 接口 + 静态托管
│   ├── config.py              # AI 配置（env / ai_config.json）
│   ├── dialects.py            # 方言映射表（四引擎字段）
│   └── parsers/
│       ├── __init__.py        # 解析链注册中心（CHAIN_ORDER 定义兜底顺序）
│       ├── base.py            # BaseParser 基类 + 血缘图结构归一化
│       ├── sqllineage_parser.py
│       ├── sqlglot_parser.py
│       └── ai_parser.py
├── static/
│   ├── index.html
│   ├── css/style.css
│   ├── js/app.js              # Monaco + AntV G6 全部交互
│   └── vendor/                # monaco / g6 / sql-formatter（本地化，离线可用）
├── scripts/download_vendor.py # 前端依赖下载脚本（多镜像）
└── test_parsers.py            # 解析器冒烟测试
```

## 新增解析器

1. 在 `backend/parsers/` 下新建 `my_parser.py`：

```python
from .base import BaseParser, normalize_graph

class MyParser(BaseParser):
    name = "my"          # 全局唯一
    label = "我的解析器"

    def parse(self, sql: str, dialect: str) -> dict:
        # 解析失败请 raise ParseError("原因")，解析链会自动跳到下一个引擎
        ...
        return normalize_graph(tables, table_edges, column_edges, warnings)
```

2. 在 `backend/parsers/__init__.py` 注册：

```python
from .my_parser import MyParser
register(MyParser)
CHAIN_ORDER = ["sqllineage", "sqlglot", "my", "ai"]   # 按需插入兜底顺序
```

3. 前端 `static/js/app.js` 的解析器下拉框里加一个 `<option value="my">`（可选）。

## 统一血缘图结构

```json
{
  "tables":       [{"id": "db.tbl", "role": "source|target|intermediate", "columns": ["c1", "c2"]}],
  "table_edges":  [{"source": "db.a", "target": "db.b"}],
  "column_edges": [{"source_table": "db.a", "source_column": "c1", "target_table": "db.b", "target_column": "x"}],
  "warnings":     []
}
```

## 测试

```bash
python test_parsers.py    # 六个典型用例跑一遍解析链
```

## 常见问题

- **端口占用**：`PORT=9000 python run.py` 换端口。
- **离线环境**：`static/vendor` 已本地化，运行时不依赖外网；仅在首次下载依赖库时需要网络。
- **sqllineage 报方言不支持**：自动回退 ANSI 再试；sqlglot 与 AI 使用各自方言名（见 `backend/dialects.py`）。
- **字段级血缘不完整**：`SELECT *` 未知表结构时以 `*` 列表示；AI 兜底可补齐但需人工复核。
