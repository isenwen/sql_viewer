# 需联网阶段
# 基础镜像含 JRE，用于支持 Druid 解析引擎（无需 JDK，编译好的 helper jar 已内置）
FROM eclipse-temurin:17-jre

WORKDIR /app

# 装 Python 运行时与 pip
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# pip 源由构建参数 PIP_INDEX_URL 指定，默认官方 pypi.org
# 国内网络慢可在构建时覆盖: --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple

# 先装依赖，充分利用 Docker 层缓存（代码变更不触发重装）
COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 60 --retries 5 \
    -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

# 前端静态库（monaco / g6 / sql-formatter）：仓库已内置则跳过下载，
# 保证从源码包构建时镜像同样完整可用
RUN python scripts/download_vendor.py

ENV HOST=0.0.0.0 \
    PORT=8866 \
    PYTHONUNBUFFERED=1

EXPOSE 8866

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8866/api/health', timeout=4)" || exit 1

CMD ["python", "run.py"]
