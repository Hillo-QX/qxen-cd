#!/bin/bash
# T047 — ctxA adapter 安全训练启动脚本（后台 + 内存监控）
# 用法: bash scripts/train_ctxA.sh
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/ctxA_training.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_ctxA.log"

echo "[train_ctxA] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束：Ollama 必须为空
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[train_ctxA] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[train_ctxA] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_ctxA] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 训练（后台）
# 注意：T047 描述的 --train-file 参数实际是 --data <目录>（mlx_lm CLI），
#       数据目录为 outputs/context_decision_training/（内含 train.jsonl，格式 {prompt,completion}）。
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data outputs/context_decision_training \
  --adapter-path outputs/lora_adapters_ctxA \
  --iters 550 \
  --batch-size 1 \
  --grad-accumulation-steps 4 \
  --learning-rate 1e-5 \
  --num-layers 4 \
  --max-seq-length 512 \
  --grad-checkpoint \
  --save-every 100 \
  --steps-per-report 5 \
  --steps-per-eval 100 \
  --val-batches 0 \
  --clear-cache-threshold 1073741824 \
  --seed 0 \
  > "$TRAIN_LOG" 2>&1 &
TRAIN_PID=$!
echo "[train_ctxA] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控（训练结束或触发保护自动退出）
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[train_ctxA] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
echo "[train_ctxA] training log tail:"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
