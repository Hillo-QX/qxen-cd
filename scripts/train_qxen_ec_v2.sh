#!/bin/bash
# QXEN Evidence Capsule v2 独立训练入口。
# 训练期间不启动 LocalQwen/Ollama，避免与 MLX 争抢统一内存。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="models/qwen3.5-9b-mlx-4bit"
DATA="data/r3/ec_v2"
OUT="models/qxen_ec_v2_schema_round1"
LOG="logs/qxen_ec_v2_schema_round1_train.log"
MONITOR="logs/qxen_ec_v2_schema_round1_memory.log"
mkdir -p logs

if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "ERROR: Ollama has loaded models; stop before MLX training."
  exit 2
fi
if pgrep -f "mlx_lm lora" >/dev/null 2>&1; then
  echo "ERROR: another mlx_lm lora process is running."
  exit 2
fi
if [ -e "$OUT" ]; then
  echo "ERROR: output already exists: $OUT"
  exit 2
fi

echo "[v2] start $(date '+%Y-%m-%d %H:%M:%S')"
echo "[v2] model=$MODEL data=$DATA output=$OUT"
nohup ./venv/bin/python -m mlx_lm lora \
  --model "$MODEL" \
  --train \
  --data "$DATA" \
  --adapter-path "$OUT" \
  --iters 400 \
  --batch-size 1 \
  --grad-accumulation-steps 4 \
  --learning-rate 4e-6 \
  --num-layers 2 \
  --max-seq-length 512 \
  --grad-checkpoint \
  --save-every 25 \
  --steps-per-report 10 \
  --steps-per-eval 200 \
  --val-batches 0 \
  --clear-cache-threshold 1073741824 \
  --seed 42 \
  > "$LOG" 2>&1 &
PID=$!
echo "[v2] PID=$PID log=$LOG"
./scripts/memory_monitor.sh "$PID" "$MONITOR"
RC=$?
echo "[v2] monitor exit=$RC train_alive=$(kill -0 "$PID" 2>/dev/null && echo yes || echo no)"
exit "$RC"
