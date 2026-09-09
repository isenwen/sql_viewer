#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS Docker 一键部署 / 启动脚本
#  用法:  bash scripts/deploy.sh [--port 8866]
#  说明: 使用 Docker 构建并部署。容器内 pip 走官方源，
#        不受宿主机被劫持的 pip 全局源影响（如 mirrors.tencentyun.com）。
# ============================================================
set -euo pipefail

APP_NAME="sql-lineage-viewer"
CONTAINER_NAME="sql-lineage-viewer"
DEFAULT_PORT=8866
PORT="$DEFAULT_PORT"

# ---------- 解析参数 ----------
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-$DEFAULT_PORT}" ;;
    -h|--help)
      echo "用法: bash scripts/deploy.sh [--port 8866]"
      echo "  --port 8866    宿主机映射端口（默认 8866）"
      exit 0 ;;
  esac
done

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - Docker 部署启动"
echo "  端口: $PORT"
echo "============================================"

# ---------- 检查 Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  echo "[错误] 未检测到 docker。请先安装并启动 Docker："
  echo "    yum install -y docker    # CentOS"
  echo "    systemctl enable --now docker"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "[错误] Docker 守护进程不可用，请确认已启动: systemctl start docker"
  exit 1
fi
echo "      Docker 就绪。"

# 检测 compose 命令（docker compose 或 docker-compose）
COMPOSE=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif docker-compose version >/dev/null 2>&1; then
  COMPOSE="docker-compose"
fi

# ---------- 1. 构建镜像 ----------
echo "[1/3] 构建镜像 $APP_NAME ..."
# 容器内 pip 用清华源（默认），避免宿主机被劫持的 pip 配置影响构建。
# 如需改用其他源（如官方 pypi.org / 腾讯云公共源），设置环境变量 PIP_INDEX_URL=...
BUILD_ARGS=(--build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}")
if [ "$PORT" = "$DEFAULT_PORT" ] && [ -n "$COMPOSE" ] && [ -f docker-compose.yml ]; then
  $COMPOSE build "${BUILD_ARGS[@]}"
else
  # 自定义端口或无 compose：用 docker build + docker run
  docker build -t "$APP_NAME:latest" "${BUILD_ARGS[@]}" .
fi

# ---------- 2. 启动容器 ----------
echo "[2/3] 启动容器 $CONTAINER_NAME ..."
# 若已存在旧容器，先移除
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

if [ "$PORT" = "$DEFAULT_PORT" ] && [ -n "$COMPOSE" ] && [ -f docker-compose.yml ]; then
  $COMPOSE up -d
else
  # 用临时 compose 覆盖端口映射，其余沿用 docker-compose.yml 配置
  if [ -n "$COMPOSE" ] && [ -f docker-compose.yml ]; then
    $COMPOSE -f - up -d <<EOF
services:
  sql-viewer:
    build: .
    image: $APP_NAME:latest
    container_name: $CONTAINER_NAME
    ports: ["$PORT:8866"]
    restart: unless-stopped
EOF
  else
    docker run -d --name "$CONTAINER_NAME" -p "$PORT:8866" \
      --restart unless-stopped "$APP_NAME:latest"
  fi
fi

# ---------- 3. 健康检查 ----------
echo "[3/3] 等待服务就绪..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "      服务已就绪。"
    echo "      访问地址: http://127.0.0.1:$PORT/  （远程请用 http://<服务器IP>:$PORT/）"
    echo "      查看日志: docker logs -f $CONTAINER_NAME"
    echo "      停止服务: bash scripts/stop.sh"
    exit 0
  fi
  sleep 1
done
echo "      [警告] 服务未就绪，请查看容器日志: docker logs $CONTAINER_NAME"
echo "      或: docker logs -f $CONTAINER_NAME"
exit 1
