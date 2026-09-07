# -*- coding: utf-8 -*-
"""SQL 血缘查看器启动入口：python run.py"""
import os
import webbrowser
import threading

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8866"))
    url = f"http://{host}:{port}"

    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Timer(1.2, _open).start()
    uvicorn.run("backend.app:app", host=host, port=port, log_level="info")
