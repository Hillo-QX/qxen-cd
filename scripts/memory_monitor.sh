#!/bin/bash
# memory_monitor.sh - 训练内存保护监控（macOS / Apple Silicon 专用）
#
# 背景：2026-08-12 两次整机重启：
#   - 18:45 训练进程 RSS 23.15GB，IOGPUGroupMemory.cpp:528 内核 panic。
#   - 19:06 即便配置收窄(seq=1024/layers=8)，wired 峰值 20.2GB、free 多次跌至 27-117MB，
#            monitor 因 wired 未达 20GB 阈值而没触发，整机再次崩溃重启。
# 本脚本在内存逼近危险线时主动终止训练进程，以"丢一次训练"换取"不再整机 panic"。
#
# 用法：
#   scripts/memory_monitor.sh <训练PID> [日志文件]
#
# 规则（每 2 秒采样一次；连续 3 次越过红线才终止，避免模型加载期瞬时抖动误杀）：
#   - wired 内存 > 18GB => 危险，SIGTERM 训练进程（红线，低于上次崩溃时的 20.2GB 峰值）
#   - free < 500MB      => 危险，SIGTERM 训练进程（上次崩溃前 free 多次跌至 27-117MB）
#   - SIGTERM 后 15 秒仍存活 => 升级 SIGKILL

set -u

PID="${1:-}"
LOG="${2:-logs/memory_monitor.log}"
INTERVAL=2
FREE_LIMIT_MB="${FREE_LIMIT_MB:-500}"  # 可按单次任务下调；默认仍为500MB
WIRED_LIMIT_MB="${WIRED_LIMIT_MB:-18432}" # wired红线保持18GB
CONSEC="${CONSEC:-3}"            # 默认连续3次采样命中才触发（约6s）

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

if [ -z "$PID" ]; then
  echo "usage: $0 <training_pid> [logfile]" >&2
  exit 2
fi

if ! kill -0 "$PID" 2>/dev/null; then
  echo "PID $PID is not alive." >&2
  exit 2
fi

mkdir -p "$(dirname "$LOG")"
log "memory_monitor started: watching PID=$PID, interval=${INTERVAL}s, free_limit=${FREE_LIMIT_MB}MB wired_limit=${WIRED_LIMIT_MB}MB"

PAGE_SIZE=$(sysctl -n hw.pagesize 2>/dev/null || echo 16384)
TOTAL_MB=$(awk -v m="$(sysctl -n hw.memsize 2>/dev/null || echo 0)" 'BEGIN { printf "%d", m/1048576 }')

WIRED_HITS=0
FREE_HITS=0

while :; do
  # 训练进程已结束 => 监控任务完成，正常退出
  if ! kill -0 "$PID" 2>/dev/null; then
    log "training PID $PID no longer running; monitor exiting normally."
    exit 0
  fi

  # 采样 vm_stat
  FREE_PAGES=$(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}')
  WIRED_PAGES=$(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}')
  SPEC_PAGES=$(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}')
  [ -z "$FREE_PAGES" ] && FREE_PAGES=0
  [ -z "$WIRED_PAGES" ] && WIRED_PAGES=0
  [ -z "$SPEC_PAGES" ] && SPEC_PAGES=0

  FREE_MB=$(( (FREE_PAGES + SPEC_PAGES) * PAGE_SIZE / 1048576 ))
  WIRED_MB=$(( WIRED_PAGES * PAGE_SIZE / 1048576 ))

  log "sample: free=${FREE_MB}MB wired=${WIRED_MB}MB total=${TOTAL_MB}MB (PID=$PID)"

  # 累计连续命中次数（恢复即清零）；连续 CONSEC 次越线才触发终止
  if [ "$WIRED_MB" -gt "$WIRED_LIMIT_MB" ]; then
    WIRED_HITS=$((WIRED_HITS + 1))
  else
    WIRED_HITS=0
  fi
  if [ "$FREE_MB" -lt "$FREE_LIMIT_MB" ]; then
    FREE_HITS=$((FREE_HITS + 1))
  else
    FREE_HITS=0
  fi

  TRIGGER=""
  if [ "$WIRED_HITS" -ge "$CONSEC" ]; then
    TRIGGER="wired memory exceeded ${WIRED_LIMIT_MB}MB for ${CONSEC} consecutive samples (wired=${WIRED_MB}MB)"
  elif [ "$FREE_HITS" -ge "$CONSEC" ]; then
    TRIGGER="free memory below ${FREE_LIMIT_MB}MB for ${CONSEC} consecutive samples (free=${FREE_MB}MB)"
  fi

  if [ -n "$TRIGGER" ]; then
    log "WARN $TRIGGER; sending SIGTERM to PID $PID"
    kill -TERM "$PID" 2>/dev/null
    sleep 15
    if kill -0 "$PID" 2>/dev/null; then
      log "WARN PID $PID still alive after SIGTERM; sending SIGKILL"
      kill -KILL "$PID" 2>/dev/null
    fi
    log "monitor exiting after kill (training aborted to prevent kernel panic)."
    exit 1
  fi

  sleep "$INTERVAL"
done
