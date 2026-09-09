#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS 本机一键停止 / 清理脚本
#  用法:  bash scripts/stop.sh
#  功能: 停止 deploy.sh 启动的本机后台进程，并清理临时 pid 文件
# ============================================================
set -uo pipefail

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - 停止"
echo "============================================"

# ---------- 1. 按 pid 文件停止（deploy.sh 记录） ----------
PID_FILE="$PWD/.sv.pid"
stopped=0

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "${PID:-}" ] && kill -0 "$PID" >/dev/null 2>&1; then
    echo "[1/2] 正在停止服务进程 PID=$PID ..."
    kill "$PID" >/dev/null 2>&1 || true
    # 等待退出，最多 10s
    for _ in $(seq 1 10); do
      kill -0 "$PID" >/dev/null 2>&1 || break
      sleep 1
    done
    kill -9 "$PID" >/dev/null 2>&1 || true
    echo "      已停止。"
    stopped=1
  else
    echo "[1/2] pid 文件中记录进程不存在或已退出。"
  fi
  rm -f "$PID_FILE"
else
  echo "[1/2] 未找到 pid 文件。"
fi

# ---------- 2. 兜底：按进程名查找残留的 run.py ----------
if [ "$stopped" -eq 0 ]; then
  echo "[2/2] 按进程名兜底查找 run.py 残留进程..."
  # 匹配 run.py 的 python 进程（排除 grep/本脚本自身）
  PIDS=$(pgrep -f 'run\.py' 2>/dev/null || true)
  if [ -n "${PIDS:-}" ]; then
    for p in $PIDS; do
      kill "$p" >/dev/null 2>&1 || true
    done
    sleep 1
    kill -9 $PIDS >/dev/null 2>&1 || true
    echo "      已停止进程: $PIDS"
  else
    echo "      未发现残留的服务进程。"
  fi
fi

echo "--------------------------------------------"
echo "  停止完成。日志保留在 server.log（如需清空自行删除）。"
