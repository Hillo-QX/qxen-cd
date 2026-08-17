#!/bin/bash
# watch_r3a_v2.sh — R3A 系列训练巡检 + 异常推 GPT 主 Agent
#
# 用途：每小时抓一次 R3A v2 训练状态；发现异常时用 codex exec 推送一条
#       蒸馏后的告警，让 GPT 主 Agent 去读日志、判断、调用 DeepSeek 备用层并处理。
#
# 异常判定（任一命中即推 codex）：
#   A. 训练进程已退出，但最新 iter < 2160（未跑完就退出）
#   B. 日志出现 Traceback / Error / OOM / out of memory / Killed
#   C. 峰值内存 Peak mem 较基线暴涨（当前 > 18GB，接近上次整机崩溃红线）
#
# 正常完成：最新 iter >= 2160 且日志含 "Saved final weights"
#
# 用法：
#   scripts/watch_r3a_v2.sh              # 手动跑一次
#   由 launchd 每 3600s 调度              # 自动每小时跑
#
# 状态与告警去重：logs/r3/r3a_v2_watch_state.json

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADAPTER="${R3_ADAPTER:-r3a_v2}"
LOG="${R3_TRAIN_LOG:-logs/r3/${ADAPTER}_train.log}"
STATE="${R3_WATCH_STATE:-logs/r3/${ADAPTER}_watch_state.json}"
WATCH_LOG="${R3_WATCH_LOG:-logs/r3/${ADAPTER}_watch.log}"
TARGET_ITERS=2160
CODEX_BIN="/Applications/ChatGPT.app/Contents/Resources/codex"

mkdir -p logs/r3

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$WATCH_LOG"; }

# ---- 1. 抓最新进度（只取关键行，不整读日志）----
last_iter=$(grep -aE "Iter [0-9]+: Train loss" "$LOG" 2>/dev/null | tail -n 1 | grep -aoE "Iter [0-9]+" | grep -aoE "[0-9]+")
last_val=$(grep -aE "Iter [0-9]+: Val loss" "$LOG" 2>/dev/null | tail -n 1 | grep -aoE "Val loss [0-9.]+" | grep -aoE "[0-9.]+")
last_train=$(grep -aE "Iter [0-9]+: Train loss" "$LOG" 2>/dev/null | tail -n 1 | grep -aoE "Train loss [0-9.]+" | grep -aoE "[0-9.]+")
last_mem=$(grep -aE "Iter [0-9]+: Train loss" "$LOG" 2>/dev/null | tail -n 1 | grep -aoE "Peak mem [0-9.]+ GB" | grep -aoE "[0-9.]+")
[ -z "$last_iter" ] && last_iter=0
[ -z "$last_val" ] && last_val="?"
[ -z "$last_train" ] && last_train="?"
[ -z "$last_mem" ] && last_mem=0

# ---- 2. 进程存活判断（按 mlx_lm lora + adapter 匹配，PID 可能变）----
proc_alive=$(pgrep -f "mlx_lm lora.*${ADAPTER}" >/dev/null 2>&1 && echo 1 || echo 0)

# ---- 3. 异常检测 ----
alert=""
if [ "$proc_alive" = "0" ] && [ "$last_iter" -lt "$TARGET_ITERS" ]; then
  alert="进程已退出但只跑到 Iter ${last_iter}/${TARGET_ITERS}（未跑完）"
fi

if grep -aqE "Traceback|out of memory|MemoryError" "$LOG" 2>/dev/null; then
  alert="${alert:+${alert}；}日志出现 Traceback/OOM"
fi
if grep -aqE "Killed" "$LOG" 2>/dev/null && [ "$proc_alive" = "0" ]; then
  alert="${alert:+${alert}；}进程被 Killed"
fi

# 峰值内存红线（上次整机崩溃前 wired 峰值 20.2GB；训练进程 RSS 超 18GB 视为危险）
mem_danger=$(echo "$last_mem" | awk '{if ($1+0 > 18) print "1"; else print "0"}')
if [ "$mem_danger" = "1" ]; then
  alert="${alert:+${alert}；}峰值内存 ${last_mem}GB 超 18GB 红线"
fi

# 正常完成判定
if [ "$last_iter" -ge "$TARGET_ITERS" ] && grep -aq "Saved final weights" "$LOG" 2>/dev/null; then
  log "${ADAPTER} 训练正常完成：Iter ${last_iter}/${TARGET_ITERS}，Val loss ${last_val}"
  # 当前实验完成，但不把 GPT 主 Agent 的整个 working loop 置为停止。
  cat > "$STATE" <<EOF
{"status":"done","adapter":"r3a_v2","last_iter":${last_iter},"val_loss":"${last_val}","finished":"$(date -u +%FT%TZ)"}
EOF
  exit 0
fi

# ---- 4. 记录快照 ----
cat > "$STATE" <<EOF
{"adapter":"r3a_v2","last_iter":${last_iter},"val_loss":"${last_val}","train_loss":"${last_train}","peek_mem_gb":"${last_mem}","proc_alive":${proc_alive},"checked":"$(date -u +%FT%TZ)"}
EOF

if [ -n "$alert" ]; then
  log "⚠️ 检测到异常：${alert}"
  # ---- 5. qwen 语义蒸馏（shell 采数判阈值，qwen 只做语义层）----
  analysis_json=$("$ROOT/venv/bin/python" "$ROOT/scripts/monitor_analyze.py" --alert "$alert" --log "$LOG" 2>>"$WATCH_LOG")
  [ -n "$analysis_json" ] || analysis_json="{\"status\":\"FALLBACK\",\"reason\":\"bridge 无输出\"}"
  log "qwen 蒸馏结果：${analysis_json}"
  # ---- 6. 推 codex（蒸馏告警，不塞原始日志）----
  prompt="训练监控告警（${ADAPTER}，qwen 语义蒸馏）。蒸馏结果：${analysis_json}。原始摘要：${alert}。当前进度 Iter ${last_iter}/${TARGET_ITERS}，Val loss ${last_val}，Train loss ${last_train}，峰值内存 ${last_mem}GB。你是 GPT 主 Agent：先保留 checkpoint/日志/内存/进程证据，按蒸馏结果的 verdict 判断是否可恢复；第一次修复失败后做第二次有依据修复；第二次仍失败必须自动尝试 DeepSeek Fallback。DeepSeek 不可用时记录 fallback_unavailable，由 GPT 自行选择安全的有界诊断/配置/数据任务并继续 working loop；禁止第三次盲目重试同一故障。任何异常都不得把阻塞消息原样推给用户后结束；OOM 或 Metal 停滞时停止危险训练动作，但继续处理安全任务，直到用户明确介入或任务 DONE。"
  log "推送 codex exec ..."
  "$CODEX_BIN" exec -C "$ROOT" --skip-git-repo-check "$prompt" >>"$WATCH_LOG" 2>&1
  log "codex exec 返回码 $?"
else
  log "✅ 正常：Iter ${last_iter}/${TARGET_ITERS}，Val loss ${last_val}，Train loss ${last_train}，峰值内存 ${last_mem}GB，进程存活=${proc_alive}"
fi

exit 0
