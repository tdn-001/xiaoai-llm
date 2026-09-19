# syntax=docker/dockerfile:1.7
#
# 多阶段构建：
#   stage 1 (builder) - 安装依赖到 /install 目录
#   stage 2 (runtime) - 仅复制 /install + 应用代码，体积更小、更安全
#
# Build args:
#   CN_MIRROR=1            启用国内 apt 镜像（阿里云）
#   PIP_INDEX_URL=<url>    pip 源地址，默认 https://pypi.org/simple
#
# Examples:
#   docker build -t xiaoai-llm:latest .                                   # 境外默认
#   docker build --build-arg CN_MIRROR=1 --build-arg \
#     PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
#     -t xiaoai-llm:cn .                                                   # 国内构建
#

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# Stage 1: builder —— 装依赖到 /install 目录
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ARG CN_MIRROR=0
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFIX=/install

# 国内 apt 镜像（阿里云）
RUN if [ "$CN_MIRROR" = "1" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
      fi; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ca-certificates \
         gcc \
         libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt ./

ENV PIP_INDEX_URL=${PIP_INDEX_URL}
RUN pip install --no-cache-dir -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2: runtime —— 仅运行时所需
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG CN_MIRROR=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 国内 apt 镜像（阿里云）
RUN if [ "$CN_MIRROR" = "1" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
      fi; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ca-certificates \
         tzdata \
         curl \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 复制 builder 阶段安装好的 site-packages
COPY --from=builder /install /usr/local

WORKDIR /app

# 应用代码
COPY app ./app
COPY main.py ./

# 非 root 用户
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