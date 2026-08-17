#!/usr/bin/env bash
# 收工审计钩子：会话结束前必跑的唯一入口。FAIL 即禁止收工。
#
# 用法:
#   ./scripts/finish_session.sh              审计最新会话
#   ./scripts/finish_session.sh <session.json>  审计指定会话
#
# 退出码:
#   0 = PASS/WARN，可收工
#   1 = FAIL，禁止收工（必须回炉补蒸馏后重跑）
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/venv/bin/python"
AUDIT="$ROOT/scripts/audit_v2_session.py"

echo "==================== 会话收工审计 ===================="
echo "入口: scripts/finish_session.sh  (审计项: model/boot/raw_bypass/schema/token)"
echo "------------------------------------------------------"

if [ "$#" -gt 0 ]; then
    OUTPUT="$("$PY" "$AUDIT" "$1" 2>&1)"
else
    OUTPUT="$("$PY" "$AUDIT" 2>&1)"
fi
CODE=$?

printf '%s\n' "$OUTPUT"

echo "------------------------------------------------------"
SAFE_RUN="$ROOT/scripts/safe_run.sh"
if [ -x "$SAFE_RUN" ]; then
    echo "输出预算守卫: ENABLED (safe_run.sh, limit=1500 bytes)"
else
    echo "输出预算守卫: WARN (safe_run.sh 不可执行)"
fi
if [ "$CODE" -eq 0 ]; then
    echo "判定: PASS/WARN —— 可收工"
    echo "checklist: [x] 跑审计  [x] 无 FAIL 项  [x] 报告落盘 日志/audit/"
    exit 0
else
    echo "判定: FAIL —— 禁止收工，回炉补蒸馏后重跑"
    echo "checklist: [x] 跑审计  [ ] 修复 FAIL 项  [ ] 重跑至 PASS/WARN"
    echo "FAIL 项:"
    printf '%s\n' "$OUTPUT" | grep -E "FAIL" | head -20
    exit 1
fi
