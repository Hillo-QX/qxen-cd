#!/bin/bash
# T054 Phase B 修复轮 — 重采样训练数据增量训练启动脚本
# 用法: bash scripts/train_ctxA_phaseB_fix.sh
# 数据: data/phaseB/train_resampled（210 条 = KEEP 126 + 其余 6 类各 14，向评估分布倾斜）
# 起点: outputs/lora_adapters_ctxA_mixed（T052 Phase A 正式产物，resume 增量）
# 输出: outputs/lora_adapters_ctxA_phaseB_fix
# 超参沿用 T052（已通过门控）：rank=8 / ga=4 / lr=1e-5 / iters=500 / layers=4 /
# seq=512 / batch=1 / grad_checkpoint / save_every=100 / seed=0。
# 依据: Dispatcher 决策 —— phaseB 门控 FAIL，按评估分布重采样训练数据重试。
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/ctxA_training_phaseB_fix.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_ctxA_phaseB_fix.log"

echo "[train_ctxA_phaseB_fix] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束：Ollama 必须为空（T041 lesson）
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[train_ctxA_phaseB_fix] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[train_ctxA_phaseB_fix] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_ctxA_phaseB_fix] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 增量训练（后台）
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/phaseB/train_resampled \
  --adapter-path outputs/lora_adapters_ctxA_phaseB_fix \
  --resume-adapter-file outputs/lora_adapters_ctxA_mixed/adapters.safetensors \
  --iters 500 \
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
echo "[train_ctxA_phaseB_fix] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控（训练结束或触发保护自动退出）
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[train_ctxA_phaseB_fix] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
echo "[train_ctxA_phaseB_fix] training log tail:"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
