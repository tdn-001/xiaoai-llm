# syntax=docker/dockerfile:1.7
#
# Build args:
#   CN_MIRROR=1           启用国内 apt 镜像（阿里云）
#   PIP_INDEX_URL=<url>   pip 源地址，默认 https://pypi.org/simple
#
# Examples:
#   docker build -t xiaoai-llm:latest .                                   # 境外默认
#   docker build --build-arg CN_MIRROR=1 --build-arg \
#     PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
#     -t xiaoai-llm:cn .                                                   # 国内构建
#

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base

ARG CN_MIRROR=0
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# 根据 CN_MIRROR 切换 apt 镜像（阿里云 / 默认 deb.debian.org）
RUN if [ "$CN_MIRROR" = "1" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
      fi; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         ca-certificates \
         tzdata \
         curl \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先 COPY 依赖清单以利用 Docker 缓存层
COPY requirements.txt pyproject.toml README.md ./
COPY app ./app
COPY main.py ./

ENV PIP_INDEX_URL=${PIP_INDEX_URL}
RUN pip install --no-cache-dir -r requirements.txt

# 以非 root 用户运行
RUN groupadd --system --gid 1000 xiaoai \
    && useradd --system --uid 1000 --gid xiaoai --create-home --shell /sbin/nologin xiaoai \
    && mkdir -p /app/data \
    && chown -R xiaoai:xiaoai /app
USER xiaoai

ENV XIAOAI_CONFIG=/app/data/config.json
VOLUME ["/app/data"]
EXPOSE 33003

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:33003/health || exit 1

CMD ["python", "main.py"]