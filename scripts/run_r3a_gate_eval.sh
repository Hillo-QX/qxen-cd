#!/usr/bin/env bash
# QXEN R3 T357 — R3A gate 评估启动脚本
# 封装 scripts/r3_gate_eval.py，参数化 stage=r3a。
#
# 用法:
#   ./scripts/run_r3a_gate_eval.sh                 # 完整 540 条 fresh 评估
#   ./scripts/run_r3a_gate_eval.sh --limit 20      # 冒烟
#   ./scripts/run_r3a_gate_eval.sh --base          # 含 base 对照 (Shadow)
#   ./scripts/run_r3a_gate_eval.sh --dry-run       # 静态校验（不加载模型）
#
# 前置: R3A 训练完成，models/r3a/ 含最终 adapters.safetensors（或经 select_best_checkpoint.py 选择的 checkpoint）。
# 注意: gate 评估需要加载模型做推理，必须等 R3A 训练完全结束（进程退出）后运行，避免内存竞争。
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE="r3a"
RUNS=("$STAGE")
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) RUNS=("base" "$STAGE"); shift ;;
    --limit=*) EXTRA+=("--limit" "${1#--limit=}"); shift ;;
    --limit) EXTRA+=("--limit" "$2"); shift 2 ;;
    --dry-run) EXTRA+=("--dry-run"); shift ;;
    --out=*) EXTRA+=("--out" "${1#--out=}"); shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 训练未结束则拒绝运行（避免与训练抢内存）。dry-run 不加载模型，允许执行。
if ! [[ " ${EXTRA[*]:-} " == *" --dry-run "* ]]; then
  if pgrep -f "mlx_lm lora.*adapter-path models/r3a" > /dev/null 2>&1; then
    echo "[run_r3a_gate_eval] FAIL: R3A 训练仍在运行，禁止评估（内存竞争）。请等待训练结束后重试。" >&2
    exit 1
  fi
fi

echo "[run_r3a_gate_eval] stage=$STAGE runs=${RUNS[*]} ${EXTRA[*]:-}"
# 注意: macOS bash 3.2 在 set -u 下，空数组 "${EXTRA[@]}" 会报 unbound variable，
# 需用 ${EXTRA[@]+...} 安全展开（数组非空时展开全部元素，为空时展开为空）。
exec ./venv/bin/python scripts/r3_gate_eval.py --stage "$STAGE" --runs "${RUNS[@]}" ${EXTRA[@]+"${EXTRA[@]}"}
