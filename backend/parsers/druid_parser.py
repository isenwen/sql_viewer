"""Druid 解析引擎：通过子进程调用阿里 Druid SQL Parser（Java）提取血缘。

运行要求：
- Java 运行时 8+ 在 PATH 中（或环境变量 SQL_VIEWER_JAVA 指向 java 可执行文件）
- backend/jars/ 下有 druid jar 与 druid-lineage-helper.jar
  （缺失时运行 python scripts/download_druid.py 下载 druid，
   helper jar 由 scripts/build_druid_helper.py 编译打包，仓库已内置）

架构说明：Java 侧 backend/java/DruidLineage.java 负责 AST 遍历与血缘提取，
stdout 输出统一结构 JSON，Python 侧做归一化。子进程隔离保证 JVM 异常不影响服务，
且兼容 Java 8（JPype 需要 Java 9+，故不用进程内方案）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..dialects import get_dialect
from .base import BaseParser, ParseError, normalize_graph

BACKEND_DIR = Path(__file__).resolve().parent.parent
JARS_DIR = BACKEND_DIR / "jars"
TIMEOUT_SECONDS = 60


def java_executable() -> str | None:
    return os.environ.get("SQL_VIEWER_JAVA") or shutil.which("java")


def jars_ready() -> bool:
    try:
        return any(JARS_DIR.glob("*.jar"))
    except OSError:
        return False


class DruidParser(BaseParser):
    name = "druid"
    label = "Druid"

    @classmethod
    def is_available(cls) -> bool:
        return bool(java_executable()) and jars_ready()

    def parse(self, sql: str, dialect: str, options: dict | None = None) -> dict:
        java = java_executable()
        if not java:
            raise ParseError(
                "未找到 Java 运行时（Druid 引擎需要 Java 8+）。"
                "请安装 Java 或设置环境变量 SQL_VIEWER_JAVA 指向 java 可执行文件"
            )
        if not jars_ready():
            raise ParseError("缺少 druid jar，请运行 python scripts/download_druid.py 安装")

        db_type = get_dialect(dialect)["druid"] or "mysql"
        try:
            proc = subprocess.run(
                [java, "-Dfile.encoding=UTF-8", "-cp", str(JARS_DIR / "*"), "DruidLineage", db_type],
                input=sql.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise ParseError(f"Druid 解析超时（>{TIMEOUT_SECONDS}s）") from e
        except OSError as e:
            raise ParseError(f"启动 Java 进程失败: {e}") from e

        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise ParseError(f"Druid 引擎异常退出（code={proc.returncode}）: {detail[-300:]}")

        stdout = proc.stdout.decode("utf-8", errors="replace")
        line = next((ln for ln in reversed(stdout.splitlines()) if ln.startswith("{")), None)
        if line is None:
            detail = (proc.stderr or stdout).decode("utf-8", errors="replace").strip()
            raise ParseError(f"Druid 引擎无有效输出: {detail[-300:]}")
        try:
            data = json.loads(line)
        except Exception as e:
            raise ParseError(f"Druid 引擎输出无法解析: {e}") from e
        if "error" in data:
            raise ParseError(str(data["error"]))

        return normalize_graph(
            [(t["id"], t["role"]) for t in data.get("tables", [])],
            data.get("table_edges") or [],
            data.get("column_edges") or [],
            data.get("warnings") or [],
        )
