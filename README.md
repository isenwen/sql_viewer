# SQL / DataX 血缘查看器 (SQL & DataX Lineage Viewer)

输入 **SQL 或 DataX 同步任务 JSON**，即时生成交互式**表级 / 字段级血缘图**。后端采用**多引擎解析链**：`DataX(JSON) → sqllineage → sqlglot → Druid → AI 兜底`，自动识别输入类型，一个失败自动换下一个，解析器模块化可扩展。

![架构](https://img.shields.io/badge/backend-FastAPI-blue) ![engine](https://img.shields.io/badge/engine-DataX%20%7C%20sqllineage%20%7C%20sqlglot%20%7C%20Druid%20%7C%20AI-green) ![deploy](https://img.shields.io/badge/deploy-Docker-2496ED)

## 功能特性

**后端**
- 解析链兜底：DataX(JSON) → sqllineage（精确）→ sqlglot（AST 自研提取）→ **Druid（阿里 Java SQL Parser）**→ AI 大模型兜底
- **DataX 同步任务解析**（详见下文）
- **Druid 解析引擎**（详见下文）
- 支持 14 种方言：MySQL / PostgreSQL / Oracle / SQL Server / Hive / Spark / ClickHouse / SQLite / MariaDB / Trino / DuckDB / BigQuery / DB2 / ANSI
- 支持 INSERT SELECT、CTE、JOIN、UNION、子查询、CREATE TABLE/VIEW AS、UPDATE、MERGE、SELECT * 等常见模式
- 解析器模块化：新增解析器只需在 `backend/parsers/` 下加一个文件并注册

**DataX 解析能力**
- 自动识别 DataX JSON 配置（`job.content` 结构），支持多个 content 并发组
- 宽容解析线上配置：支持 `//` 与 `/* */` 注释、尾逗号、单引号字符串
- 表提取：`connection[].table`、`collection / index` 等插件参数、HDFS 路径自动解析 Hive 库表（`/user/hive/warehouse/db.db/table`）；从 `jdbcUrl` 提取库名生成 `库.表` 限定名
- 字段级血缘：`reader.column` 与 `writer.column` 按位置对齐（含 hdfswriter 的 `{name, type}` 对象写法）；`querySql` 交给 sqlglot 分析，JOIN / 别名 / `${调度变量}` 都能正确提取真实来源表与字段映射
- `*` 通配字段自动降级为表级血缘 + 警告提示（暂不分析 preSql / postSql 中的语句）

**前端**
- Monaco 编辑器：SQL / JSON 语法高亮（按内容自动切换）、关键字补全、一键美化（Shift+Alt+F，SQL 走 sql-formatter、JSON 走内置格式化）、4 种主题切换
- 数据库类型选择、文件上传（.sql / .json，按钮或拖拽到编辑器）、内置 SQL 与 DataX 示例
- **网页端 AI 配置**：AI 兜底的 Base URL / API Key / 模型 / 超时可在页面「AI 配置」对话框填写，仅存浏览器 **sessionStorage**（当前会话有效，刷新即失效），服务端不落盘；保存前可一键「测试连接」
- 表级血缘 ⇄ 字段级血缘一键切换
- 点击表 / 字段行 / 连线 → 高亮上游下游血缘链路，其余元素淡出
- 高亮颜色可自定义（取色器）
- 画布水印（可开关、可自定义文字）
- 画布拖拽、滚轮/按钮缩放、自适应、视图居中
- 小地图（Minimap）+ 视口拖拽导航
- 血缘图导出：**SVG 矢量图**（无限放大不失真，可在 Office/Figma/浏览器中编辑）或 **3 倍分辨率高清 PNG**；画布水印会一并写入导出文件

## 快速开始

> 想用 Docker 一步部署？直接参考下方 **[Docker 部署](#docker-部署推荐)** 一节即可。

### 本地运行（需 Python 3.10+）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载前端静态库（首次运行前，支持 npmmirror/unpkg/jsdelivr 自动切换）
python scripts/download_vendor.py

# 3. 下载 Druid jar（首次运行 Druid 引擎前；已随仓库内置则跳过）
python scripts/download_druid.py

# 4. 启动（自动打开浏览器）
python run.py
```

访问 http://127.0.0.1:8866/ ，左侧输入 SQL / DataX JSON（或上传文件），点击「解析」（快捷键 Ctrl+Enter）。

Windows 下也可直接双击 `start.bat`。

## 解析 DataX 任务

直接把 DataX 作业 JSON 粘贴到编辑器（或选择示例「DataX 同步任务示例」），点「解析」即可：

- 自动模式下无需切换引擎；也可在引擎下拉框显式选择「DataX (JSON)」
- 血缘方向为 `reader 来源表 → writer 目标表`，字段级血缘按 `reader.column[i] → writer.column[i]` 位置对齐
- `querySql` 中的 JOIN 会被 sqlglot 完整分析：`SELECT o.id AS order_id, u.name FROM orders o JOIN users u ...` 会生成
  `orders.id → 目标.字段`、`users.name → 目标.字段` 两条字段边

## 解析引擎说明

- **Druid 引擎**：基于阿里 Druid SQL Parser（Java），覆盖 INSERT / CREATE TABLE AS / UPDATE / MERGE / CTE / JOIN / 子查询等。需要本机有 **Java 8+**，且 `backend/jars/` 下有 `druid-*.jar`。
  - 首次使用运行 `python scripts/download_druid.py` 下载 druid jar（仓库已内置编译好的 `druid-lineage-helper.jar`）
  - 修改了 `backend/java/DruidLineage.java` 后运行 `python scripts/build_druid_helper.py` 重新编译打包
  - 引擎不可用时前端「Druid」选项会标注「不可用」并禁用，自动模式直接跳过
- **引擎优先级（自动模式）**：DataX JSON → sqllineage → sqlglot → Druid → AI。也可在引擎下拉框显式指定，或逐个「解析」观察哪个引擎识别得最准。
- **AI 兜底**：其余引擎全部失败后兜底，结果仅供参考。可在网页「AI 配置」填入 OpenAI 兼容接口的 Base URL / API Key / 模型名，**仅当前浏览器会话有效**（存 sessionStorage，刷新即失效，不写入服务端）。

## 配置 AI 兜底

**方式一（推荐，仅当前浏览器会话）**：点击页面右上角「AI 配置」，填写 Base URL / API Key / 模型名 / 超时，点「测试连接」验证后保存。配置只保存在当前浏览器 tab 的 sessionStorage 中，关闭标签页或刷新即失效，服务端不落盘、不会被持久化。

**方式二（全局）**：编辑 `backend/ai_config.json` 或设置环境变量 `AI_API_KEY`、`AI_API_BASE`、`AI_MODEL`、`AI_TIMEOUT`（见下文）。

配置后顶部状态栏显示「AI 兜底: 已配置」。AI 结果仅供参考，界面会附加提示。

## 全局配置（环境变量 / ai_config.json）

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

## Docker 部署（推荐）

服务端无状态、不依赖外网（前端静态库与 Druid jar 已内置于镜像）。**推荐用 Docker 部署**，容器内 pip 走官方源，不受宿主机可能被劫持/过期的 pip 全局源影响（如腾讯云 CVM 的 `mirrors.tencentyun.com`）。

> 服务器（如 CentOS）部署：直接用项目内置的 **[一键脚本](#centos-一键部署脚本)** 最省事——自动构建、启动、健康检查，停止也有对应脚本。

### 方式一：docker compose（最简单）

前提：已安装 Docker Desktop（Windows / macOS）或 Docker Engine（Linux）。

```bash
# 克隆代码
git clone https://github.com/isenwen/sql_viewer.git
cd sql_viewer

# 首次：构建并后台启动
docker compose up -d --build

# 查看日志
docker compose logs -f
```

访问 http://127.0.0.1:8866/ （服务器上则用 http://<服务器IP>:8866/）。

### 方式二：docker 命令行

```bash
# 构建镜像
docker build -t sql-lineage-viewer .

# 运行容器（-d 后台，-p 映射端口，--name 命名）
docker run -d -p 8866:8866 --name sql-viewer sql-lineage-viewer
```

### 常用操作

| 操作 | 命令 |
| --- | --- |
| 后台启动 | `docker compose up -d` |
| 查看日志 | `docker compose logs -f` |
| 停止 | `docker compose down` |
| 重建（升级镜像） | `docker compose up -d --build` |
| 换端口 | 修改 compose 的 `"8866:8866"` 左侧为宿主机端口，如 `"9000:8866"` |
| 指定宿主 IP 监听 | 端口可写成 `"0.0.0.0:8866:8866"` 暴露到局域网 |

### 配置项

**端口**：compose 中 `ports: - "8866:8866"`，左边是宿主机端口，右边是容器内端口（固定 8866）。改左值即可换端口。

**Druid 引擎**：无需配置，基础镜像 `eclipse-temurin:17-jre` 自带 JRE，开箱即用。

**AI 兜底（可选，二选一）**：
- 环境变量方式：在 `docker-compose.yml` 的 `services.sql-viewer.environment` 下取消注释并填入：
  ```yaml
  environment:
    AI_API_KEY: "你的APIKey"
    AI_API_BASE: "https://open.bigmodel.cn/api/paas/v4"
    AI_MODEL: "glm-4-flash"
    AI_TIMEOUT: "60"
  ```
- 挂载配置文件：把本机 `backend/ai_config.json` 挂进容器：
  ```yaml
  volumes:
    - ./backend/ai_config.json:/app/backend/ai_config.json:ro
  ```
  也可直接在网页右上角「AI 配置」填入，仅当前浏览器会话有效，无需改容器。

### 停止 / 卸载

```bash
docker compose down        # 停止并删除容器（不删镜像）
docker rmi sql-lineage-viewer   # 删除镜像
```

### 说明

- 基础镜像为 `eclipse-temurin:17-jre`（自带 JRE），Druid 引擎开箱即用，无需额外安装 Java。
- 数据无状态：服务不存任何数据，重建镜像即可升级。
- 健康检查：`GET /api/health`，镜像已内置 HEALTHCHECK。
- 连不上：先 `docker compose logs -f` 看日志，再确认端口映射与宿主机防火墙。

## CentOS 一键部署脚本

面向服务器（CentOS / RHEL 等 Linux）场景，项目内置两个 bash 脚本，**基于 Docker 部署**，自动完成构建、启动与健康检查、停止清理。容器内 pip 走官方源，规避宿主机 pip 源被劫持/过期的问题。

### 启动

```bash
# 克隆代码（已克隆可跳过）
git clone https://github.com/isenwen/sql_viewer.git
cd sql_viewer

# 一键部署（Docker 构建 + 启动，自动健康检查）
bash scripts/deploy.sh
```

自定义端口：

```bash
bash scripts/deploy.sh --port 9000        # 映射到 9000 端口
bash scripts/deploy.sh --help             # 查看所有参数
```

脚本会：检查 Docker 是否可用 → `docker compose build`（或 `docker build`）构建镜像 → 启动容器 → 轮询 `GET /api/health` 等待就绪 → 打印访问地址与日志命令。

如需在构建时指定 pip 源（容器内默认 `pypi.org`，若太慢可用清华）：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/deploy.sh
```

### 停止

```bash
bash scripts/stop.sh
```

检测并停止 Docker compose 服务或容器，并移除。

### 说明

- **权限**：脚本已带可执行位（`100755`），克隆后可直接 `bash scripts/xxx.sh` 运行；也可手动 `chmod +x`。
- **换行**：脚本为 LF 格式，兼容 Linux 终端。
- **依赖**：需已安装 Docker，且守护进程已启动。CentOS：`yum install -y docker && systemctl enable --now docker`。健康检查用 `curl`（CentOS 缺则 `yum install -y curl`）。
- **查看日志**：`docker logs -f sql-lineage-viewer`。

## 项目结构

```
viewer/
├── run.py                     # 启动入口（python run.py）
├── start.bat                  # Windows 一键启动
├── requirements.txt
├── backend/
│   ├── app.py                 # FastAPI 服务 + /api/parse 接口 + 静态托管
│   ├── config.py              # AI 配置（env / ai_config.json）
│   ├── dialects.py            # 方言映射表（五引擎字段）
│   ├── java/DruidLineage.java # Druid AST → 血缘 JSON 的 Java 帮助类
│   ├── jars/                  # druid-*.jar + druid-lineage-helper.jar
│   └── parsers/
│       ├── __init__.py        # 解析链注册中心（CHAIN_ORDER 定义兜底顺序）
│       ├── base.py            # BaseParser 基类 + 血缘图结构归一化
│       ├── datax_parser.py    # DataX JSON 解析器（querySql 复用 sqlglot 分析）
│       ├── sqllineage_parser.py
│       ├── sqlglot_parser.py
│       ├── druid_parser.py    # Druid 引擎（子进程调用 Java）
│       └── ai_parser.py
├── static/
│   ├── index.html
│   ├── css/style.css
│   ├── js/app.js              # Monaco + AntV G6 全部交互
│   └── vendor/                # monaco / g6 / sql-formatter（本地化，离线可用）
├── scripts/
│   ├── download_vendor.py     # 前端依赖下载脚本（多镜像）
│   ├── download_druid.py      # 下载 druid jar
│   ├── build_druid_helper.py  # 编译打包 DruidLineage 帮助类
│   ├── deploy.sh              # CentOS 一键部署/启动（Docker 构建 + 启动）
│   └── stop.sh                # CentOS 一键停止/清理（Docker）
├── Dockerfile                 # Docker 镜像构建（eclipse-temurin 17-jre + Python）
├── docker-compose.yml         # 一键部署（端口 / AI 配置）
├── .dockerignore
└── test_parsers.py            # 解析器冒烟测试（含 DataX / Druid 用例）
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
CHAIN_ORDER = ["datax", "sqllineage", "sqlglot", "druid", "my", "ai"]   # 按需插入兜底顺序
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
python test_parsers.py    # 典型 SQL、DataX 与 Druid 用例跑一遍解析链
```

## 常见问题

- **端口占用**：`PORT=9000 python run.py` 换端口；Docker 下改 compose 的 `"8866:8866"` 左值。
- **离线环境**：`static/vendor` 已本地化，运行时不依赖外网；仅在首次下载依赖库时需要网络。
- **sqllineage 报方言不支持**：自动回退 ANSI 再试；sqlglot 与 AI 使用各自方言名（见 `backend/dialects.py`）。
- **Druid 引擎不可用**：前端「Druid」下拉标注「不可用」并禁用。需本机安装 Java 8+，并确认 `backend/jars/` 下有 druid jar（运行 `python scripts/download_druid.py`）；不可用时自动模式会静默跳过，不影响其他引擎。Docker 镜像内置 JRE，无需处理。
- **Docker 连不上**：先 `docker compose logs -f` 看日志；确认端口映射与宿主机防火墙；浏览器用 `http://<服务器IP>:8866/` 而非 `127.0.0.1`（若容器在远程服务器）。
- **Docker 镜像构建慢**：首次需联网拉 `eclipse-temurin:17-jre` 基础镜像与 pip 依赖，之后有缓存；若构建时 `download_vendor.py` 需联网，可先保证网络或手动跑一次。
- **字段级血缘不完整**：`SELECT *` 未知表结构时以 `*` 列表示；AI 兜底可补齐但需人工复核。
- **DataX 字段级血缘缺失**：检查 `reader.column` / `writer.column` 是否配置且数量一致；`*` 通配或 `querySql` 解析失败时会降级为仅表级血缘并在警告中说明。
