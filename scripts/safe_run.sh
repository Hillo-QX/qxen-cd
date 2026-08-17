#!/usr/bin/env bash
# Codex 输出预算守卫
#
# 用法：
#   ./scripts/safe_run.sh -- command arg1 arg2
#   ./scripts/safe_run.sh -- bash -lc 'rg ... file | sort'
#
# 命令的 stdout/stderr 先落盘，再按字节数决定是否回显：
#   <= 1500 字节（含 safe_run 标记）：回显原文，可走 deterministic fast path
#   >  1500 字符：只回显元数据和文件路径，随后交给 QXEN-CD/LocalQwen 蒸馏
#
# 该脚本不调用 MCP；它的职责是保证长原文不会先进入 Codex 上下文。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="${TMPDIR:-/tmp}"
MAX_CHARS="${SAFE_RUN_MAX_CHARS:-1500}"

if [[ "${1:-}" != "--" || "$#" -lt 2 ]]; then
    echo "用法: $0 -- command [args...]" >&2
    exit 2
fi
shift

if ! [[ "$MAX_CHARS" =~ ^[0-9]+$ ]] || [ "$MAX_CHARS" -lt 1 ] || [ "$MAX_CHARS" -gt 1500 ]; then
    echo "SAFE_RUN_MAX_CHARS 必须是 1-1500 的整数，不能绕过 raw_bypass 预算" >&2
    exit 2
fi

OUT_FILE="$(mktemp "${TMP_ROOT%/}/codex-safe-run.XXXXXX")" || {
    echo "无法创建安全输出临时文件" >&2
    exit 2
}

"$@" >"$OUT_FILE" 2>&1
CMD_CODE=$?
BYTE_COUNT="$(wc -c <"$OUT_FILE" | tr -d ' ')"
LINE_COUNT="$(wc -l <"$OUT_FILE" | tr -d ' ')"

# 字节数在 UTF-8 下是保守预算：不会因多字节字符导致回显超过阈值。
# fast_path 标记也计入预算，避免“记录模式”本身把输出推过阈值。
META="[safe_run] fast_path=deterministic bytes=$BYTE_COUNT lines=$LINE_COUNT limit=$MAX_CHARS\n"
META_BYTES="$(printf '%b' "$META" | wc -c | tr -d ' ')"
PAYLOAD_LIMIT=$((MAX_CHARS - META_BYTES))
if [ "$PAYLOAD_LIMIT" -gt 0 ] && [ "$BYTE_COUNT" -le "$PAYLOAD_LIMIT" ]; then
    printf '%b' "$META"
    cat "$OUT_FILE"
    rm -f "$OUT_FILE"
    exit "$CMD_CODE"
fi

echo "[safe_run] output_guard=TRIPPED"
echo "[safe_run] bytes=$BYTE_COUNT lines=$LINE_COUNT limit=$MAX_CHARS command_exit=$CMD_CODE"
echo "[safe_run] raw_output=$OUT_FILE"
echo "[safe_run] next=先调用 QXEN-CD/LocalQwen 蒸馏；仅按需读取 <=100 行必要片段"
exit "$CMD_CODE"
