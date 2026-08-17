#!/bin/bash
# 只读等待循环：监控 r3_gate_eval 进程，退出后提取 gate 结果摘要到 handoff 文件。
# 不修改任何训练/冻结资产，只写交接摘要。
set -u

PID="${1:-76551}"
RESULT="/Users/hillo/Desktop/任务调度器/reports/r3/r3a_structured_v1_gate_eval.json"
FLAG="/Users/hillo/Desktop/任务调度器/调度状态/gate_eval_handoff.json"
LOG="/Users/hillo/Desktop/任务调度器/日志/gate_wait.log"
PY="/Users/hillo/Desktop/任务调度器/venv/bin/python"

echo "[$(date -u +%FT%TZ)] wait_start pid=$PID" >> "$LOG"

while kill -0 "$PID" 2>/dev/null; do
  sleep 30
done

echo "[$(date -u +%FT%TZ)] pid=$PID exited, waiting for output" >> "$LOG"

# 等输出文件落盘（最多 60 秒）
for _ in $(seq 1 12); do
  [ -f "$RESULT" ] && break
  sleep 5
done

if [ -f "$RESULT" ]; then
  "$PY" - "$RESULT" "$FLAG" "$LOG" <<'PY'
import json, sys, datetime
result_path, flag_path, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(result_path))
    verdict = d.get('gate', {}).get('verdict', 'UNKNOWN')
    metrics = d.get('gate', {}).get('metrics', [])
    summary = {
        'verdict': verdict,
        'metrics': [{'metric': m.get('metric'), 'value': m.get('value'),
                     'threshold': m.get('threshold'), 'ok': m.get('ok')} for m in metrics],
        'results_r3a': d.get('results', {}).get('r3a', {}),
        'generated_at': datetime.datetime.utcnow().isoformat() + 'Z',
    }
    json.dump(summary, open(flag_path, 'w'), ensure_ascii=False, indent=2)
    with open(log_path, 'a') as f:
        f.write(f"[{datetime.datetime.utcnow().isoformat()}Z] handoff written: verdict={verdict}\n")
except Exception as e:
    with open(log_path, 'a') as f:
        f.write(f"[{datetime.datetime.utcnow().isoformat()}Z] ERROR extracting: {e}\n")
PY
else
  echo "[$(date -u +%FT%TZ)] RESULT file not found after wait" >> "$LOG"
fi
