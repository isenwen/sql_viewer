#!/usr/bin/env bash
# ============================================================
#  SQL 血缘查看器 - CentOS 本机部署 / 启动脚本
#  用法:  bash scripts/deploy.sh [--port 8866]
#  说明: 仅使用本机 python3 运行，不依赖 Docker。
#        本机 Druid 引擎需另装 Java；不装则自动跳过 Druid。
# ============================================================
set -euo pipefail

DEFAULT_PORT=8866
PORT="$DEFAULT_PORT"

# ---------- 解析参数 ----------
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-$DEFAULT_PORT}" ;;
    -h|--help)
      echo "用法: bash scripts/deploy.sh [--port 8866]"
      echo "  --port 8866    监听端口（默认 8866）"
      exit 0 ;;
  esac
done

cd "$(dirname "$0")/.."   # 回到项目根目录
echo "============================================"
echo "  SQL 血缘查看器 - 本机部署启动"
echo "  端口: $PORT"
echo "============================================"

# ---------- 检查 python3 ----------
if ! command -v python3 >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "[错误] 未检测到 python3/python。请安装: yum install -y python3 python3-pip"
    exit 1
  fi
else
  PY=python3
fi

# ---------- 1. 前端静态库 ----------
echo "[1/4] 检查前端静态库 vendor（离线可用，缺失则联网下载）..."
if [ -f static/vendor/g6/g6.min.js ]; then
  echo "      vendor 已存在，跳过。"
else
  "$PY" scripts/download_vendor.py
fi

# ---------- 2. Python 依赖 ----------
echo "[2/4] 检查 Python 依赖..."
if ! "$PY" - <<'PY' >/dev/null 2>&1
import importlib.util
for m in ("fastapi","uvicorn","sqllineage","sqlglot","requests","pydantic"):
    if importlib.util.find_spec(m) is None:
        raise SystemExit(1)
PY
then
  echo "      安装依赖 requirements.txt ..."
  "$PY" -m pip install -r requirements.txt
else
  echo "      依赖已安装。"
fi

# ---------- 3. 检查 Druid 依赖（可选） ----------
echo "[3/4] 检查 Druid 引擎依赖（可选）..."
if command -v java >/dev/null 2>&1; then
  if ls backend/jars/druid-*.jar >/dev/null 2>&1; then
    echo "      Java + druid jar 就绪，Druid 引擎可用。"
  else
    echo "      缺少 druid jar，运行: $PY scripts/download_druid.py"
  fi
else
  echo "      未检测到 Java，Druid 引擎将不可用（自动模式会跳过，不影响其他引擎）。"
fi

# ---------- 4. 后台启动 ----------
echo "[4/4] 后台启动服务（HOST=0.0.0.0 PORT=$PORT）..."
if [ -f "$PWD/.sv.pid" ] && kill -0 "$(cat "$PWD/.sv.pid" 2>/dev/null)" >/dev/null 2>&1; then
  echo "      服务已在运行（PID=$(cat "$PWD/.sv.pid")），先停止再启动..."
  bash scripts/stop.sh
fi
nohup env HOST=0.0.0.0 PORT="$PORT" "$PY" run.py >> "$PWD/server.log" 2>&1 &
echo $! > "$PWD/.sv.pid"

# ---------- 健康检查 ----------
echo "      等待服务就绪..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "      服务已就绪。"
    echo "      访问地址: http://127.0.0.1:$PORT/  （远程请用 http://<服务器IP>:$PORT/）"
    echo "      查看日志: tail -f server.log"
    echo "      停止服务: bash scripts/stop.sh"
    exit 0
  fi
  sleep 1
done
echo "      [警告] 服务未就绪，请查看 server.log 确认。"
exit 1
