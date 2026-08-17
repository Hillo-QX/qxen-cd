#!/bin/bash
# QXEN-CD R1.3 B3 — 数据增补后微调训练 (T356)
# 数据: data/r1.3/mlx/ (train 108, 五类 T1-T5, prompt 协议与 R1.2 一致)
# 起点: resume outputs/r1.2_multi_epoch/adapters.safetensors (R1.2 3epoch, sha256 0c0ead0d)
# 超参: rank8/lr5e-6/layers4/seq512/batch1/ga4/iters=216(2 epoch)/save_every36
# 输出: outputs/lora_adapters_r1_recall_r13/
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r13_training.log"

echo "[r13] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束: Ollama 必须为空 (T041 lesson)
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[r13] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[r13] ollama ps: empty (OK)"

# 2) 数据存在性校验
for f in data/r1.3/mlx/train.jsonl outputs/r1.2_multi_epoch/adapters.safetensors; do
  [ -f "$f" ] || { echo "[r13] ERROR: missing $f"; exit 2; }
done
echo "[r13] data files: OK (train $(wc -l < data/r1.3/mlx/train.jsonl))"
echo "[r13] resume adapter sha256: $(shasum -a 256 outputs/r1.2_multi_epoch/adapters.safetensors | cut -c1-16)"

# 3) 启动 mlx_lm lora 训练 (后台)
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1.3/mlx \
  --adapter-path outputs/lora_adapters_r1_recall_r13 \
  --resume-adapter-file outputs/r1.2_multi_epoch/adapters.safetensors \
  --iters 216 \
  --batch-size 1 \
  --grad-accumulation-steps 4 \
  --learning-rate 5e-6 \
  --num-layers 4 \
  --max-seq-length 512 \
  --grad-checkpoint \
  --save-every 36 \
  --steps-per-report 10 \
  --steps-per-eval 54 \
  --val-batches 0 \
  --clear-cache-threshold 1073741824 \
  --seed 42 \
  > "$TRAIN_LOG" 2>&1 &
TRAIN_PID=$!
echo "[r13] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 轮询等待训练完成 (最长 600s)
for i in $(seq 1 120); do
  if ! kill -0 $TRAIN_PID 2>/dev/null; then
    break
  fi
  sleep 5
done

if kill -0 $TRAIN_PID 2>/dev/null; then
  echo "[r13] WARN: 训练超时仍在运行, 保留后台 (PID=$TRAIN_PID)"
  tail -3 "$TRAIN_LOG"
  exit 1
fi
wait $TRAIN_PID
RC=$?
echo "[r13] training exited rc=$RC $(date '+%Y-%m-%d %H:%M:%S')"
tail -15 "$TRAIN_LOG"
exit $RC
