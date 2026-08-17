#!/bin/bash
# watch_loop.sh — QXEN working loop 系统级事件驱动巡检（launchd 兜底）
#
# 核心：只在「状态变化需要 GPT 介入」时才推 codex exec（开新聊天框）。
# 正常训练推进 / idle / 已通知过的事件 → 静默，只写日志，不开聊天框。
#
# 唤醒 GPT 的事件（由 decide_loop_action.py 状态机判定）：
#   done / failed / mem_danger / stall / needs_decision
# 自动巡检可执行 Gate 和阶段编排；专家调用仍仅由用户明确调动。
#
# 分工：shell 采数 + 硬阈值（确定性数字），qwen 只在异常唤醒时做一次语义蒸馏
#       （monitor_analyze → verdict/failure_cluster/alert_capsule），平时不跑 qwen。
#
# 用法：
#   scripts/watch_loop.sh                 # 手动跑一次
#   由 launchd 每 900s 调度               # 系统级兜底

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/venv/bin/python"
CODEX_BIN="/Applications/ChatGPT.app/Contents/Resources/codex"
WATCH_LOG="${R3_WATCH_LOG:-logs/r3/qxen_loop_watch.log}"

mkdir -p logs/r3
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$WATCH_LOG"; }

# ---- 1. 事件驱动决策（采数 + 状态机，正常静默）----
decision_json=$("$PY" "$ROOT/scripts/decide_loop_action.py" 2>/dev/null)
[ -n "$decision_json" ] || { log "❌ decide_loop_action.py 无输出"; exit 0; }

# 用 python 安全解析 JSON
notify=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
d = json.loads(sys.argv[1])
print("1" if d.get("notify") else "0")
PYEOF
)
reason=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
print(json.loads(sys.argv[1]).get("reason", "?"))
PYEOF
)
adapter=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
print(json.loads(sys.argv[1]).get("adapter", "?"))
PYEOF
)
iter_info=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
print(json.loads(sys.argv[1]).get("iter", "?/?"))
PYEOF
)

# ---- 2. 分支 ----
if [ "$notify" = "0" ]; then
  log "✅ 静默：adapter=${adapter} phase=${reason} iter=${iter_info}"
  exit 0
fi

# ---- 3. 完成事件自动进入 R3 编排器 ----
# Gate 仍是硬门禁；专家不是前置条件。Gate FAIL 由 GPT 自主进入诊断/修复。
if [ "$reason" = "done" ]; then
  pipeline_json=$("$PY" "$ROOT/scripts/advance_r3_loop.py" --auto 2>>"$WATCH_LOG" || true)
  log "R3 编排器：${pipeline_json}"
  pipeline_status=$("$PY" - "$pipeline_json" <<'PYEOF'
import json, sys
try:
    print(json.loads(sys.argv[1]).get("status", ""))
except Exception:
    print("")
PYEOF
)
  if [ "$pipeline_status" = "GATE_PASS" ]; then
    pipeline_json2=$("$PY" "$ROOT/scripts/advance_r3_loop.py" --auto 2>>"$WATCH_LOG" || true)
    log "R3 编排器续接：${pipeline_json2}"
  fi
fi

# ---- 4. 需要唤醒 GPT：状态通知，不自动调用专家 ----
alert=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
print(json.loads(sys.argv[1]).get("alert", ""))
PYEOF
)
mem_gb=$("$PY" - "$decision_json" <<'PYEOF'
import json, sys
print(json.loads(sys.argv[1]).get("mem_gb", 0))
PYEOF
)

log "🔔 事件触发（${reason}）：${alert}"

train_log="logs/r3/${adapter}_train.log"
analysis_json=$("$PY" "$ROOT/scripts/monitor_analyze.py" --alert "$alert" --log "$train_log" 2>>"$WATCH_LOG")
[ -n "$analysis_json" ] || analysis_json="{\"status\":\"FALLBACK\",\"reason\":\"bridge 无输出\"}"
log "qwen 蒸馏结果：${analysis_json}"

# ---- 5. 推 codex exec（系统级兜底：app 关着也能独立触发 codex）----
prompt="QXEN working loop 状态通知（自动巡检，GPT自主决策）。事件类型：${reason}。蒸馏结果：${analysis_json}。原始摘要：${alert}。当前 adapter=${adapter}，iter=${iter_info}，峰值内存 ${mem_gb}GB。GPT主 Agent 可自行保留证据、运行 Gate、诊断修复并继续 working loop；Gate FAIL 不启动下一阶段；Gate PASS 自动晋级；不自动调用 Kimi-Expert/DeepSeek，只有用户明确要求时才调用。"
log "推送 codex exec ..."
"$CODEX_BIN" exec -C "$ROOT" --skip-git-repo-check "$prompt" >>"$WATCH_LOG" 2>&1
log "codex exec 返回码 $?"

exit 0
