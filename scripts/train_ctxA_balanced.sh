#!/bin/bash
# T049 — ctxA 平衡数据重训启动脚本（后台 + 内存监控）
# 用法: bash scripts/train_ctxA_balanced.sh
# 差异（vs T047 train_ctxA.sh）：
#   1) --data: outputs/context_decision_training -> data/distill_ctxA/train_balanced_dir
#      （78 条平衡集, DROP 45.4% -> 24.4%, 其余标签全保留）
#   2) --adapter-path: outputs/lora_adapters_ctxA -> outputs/lora_adapters_ctxA_balanced
#      （新目录, 不覆盖 T047 产物）
#   3) 日志: ctxA_training.log -> ctxA_training_balanced.log; monitor 对应改名
# 超参严格不变：rank=8 / ga=4 / lr=1e-5 / iters=550 / layers=4 / seq=512 / batch=1 /
# grad_checkpoint / save_every=100 / seed=0（唯一变量 = 数据分布）。
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/ctxA_training_balanced.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_ctxA_balanced.log"

echo "[train_ctxA_balanced] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束：Ollama 必须为空
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[train_ctxA_balanced] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[train_ctxA_balanced] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_ctxA_balanced] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 训练（后台）
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/distill_ctxA/train_balanced_dir \
  --adapter-path outputs/lora_adapters_ctxA_balanced \
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
echo "[train_ctxA_balanced] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控（训练结束或触发保护自动退出）
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[train_ctxA_balanced] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
echo "[train_ctxA_balanced] training log tail:"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
