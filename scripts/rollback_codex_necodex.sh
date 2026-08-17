#!/bin/bash
# 一键回滚 codex 到 necodex 账号配置（幂等）—— 委托统一入口 codex-switch
# 用法: ./rollback_codex_necodex.sh
# 行为: 切换到 necodex 账号 + 侧边栏记录迁移（离开方 chatgpt 快照 -> 目标 necodex 恢复）
#       若只想要旧行为（不迁移侧边栏），用: ./rollback_codex_necodex.sh --no-sidebar

ROOT="/Users/hillo/Desktop/任务调度器"
ENTRY="$ROOT/scripts/codex-switch.sh"

if [[ ! -f "$ENTRY" ]]; then
  echo "❌ 未找到统一入口 $ENTRY"
  exit 1
fi

EXTRA=""
for a in "$@"; do
  case "$a" in
    --no-sidebar) EXTRA="--no-sidebar" ;;
    *) echo "❌ 未知参数: $a"; exit 2 ;;
  esac
done

exec "$ENTRY" --to necodex --yes $EXTRA
