#!/bin/bash
# QXEN-CD R1.1 RECALL REPAIR — 续训脚本 (STEP6, iter1000→2880)
# 起点: outputs/lora_adapters_r1_recall/0001000_adapters.safetensors (已训1000 iters)
# 续训: 1880 iters (总2880)，保持超参 seq512/save_every50/lr5e-6
# 背景: 首次训练在 iter1015 被内存保护SIGTERM(Dispatcher方案B: resume续训)
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r11_training_resume.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_r11_resume.log"

echo "[r11-resume] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束: Ollama 必须为空 (T041 lesson)
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[r11-resume] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[r11-resume] ollama ps: empty (OK)"

# 2) 训练前内存快照 (需 >1GB free)
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[r11-resume] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"
if [ "$FREE_MB" -lt 1000 ]; then
  echo "[r11-resume] ERROR: free memory < 1GB; abort. 请先释放内存(关闭后台进程)。"
  exit 3
fi

# 3) 续训 (resume from 0001000, iters=1880 达总2880)
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1_balanced_recall \
  --adapter-path outputs/lora_adapters_r1_recall \
  --resume-adapter-file outputs/lora_adapters_r1_recall/0001000_adapters.safetensors \
  --iters 1880 \
  --batch-size 1 \
  --grad-accumulation-steps 4 \
  --learning-rate 5e-6 \
  --num-layers 4 \
  --max-seq-length 512 \
  --grad-checkpoint \
  --save-every 50 \
  --steps-per-report 5 \
  --steps-per-eval 100 \
  --val-batches 0 \
  --clear-cache-threshold 1073741824 \
  --seed 42 \
  > "$TRAIN_LOG" 2>&1 &
TRAIN_PID=$!
echo "[r11-resume] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[r11-resume] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
