# -*- coding: utf-8 -*-
"""编译 backend/java/DruidLineage.java 并打包为 backend/jars/druid-lineage-helper.jar。

需要 JDK（javac + jar）；仓库已内置编译好的 jar，仅在修改 Java 源码后需要重新执行：
python scripts/build_druid_helper.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAVA_SRC = ROOT / "backend" / "java" / "DruidLineage.java"
JARS_DIR = ROOT / "backend" / "jars"


def find_druid_jar() -> Path:
    jars = sorted(JARS_DIR.glob("druid-*.jar"))
    if not jars:
        sys.exit("未找到 druid jar，请先运行 python scripts/download_druid.py")
    return jars[0]


def main() -> int:
    for tool in ("javac", "jar"):
        if shutil.which(tool) is None:
            sys.exit(f"未找到 {tool}，需要安装 JDK（不仅仅是 JRE）")
    druid_jar = find_druid_jar()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["javac", "-encoding", "UTF-8", "-cp", str(druid_jar), "-d", tmp, str(JAVA_SRC)],
            check=True,
        )
        out_jar = JARS_DIR / "druid-lineage-helper.jar"
        subprocess.run(["jar", "cf", str(out_jar), "-C", tmp, "."], check=True)
        print(f"已生成: {out_jar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
