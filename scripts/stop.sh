#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS Docker 一键停止 / 清理脚本
#  用法:  bash scripts/stop.sh
#  功能: 停止并移除 docker compose 服务或容器
# ============================================================
set -uo pipefail

CONTAINER_NAME="sql-lineage-viewer"

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - 停止"
echo "============================================"

if ! command -v docker >/dev/null 2>&1; then
  echo "[警告] 未检测到 docker，可能未用 Docker 部署。"
  exit 0
fi

# 检测 compose 命令
COMPOSE=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif docker-compose version >/dev/null 2>&1; then
  COMPOSE="docker-compose"
fi

# ---------- 1. 优先 compose 服务 ----------
if [ -n "$COMPOSE" ] && [ -f docker-compose.yml ] && [ -n "$($COMPOSE ps -q 2>/dev/null)" ]; then
  echo "[1/2] 检测到 compose 服务，正在停止并移除..."
  $COMPOSE down
  echo "      已停止。"
# ---------- 2. 容器名停止 ----------
elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "[1/2] 检测到容器 $CONTAINER_NAME，正在停止并移除..."
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 && echo "      已停止。"
else
  echo "[1/2] 未检测到运行中的容器/服务。"
fi

echo "--------------------------------------------"
echo "  停止完成。"
echo "  （如需删除镜像: docker rmi sql-lineage-viewer）"
