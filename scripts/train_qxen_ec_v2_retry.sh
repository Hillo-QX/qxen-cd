#!/bin/bash
# EC v2 retry: seq384 + one validation batch, isolated output.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODEL="models/qwen3.5-9b-mlx-4bit"
DATA="data/r3/ec_v2"
OUT="models/qxen_ec_v2_schema_round1_retry"
LOG="logs/qxen_ec_v2_schema_round1_retry_train.log"
MONITOR="logs/qxen_ec_v2_schema_round1_retry_memory.log"
mkdir -p logs
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ] || ps -axo command= | grep -E 'python.*-m mlx_lm lora' | grep -v grep >/dev/null 2>&1; then
  echo "ERROR: local model/training process already loaded"; exit 2
fi
if [ -e "$OUT" ]; then echo "ERROR: output already exists: $OUT"; exit 2; fi
echo "[v2-retry] start $(date '+%Y-%m-%d %H:%M:%S')"
nohup ./venv/bin/python -m mlx_lm lora \
  --model "$MODEL" --train --data "$DATA" --adapter-path "$OUT" \
  --iters 400 --batch-size 1 --grad-accumulation-steps 4 \
  --learning-rate 4e-6 --num-layers 2 --max-seq-length 384 \
  --grad-checkpoint --save-every 25 --steps-per-report 10 \
  --steps-per-eval 200 --val-batches 1 --clear-cache-threshold 1073741824 \
  --seed 42 > "$LOG" 2>&1 &
PID=$!
echo "[v2-retry] PID=$PID log=$LOG"
FREE_LIMIT_MB=256 WIRED_LIMIT_MB=18432 CONSEC=3 ./scripts/memory_monitor.sh "$PID" "$MONITOR"
RC=$?
echo "[v2-retry] monitor exit=$RC train_alive=$(kill -0 "$PID" 2>/dev/null && echo yes || echo no)"
exit "$RC"
