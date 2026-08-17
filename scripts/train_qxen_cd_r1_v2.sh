#!/bin/bash
# QXEN-CD R1 v2 — 平衡分布训练（Dispatcher 方案 C）
# 数据: data/r1_balanced/train.jsonl（REL720/IRREL720=1440，1:1 平衡）
# 早停: 训练保存每 100 iters checkpoint，事后用 valid.jsonl 选优（见 r1_select_checkpoint.py）
# 输出: outputs/lora_adapters_r1_v2
# 超参: rank8/ga4/lr1e-5/iters=500/layers=4/seq=512/batch=1
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r1_training_v2.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_r1_v2.log"

echo "[train_qxen_cd_r1_v2] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束：Ollama 必须为空（T041 lesson）
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[train_qxen_cd_r1_v2] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[train_qxen_cd_r1_v2] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_qxen_cd_r1_v2] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 训练（后台）
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1_balanced \
  --adapter-path outputs/lora_adapters_r1_v2 \
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
echo "[train_qxen_cd_r1_v2] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[train_qxen_cd_r1_v2] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
