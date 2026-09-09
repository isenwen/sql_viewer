#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS 一键部署 / 启动脚本
#  用法:  bash scripts/deploy.sh [--port 8866] [--no-docker]
#  默认: 优先使用 Docker 部署；无 Docker 时自动回退本机 python 运行
# ============================================================
set -euo pipefail

APP_NAME="sql-lineage-viewer"
CONTAINER_NAME="sql-viewer"
DEFAULT_PORT=8866
PORT="$DEFAULT_PORT"
FORCE_LOCAL=0

# ---------- 解析参数 ----------
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-$DEFAULT_PORT}" ;;
    --no-docker) FORCE_LOCAL=1 ;;
    -h|--help)
      echo "用法: bash scripts/deploy.sh [--port 8866] [--no-docker]"
      echo "  --port 8866    映射/监听端口（默认 8866）"
      echo "  --no-docker    强制使用本机 python 运行，不使用 Docker"
      exit 0 ;;
  esac
done

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - 部署启动"
echo "  端口: $PORT"
echo "============================================"

# ---------- 检测 Docker 是否可用 ----------
HAVE_DOCKER=0
if [ "$FORCE_LOCAL" -eq 0 ]; then
  if command -v docker >/dev/null 2>&1; then
    HAVE_DOCKER=1
  else
    echo "[提示] 未检测到 docker 命令，将回退到本机 python 运行。"
  fi
fi

# ---------- 前置：下载 / 校验依赖文件（无论哪种方式都需要） ----------
ensure_vendor() {
  echo "[1/4] 检查前端静态库 vendor（离线可用，缺失则联网下载）..."
  if [ -f static/vendor/g6/g6.min.js ]; then
    echo "      vendor 已存在，跳过。"
  else
    python3 scripts/download_vendor.py || python scripts/download_vendor.py
  fi
}
# Python 可用性
HAVE_PYTHON=0
command -v python3 >/dev/null 2>&1 && HAVE_PYTHON=1 || { command -v python >/dev/null 2>&1 && HAVE_PYTHON=1 || HAVE_PYTHON=0; }

# ---------- 模式一：Docker ----------
run_docker() {
  echo "[2/4] 使用 Docker 构建并启动..."
  if [ -f docker-compose.yml ]; then
    if [ "$PORT" = "$DEFAULT_PORT" ]; then
      # 默认端口，直接复用 compose
      docker compose up -d --build
    else
      # 自定义端口：用 docker run 显式映射，保证 HOST 下可访问
      echo "      自定义端口 $PORT，用 docker run 启动..."
      docker build -t "$APP_NAME:latest" .
      docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
      docker run -d --name "$CONTAINER_NAME" -p "$PORT:8866" \
        --restart unless-stopped "$APP_NAME:latest"
    fi
  else
    echo "      未找到 docker-compose.yml，用 docker run 启动..."
    docker build -t "$APP_NAME:latest" .
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER_NAME" -p "$PORT:8866" \
      --restart unless-stopped "$APP_NAME:latest"
  fi
  echo "[3/4] 等待服务就绪..."
  wait_healthy "http://127.0.0.1:$PORT/api/health"
  echo "[4/4] 完成。访问地址: http://127.0.0.1:$PORT/  （远程服务器请用 http://<服务器IP>:$PORT/）"
  show_logs_hint "docker logs -f $CONTAINER_NAME"
}

# ---------- 模式二：本机 Python ----------
run_local() {
  if [ "$HAVE_PYTHON" -eq 0 ]; then
    echo "[错误] 未检测到 python3/python，且未使用 Docker。请安装 Docker 或 python3。"
    exit 1
  fi
  echo "[2/4] 检查 Python 依赖..."
  python3 - <<'PY' || true
import importlib.util
for m in ("fastapi","uvicorn","sqllineage","sqlglot","requests","pydantic"):
    if importlib.util.find_spec(m) is None:
        raise SystemExit("缺依赖")
PY
  if [ $? -ne 0 ]; then
    echo "      安装依赖 requirements.txt ..."
    python3 -m pip install -r requirements.txt || python -m pip install -r requirements.txt
  else
    echo "      依赖已安装。"
  fi
  echo "[3/4] 后台启动服务（HOST=0.0.0.0 PORT=$PORT）..."
  nohup env HOST=0.0.0.0 PORT="$PORT" python3 run.py >> "$PWD/server.log" 2>&1 &
  echo $! > "$PWD/.sv.pid"
  echo "[4/4] 等待服务就绪..."
  wait_healthy "http://127.0.0.1:$PORT/api/health"
  echo "      完成。访问地址: http://127.0.0.1:$PORT/  （远程请用 http://<服务器IP>:$PORT/）"
  show_logs_hint "tail -f server.log"
}

# ---------- 健康检查 ----------
wait_healthy() {
  local url="$1"
  for i in $(seq 1 30); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "      服务已就绪（第 $i 次探测）。"
      return 0
    fi
    sleep 1
  done
  echo "      [警告] 服务未就绪，请查看日志确认。"
  return 1
}

show_logs_hint() {
  echo "  查看日志: $1"
  echo "  停止服务: bash scripts/stop.sh"
}

# ---------- 执行 ----------
ensure_vendor
if [ "$HAVE_DOCKER" -eq 1 ]; then
  run_docker
else
  run_local
fi
