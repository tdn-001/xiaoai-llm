#!/bin/sh
# docker-entrypoint.sh
#
# 容器启动脚本：
#   1. 修复 /app/data 目录权限（处理 volume 挂载由 root 创建的场景）
#   2. 切换到 xiaoai 非 root 用户执行 CMD
#
# 使用 root 启动是因为需要 chown 数据卷；如果一开始就用 USER xiaoai，
# 宿主机 mount 进来的 volume 属于 root 时，进程将无法写入。

set -e

DATA_DIR="/app/data"

# 1) 确保数据目录存在并属于 xiaoai:xiaoai
if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
fi
chown -R xiaoai:xiaoai "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX,g+rX "$DATA_DIR" 2>/dev/null || true

# 2) 用 exec 以非 root 用户运行实际命令
exec gosu xiaoai:xiaoai "$@"