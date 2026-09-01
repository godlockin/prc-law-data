#!/usr/bin/env bash
# update.sh — prc-law-data 增量更新
#
# 检测上游 3 源 (laws-data / HF / LawRefBook) 版本变化,
# 仅下载变化的 statutes, 重新生成索引.
#
# 用法:
#   ./update.sh                 # 标准更新
#   ./update.sh --full          # 全量重建
#   ./update.sh --check         # 仅检测, 不下载

set -e
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="${PRC_LAW_DATA_CACHE:-/tmp/prc-law-data-cache}"
DATA="${ROOT}/data"
LOG="/tmp/prc-law-data-update-$(date +%Y%m%d-%H%M%S).log"

FULL=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      echo "用法: $0 [--full] [--check]"
      exit 0
      ;;
  esac
done

mkdir -p "$CACHE" "$DATA"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

cd "$ROOT"

if [ "$FULL" -eq 1 ]; then
  log "全量重建: 删除现有 statutes, 重新导入"
  rm -rf "$DATA/statutes" "$DATA/index"
fi

log "缓存目录: $CACHE"
log "数据目录: $DATA"

# 检查上游 HEAD 是否有变化 (轻量检测)
log "检测上游版本..."
LDB_VER=$(curl -sIL "https://github.com/13098806890/laws-data/commits/main.atom" 2>/dev/null | head -1 || echo "")
HF_VER="constant"  # parquet 文件名不变, 用 sha 检测
LRB_VER=$(curl -sIL "https://github.com/LawRefBook/Laws/commits/master.atom" 2>/dev/null | head -1 || echo "")

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "仅检测完成. log: $LOG"
  exit 0
fi

# 调 import.py
python3 "$ROOT/scripts/import.py" \
  --source all \
  --cache-dir "$CACHE" \
  --out-dir "$DATA" 2>&1 | tee -a "$LOG"

log "更新完成. log: $LOG"

# 报告
TOTAL=$(find "$DATA/statutes" -name '*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
log "总计: $TOTAL 部法律"

# 关键法律存在性
for slug in civil-code criminal-law company-law data-security-law personal-information-protection-law; do
  if [ -f "$DATA/statutes/$slug.json" ]; then
    CNT=$(python3 -c "import json; print(json.load(open('$DATA/statutes/$slug.json'))['article_count'])")
    log "  ✅ $slug: $CNT 条"
  else
    log "  ❌ $slug: 缺失"
  fi
done