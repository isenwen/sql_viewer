# -*- coding: utf-8 -*-
"""下载 Alibaba Druid SQL Parser jar 到 backend/jars/，供 Druid 解析引擎（JPype）使用。

用法: python scripts/download_druid.py
多版本/多镜像自动回退；已存在则跳过。
"""
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

JARS = Path(__file__).resolve().parent.parent / "backend" / "jars"

# 依次尝试的版本（新版优先）
VERSIONS = ["1.2.23", "1.2.21", "1.2.16"]
MIRRORS = [
    "https://maven.aliyun.com/repository/public/com/alibaba/druid/{ver}/druid-{ver}.jar",
    "https://repo1.maven.org/maven2/com/alibaba/druid/{ver}/druid-{ver}.jar",
]


def main() -> int:
    for ver in VERSIONS:
        dest = JARS / f"druid-{ver}.jar"
        if dest.exists() and dest.stat().st_size > 1_000_000:
            print(f"已存在，跳过: {dest}")
            return 0
        for tpl in MIRRORS:
            url = tpl.format(ver=ver)
            try:
                print(f"下载 {url} ...")
                r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and len(r.content) > 1_000_000:
                    JARS.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)
                    print(f"已保存: {dest} ({len(r.content) // 1024} KB)")
                    return 0
                print(f"  失败: HTTP {r.status_code}, {len(r.content)} bytes")
            except Exception as e:
                print(f"  失败: {e}")
    print("所有镜像均下载失败，请手动从 https://repo1.maven.org/maven2/com/alibaba/druid/ "
          "下载 druid jar 放入 backend/jars/")
    return 1


if __name__ == "__main__":
    sys.exit(main())
