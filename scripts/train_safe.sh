#!/bin/bash
# train_safe.sh - QXEN 安全训练启动包装（mlx_lm.lora + 内存保护）
#
# 背景：2026-08-12 两次整机重启（18:45 与 19:06，均为训练内存失控触发内核 panic）。
#       本脚本在启动训练的同时拉起 scripts/memory_monitor.sh，
#       一旦内存逼近危险线（wired>18GB 或 free<500MB）立即终止训练进程；
#       并在启动前检查当前大内存进程，提醒关闭非必要应用。
#
# 用法：
#   scripts/train_safe.sh [配置文件]
#     默认配置：configs/lora_train_safe.yaml
#
# 输出：
#   logs/train_safe_run.log        训练输出
#   logs/memory_monitor.log        内存监控日志

set -u

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

CONFIG="${1:-configs/lora_train_safe.yaml}"
LOG_DIR="$PROJECT_ROOT/logs"
TRAIN_LOG="$LOG_DIR/train_safe_run.log"
MONITOR_LOG="$LOG_DIR/memory_monitor.log"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: config not found: $CONFIG" >&2
  exit 2
fi
mkdir -p "$LOG_DIR"

echo "[train_safe] project_root=$PROJECT_ROOT"
echo "[train_safe] config=$CONFIG"
echo "[train_safe] train_log=$TRAIN_LOG monitor_log=$MONITOR_LOG"

# ---- 训练前内存检查：列出 top5 高内存进程，提醒关闭大内存应用 ----
echo ""
echo "[train_safe] 当前内存占用 Top5 进程（请关闭非必要大内存应用，尤其是浏览器/Codex/Office）："
ps -axo rss,comm -r 2>/dev/null | head -6 | awk 'NR==1{print "  RSS(MB)  COMMAND"} NR>1{printf "  %6d  %s\n", $1/1024, $2}'

TOTAL_MB=$(awk -v m="$(sysctl -n hw.memsize 2>/dev/null || echo 0)" 'BEGIN { printf "%d", m/1048576 }')
WIRED_NOW_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
FREE_NOW_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_safe] 当前内存: total=${TOTAL_MB}MB wired=${WIRED_NOW_MB}MB free=${FREE_NOW_MB}MB"
echo "[train_safe] 提示：若 free<2GB 或 wired>16GB，强烈建议先关闭大内存应用再训练；"
if [ -t 0 ]; then
  echo "[train_safe] 继续训练前请按回车确认（Ctrl-C 取消）..."
  read -r _ </dev/tty 2>/dev/null || true
else
  echo "[train_safe] 非交互模式（stdin 非 tty），跳过确认直接开始。"
fi

# 启动训练（后台）
./venv/bin/python -m mlx_lm lora -c "$CONFIG" > "$TRAIN_LOG" 2>&1 &
TRAIN_PID=$!
echo "[train_safe] training started PID=$TRAIN_PID"

# 拉起内存监控（前台运行；训练结束或触发保护后自动退出）
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
MONITOR_RC=$?

# 等待训练进程回收退出码
wait "$TRAIN_PID" 2>/dev/null
TRAIN_RC=$?

echo "[train_safe] finished: train_rc=$TRAIN_RC monitor_rc=$MONITOR_RC"
if [ "$MONITOR_RC" -eq 1 ]; then
  echo "[train_safe] !!! 训练被内存保护终止（防止内核 panic 重启），请查看 logs/memory_monitor.log"
elif [ "$TRAIN_RC" -ne 0 ]; then
  echo "[train_safe] 训练异常退出（train_rc=$TRAIN_RC），请查看 $TRAIN_LOG"
else
  echo "[train_safe] 训练正常结束。"
fi
echo "[train_safe] tail $TRAIN_LOG:"
tail -20 "$TRAIN_LOG"
exit 0
