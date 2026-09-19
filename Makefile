# xiaoai-llm 构建与运维 Makefile
#
# 用法：
#   make build         构建 xiaoai-llm:latest（境外默认）
#   make build-cn      构建 xiaoai-llm:cn（国内加速源）
#   make run           docker compose up -d
#   make stop          docker compose down
#   make logs          docker compose logs -f
#   make clean         停止容器、删除镜像、清理 dangling
#   make prune         仅清理 dangling / 未使用镜像
#   make rebuild       clean + build（从零开始）

IMAGE  ?= xiaoai-llm
PYPI_MIRROR ?= https://mirrors.aliyun.com/pypi/simple/

.PHONY: build build-cn run stop logs clean prune rebuild

build:
	docker build -t $(IMAGE):latest .
	@$(MAKE) --no-print-directory prune

build-cn:
	docker build \
	  --build-arg CN_MIRROR=1 \
	  --build-arg PIP_INDEX_URL=$(PYPI_MIRROR) \
	  -t $(IMAGE):cn .
	@$(MAKE) --no-print-directory prune

run:
	docker compose up -d

stop:
	docker compose down

logs:
	docker compose logs -f

prune:
	@docker image prune -f

clean:
	docker compose down -v --remove-orphans 2>/dev/null || true
	@docker rmi $(IMAGE):latest $(IMAGE):cn 2>/dev/null || true
	@docker image prune -f

rebuild: clean build