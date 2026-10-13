#!/bin/sh
# 循环：先跑一次融合 -> serve 产物(:8000) -> 每天定时重融合
set -e
cd /srv

merge_once() {
  echo "[$(date '+%F %T')] merging ..."
  MERGE_OUT_DIR=/srv/out python merge.py || echo "merge failed (kept last good output)"
}

# 启动先融合一次（产物写 /srv/out）
mkdir -p out
merge_once

# 文件服务后台常驻（serve 融合产物目录）
cd out && python -m http.server 8000 --bind 0.0.0.0 &
SERVER_PID=$!
cd /srv

# 每天重融合（间隔可由 MERGE_INTERVAL 环境变量调整，默认 86400 秒）
while true; do
  sleep "$MERGE_INTERVAL"
  merge_once
done
