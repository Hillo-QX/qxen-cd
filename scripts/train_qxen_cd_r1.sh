#!/bin/bash
# QXEN-CD R1 — REL/IRREL 二分类 LoRA 训练启动脚本
# 用法:
#   bash scripts/train_qxen_cd_r1.sh            # 完整训练 (mlx_lm lora)
#   bash scripts/train_qxen_cd_r1.sh --dry-run  # 仅数据验证 (不训练)
# 配置: configs/qxen_cd_r1_train.yaml
# 数据: data/r1/train.jsonl 2160 / valid.jsonl 300（test.jsonl 540 仅门控，不参与训练）
# 输出: outputs/lora_adapters_r1
# 依据: T055 R1 Base benchmark + Dataset Gate 20/20 PASS
# 注: mlx_lm 的 --data 期望目录（内含 train.jsonl/valid.jsonl/test.jsonl）
set -u
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ "$DRY_RUN" = "1" ]; then
  echo "[train_qxen_cd_r1] DRY-RUN 数据验证"
  ./venv/bin/python - <<'PYEOF'
import json
from collections import Counter

for name in ("train.jsonl", "valid.jsonl", "test.jsonl"):
    rows = [json.loads(l) for l in open(f"data/r1/{name}", encoding="utf-8") if l.strip()]
    c = Counter(r["completion"].strip() for r in rows)
    print(f"[{name}] {len(rows)} 条 | labels={dict(c)}")
    for r in rows[:5]:
        print(f"  prompt[:60]={r['prompt'][:60]!r} -> {r['completion']}")

gt = [json.loads(l) for l in open("ground_truth.jsonl", encoding="utf-8") if l.strip()]
print(f"[ground_truth.jsonl] {len(gt)} 条 | keys={list(gt[0].keys())}")
PYEOF
  echo "[train_qxen_cd_r1] DRY-RUN 完成（未启动训练）"
  exit 0
fi

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
TRAIN_LOG="$LOG_DIR/r1_training.log"
MONITOR_LOG="$LOG_DIR/memory_monitor_r1.log"

echo "[train_qxen_cd_r1] start $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 训练前硬约束：Ollama 必须为空（T041 lesson）
if [ -n "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
  echo "[train_qxen_cd_r1] ERROR: Ollama has loaded models; abort (T041 lesson)."
  exit 2
fi
echo "[train_qxen_cd_r1] ollama ps: empty (OK)"

# 2) 训练前内存快照
FREE_MB=$(( ( $(vm_stat | awk '/Pages free/ {gsub(/\./,""); print $3}') + $(vm_stat | awk '/Pages speculative/ {gsub(/\./,""); print $3}') ) * $(sysctl -n hw.pagesize) / 1048576 ))
WIRED_MB=$(( $(vm_stat | awk '/Pages wired down/ {gsub(/\./,""); print $4}') * $(sysctl -n hw.pagesize) / 1048576 ))
echo "[train_qxen_cd_r1] pre-train memory: free=${FREE_MB}MB wired=${WIRED_MB}MB"

# 3) 启动 mlx_lm lora 训练（后台）—— R1 从 base 全新训练（不复用 ctxA adapter）
nohup ./venv/bin/python -m mlx_lm lora \
  --model models/qwen3.5-9b-mlx-4bit \
  --train \
  --data data/r1 \
  --adapter-path outputs/lora_adapters_r1 \
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
echo "[train_qxen_cd_r1] training PID=$TRAIN_PID log=$TRAIN_LOG"

# 4) 前台运行内存监控
./scripts/memory_monitor.sh "$TRAIN_PID" "$MONITOR_LOG"
RC=$?
echo "[train_qxen_cd_r1] memory_monitor exited rc=$RC (train PID alive? $(kill -0 $TRAIN_PID 2>/dev/null && echo yes || echo no))"
tail -5 "$TRAIN_LOG" 2>/dev/null
exit $RC
