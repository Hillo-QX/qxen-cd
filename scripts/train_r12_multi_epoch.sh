#!/bin/bash
# QXEN-CD R1.2 — 多 epoch 收敛训练 (T350): 从 1-epoch 基线续训至总 3 epoch
# 起点: outputs/lora_adapters_r1_recall_r12/adapters.safetensors (1 epoch, valid acc 0.95)
# 续训: iters=840 (2 epoch, 总 3 epoch = 1260 iters), save-every=60 → 0000420(epoch2)/0000840(epoch3) 可评估
# 输出: outputs/r1.2_multi_epoch/ (adapter) + logs/r12_multi_epoch_training.log
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r12_multi_epoch_training.log"
ADAPTER_DIR="$PROJECT_ROOT/outputs/r1.2_multi_epoch"

echo "[r12-multi] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) Ollama 必须为空 (T041 lesson)
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[r12-multi] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[r12-multi] ollama ps: empty (OK)"

# 2) 数据与起点 ckpt 校验
for f in data/r1.2/mlx/train.jsonl data/r1.2/mlx/valid.jsonl outputs/lora_adapters_r1_recall_r12/adapters.safetensors; do
  [ -f "$f" ] || { echo "[r12-multi] ERROR: missing $f"; exit 2; }
done
echo "[r12-multi] inputs OK"

# 3) 启动续训 (后台)
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1.2/mlx \
  --adapter-path "$ADAPTER_DIR" \
  --resume-adapter-file outputs/lora_adapters_r1_recall_r12/adapters.safetensors \
  --iters 840 \
  --batch-size 1 \
  --grad-accumulation-steps 4 \
  --learning-rate 5e-6 \
  --num-layers 4 \
  --max-seq-length 512 \
  --grad-checkpoint \
  --save-every 60 \
  --steps-per-report 20 \
  --steps-per-eval 120 \
  --val-batches 10 \
  --clear-cache-threshold 1073741824 \
  --seed 42 \
  > "$TRAIN_LOG" 2>&1 &
TRAIN_PID=$!
echo "[r12-multi] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 轮询等待 (最长 1500s = 25min)
for i in $(seq 1 300); do
  if ! kill -0 $TRAIN_PID 2>/dev/null; then
    break
  fi
  sleep 5
done

if kill -0 $TRAIN_PID 2>/dev/null; then
  echo "[r12-multi] WARN: 训练超时仍在运行 (PID=$TRAIN_PID)"
  tail -3 "$TRAIN_LOG"
  exit 1
fi
wait $TRAIN_PID
RC=$?
echo "[r12-multi] training exited rc=$RC $(date '+%Y-%m-%d %H:%M:%S')"
tail -8 "$TRAIN_LOG"
exit $RC
