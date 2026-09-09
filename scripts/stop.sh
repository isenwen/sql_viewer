#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS 一键停止 / 清理脚本
#  用法:  bash scripts/stop.sh
#  功能: 停止 Docker 容器（若在用）或本机后台进程，并清理临时 pid 文件
# ============================================================
set -uo pipefail

APP_NAME="sql-lineage-viewer"
CONTAINER_NAME="sql-viewer"

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - 停止"
echo "============================================"

# ---------- 1. Docker 方式 ----------
HAVE_DOCKER=0
command -v docker >/dev/null 2>&1 && HAVE_DOCKER=1

if [ "$HAVE_DOCKER" -eq 1 ]; then
  # 先按容器名判断（deploy.sh 用 docker run --name 启动的情况）
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "[1/2] 检测到容器 $CONTAINER_NAME，正在停止并删除..."
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 && echo "      已停止 $CONTAINER_NAME。"
  # 再尝试 docker compose（deploy.sh 用 compose 启动的情况）
  elif [ -f docker-compose.yml ] && docker compose ps -q >/dev/null 2>&1 && [ -n "$(docker compose ps -q 2>/dev/null)" ]; then
    echo "[1/2] 检测到 Docker compose 服务，正在停止..."
    docker compose down
  else
    echo "[1/2] 未检测到运行中的 Docker 容器/服务。"
  fi
else
  echo "[1/2] 未检测到 docker，跳过 Docker 清理。"
fi

# ---------- 2. 本机 Python 进程 ----------
PID_FILE="$PWD/.sv.pid"
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "${PID:-}" ] && kill -0 "$PID" >/dev/null 2>&1; then
    echo "[2/2] 正在停止本机服务进程 PID=$PID ..."
    kill "$PID" >/dev/null 2>&1 || true
    # 等待退出
    for _ in $(seq 1 10); do
      kill -0 "$PID" >/dev/null 2>&1 || break
      sleep 1
    done
    kill -9 "$PID" >/dev/null 2>&1 || true
    echo "      已停止。"
  else
    echo "[2/2] 本机进程不存在或已退出。"
  fi
  rm -f "$PID_FILE"
else
  echo "[2/2] 未找到本机进程 pid 文件（可能未用 deploy.sh 本机模式启动）。"
fi

echo "--------------------------------------------"
echo "  停止完成。日志保留在 server.log（如需清空自行删除）。"
echo "  （若要删除镜像: docker rmi $APP_NAME）"
