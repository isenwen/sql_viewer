# -*- coding: utf-8 -*-
"""下载前端静态依赖库到 static/vendor/，多镜像自动回退。"""
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

VENDOR = Path(__file__).resolve().parent.parent / "static" / "vendor"

# (包名, 版本, 包内路径, 本地相对路径)
FILES = [
    ("monaco-editor", "0.45.0", "min/vs/loader.js", "monaco/vs/loader.js"),
    ("monaco-editor", "0.45.0", "min/vs/editor/editor.main.js", "monaco/vs/editor/editor.main.js"),
    ("monaco-editor", "0.45.0", "min/vs/editor/editor.main.css", "monaco/vs/editor/editor.main.css"),
    ("monaco-editor", "0.45.0", "min/vs/editor/editor.main.nls.js", "monaco/vs/editor/editor.main.nls.js"),
    ("monaco-editor", "0.45.0", "min/vs/base/worker/workerMain.js", "monaco/vs/base/worker/workerMain.js"),
    ("monaco-editor", "0.45.0", "min/vs/base/browser/ui/codicons/codicon/codicon.ttf", "monaco/vs/base/browser/ui/codicons/codicon/codicon.ttf"),
    ("@antv/g6", "4.8.25", "dist/g6.min.js", "g6/g6.min.js"),
    ("sql-formatter", "15.4.5", "dist/sql-formatter.min.js", "sql-formatter/sql-formatter.min.js"),
]

MIRRORS = [
    "https://registry.npmmirror.com/{pkg}/{ver}/files/{path}",
    "https://unpkg.com/{pkg}@{ver}/{path}",
    "https://cdn.jsdelivr.net/npm/{pkg}@{ver}/{path}",
]


def fetch(pkg, ver, path, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        print(f"已存在，跳过: {dest.relative_to(VENDOR.parent)}")
        return True
    for tpl in MIRRORS:
        url = tpl.format(pkg=pkg, ver=ver, path=path)
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 100:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                print(f"下载成功 [{r.status_code}] {len(r.content)//1024}KB: {dest.relative_to(VENDOR.parent)}  <- {url}")
                return True
            print(f"  镜像返回 {r.status_code}: {url}")
        except Exception as e:
            print(f"  镜像失败: {url} ({type(e).__name__})")
    print(f"!! 全部镜像失败: {pkg} {path}")
    return False


ok = all(fetch(f[0], f[1], f[2], VENDOR / f[3]) for f in FILES)
sys.exit(0 if ok else 1)
