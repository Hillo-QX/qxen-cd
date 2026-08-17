#!/bin/bash
# QXEN-CD R1.1 RECALL REPAIR — 训练脚本 (STEP6)
# 起点: outputs/lora_adapters_r1_selected (continuation, 保留 hard-negative 能力)
# 数据: data/r1_balanced_recall (train 2880 = 2160 new + 720 replay; valid 300)
# 超参: rank8/lr5e-6(保守)/iters=2880(完整一轮=2880样本)/layers=4/seq=512/batch=1/ga4/save_every=50
# 目标: repair indirect REL recall 而不退化 hard-negative discrimination
# 用户指令: 完整跑约 2880 iter (2026-08-13)
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r11_training.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_r11.log"

echo "[r11] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束: Ollama 必须为空 (T041 lesson)
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[r11] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[r11] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[r11] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 训练 (continuation from r1_selected, 后台)
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1_balanced_recall \
  --adapter-path outputs/lora_adapters_r1_recall \
  --resume-adapter-file outputs/lora_adapters_r1_selected/adapters.safetensors \
  --iters 2880 \
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
echo "[r11] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控 (短窗口，训练放后台继续)
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[r11] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
