#!/bin/zsh
# =============================================================
# codex-switch.sh —— codex 四路切换 app（统一入口）
# 用法:
#   ./codex-switch.sh --to openai|necodex|kimi|deepseek   # 切换（含侧边栏迁移）
#   ./codex-switch.sh --to openai --no-sidebar
#   ./codex-switch.sh --status
#   ./codex-switch.sh            # 弹出四路选择菜单（GUI 双击场景）
# 安全: 切换前自动备份 config.toml 与侧边栏快照，幂等。
# 聊天列表同步: Codex 桌面端运行时会把聊天状态写回 .codex-global-state.json，
#   直接切换会被覆盖（聊天列表不切换），因此 GUI 切换前会先确认并退出桌面端。
# kimi/deepseek 的 provider 段与密钥登记在 ~/.codex/model_providers_registry.toml
# =============================================================
set -u

ROOT="/Users/hillo/Desktop/任务调度器"
PY="$ROOT/venv/bin/python"
[ -x "$PY" ] || PY=python3
LOG="$ROOT/日志/codex-switch.log"
mkdir -p "$(dirname "$LOG")"
mkdir -p "$ROOT/scripts"

say()  { echo "$1"; echo "$1" >> "$LOG"; }

say "===== $(date '+%Y-%m-%d %H:%M:%S') codex-switch $* ====="

TO=""
NO_SIDEBAR=""
STATUS=0
YES=0
for a in "$@"; do
  case "$a" in
    --to) ;;
    --yes) YES=1 ;;
    --no-sidebar) NO_SIDEBAR="--no-sidebar" ;;
    --status) STATUS=1 ;;
    openai|necodex|kimi|deepseek) TO="$a" ;;
    *) say "❌ 未知参数: $a"; echo "用法: ./codex-switch.sh --to openai|necodex|kimi|deepseek | --status [--no-sidebar] [--yes]"; exit 2 ;;
  esac
done

if [[ "$STATUS" -eq 1 ]]; then
  "$PY" "$ROOT/scripts/codex_switch.py" --status --log "$LOG"
  exit $?
fi
if [[ -z "$TO" ]]; then
  # 未指定目标 -> 弹出四路选择菜单（GUI 双击场景）
  CURRENT=$("$PY" -c "
import sys; sys.path.insert(0,'$ROOT/scripts')
import codex_switch as c
print(c.current_provider() or '')
")
  PICK=$(osascript -e "choose from list {\"openai (ChatGPT 官方)\", \"necodex (代理)\", \"kimi (K3 订阅)\", \"deepseek (V4 Flash)\"} with title \"codex 四路切换\" with prompt \"当前: ${CURRENT:-<未设置>}　→　选择目标:\" default items {\"kimi (K3 订阅)\"}" 2>/dev/null || echo "false")
  case "$PICK" in
    openai*)    TO="openai" ;;
    necodex*)   TO="necodex" ;;
    kimi*)      TO="kimi" ;;
    deepseek*)  TO="deepseek" ;;
    *) say "已取消。"; exit 0 ;;
  esac
  say "菜单选择 -> $TO"
fi

# 幂等快速路径
CURRENT=$("$PY" -c "
import sys; sys.path.insert(0,'$ROOT/scripts')
import codex_switch as c
print(c.current_provider() or '')
")
if [[ "$CURRENT" == "$TO" ]]; then
  say "✅ 已是 $TO 配置，无需切换（幂等通过）"
  "$PY" "$ROOT/scripts/codex_switch.py" --to "$TO" --log "$LOG"
  exit $?
fi

# GUI 确认（--yes 跳过；osascript 不可用时退化为命令行确认）
CONFIRM=no
if [[ "$YES" -eq 1 ]]; then
  CONFIRM=yes
elif osascript -e "display dialog \"将 codex 切换到 $TO 账号？\n\n当前: ${CURRENT:-<未设置>}\n将自动备份 config.toml 与侧边栏记录\" buttons {\"取消\",\"切换\"} default button \"切换\" with title \"codex 双边回滚\" with icon caution" >/dev/null 2>&1; then
  CONFIRM=yes
else
  if [[ -t 0 ]]; then
    printf "确认切换到 %s 账号？(y/N): " "$TO"
    read -r ANS
    [[ "$ANS" == "y" || "$ANS" == "Y" ]] && CONFIRM=yes
  fi
fi
if [[ "$CONFIRM" != "yes" ]]; then
  say "已取消，未做任何修改。"
  exit 0
fi

# 侧边栏迁移前置：Codex 桌面端运行时会把聊天状态写回
# .codex-global-state.json，直接切换会被覆盖（聊天列表不切换），所以先退出桌面端。
QUIT_CODEX=no
FORCE_FLAG=""
if [[ -z "$NO_SIDEBAR" ]]; then
  # Codex 请求进程可能是 ChatGPT.app 内的 Frameworks/.../Helpers/Codex，
  # 不能只匹配 Contents/MacOS/ChatGPT。
  if pgrep -f 'ChatGPT\.app/Contents/' >/dev/null 2>&1; then
    QUIT=no
    if [[ "$YES" -eq 1 ]]; then
      QUIT=yes
    elif osascript -e 'display dialog "Codex 桌面端正在运行。\n\n直接切换会被 Codex 立即覆盖，聊天列表不会切换。\n是否先退出 Codex 再切换？" buttons {"取消","退出 Codex 并切换"} default button "退出 Codex 并切换" with title "codex 双边回滚" with icon caution' >/dev/null 2>&1; then
      QUIT=yes
    elif [[ -t 0 ]]; then
      printf "Codex 桌面端正在运行，直接切换聊天列表不会切换。\n确认退出 Codex 再切换？(y/N): "
      read -r ANS
      [[ "$ANS" == "y" || "$ANS" == "Y" ]] && QUIT=yes
    fi
    if [[ "$QUIT" != "yes" ]]; then
      say "已取消：Codex 桌面端正在运行，为避免切换被覆盖已中止。"
      osascript -e "display notification \"已取消：请先退出 Codex 再切换\" with title \"codex 双边回滚\"" >/dev/null 2>&1
      exit 0
    fi
    say "⏹  正在退出 Codex 桌面端..."
    osascript -e 'quit app "ChatGPT"' >/dev/null 2>&1
    for _ in {1..30}; do
      pgrep -f 'ChatGPT\.app/Contents/' >/dev/null 2>&1 || break
      sleep 0.5
    done
    if pgrep -f 'ChatGPT\.app/Contents/' >/dev/null 2>&1; then
      say "⚠️  Codex 桌面端未在 15 秒内退出，继续切换（请稍后手动重启 Codex 生效）"
      FORCE_FLAG="--force"
    else
      say "✅ Codex 桌面端已退出"
    fi
    QUIT_CODEX=yes
  fi
fi

"$PY" "$ROOT/scripts/codex_switch.py" --to "$TO" $NO_SIDEBAR $FORCE_FLAG --log "$LOG"
RC=$?
if [[ "$RC" -eq 0 ]]; then
  osascript -e "display notification \"codex 已切到 $TO 账号\" with title \"codex 双边回滚\"" >/dev/null 2>&1
  # 切换成功且刚才退出了桌面端 -> 询问是否重新打开
  if [[ "$QUIT_CODEX" == "yes" ]]; then
    RELAUNCH=no
    if [[ "$YES" -eq 1 ]] || osascript -e 'display dialog "切换成功。是否现在重新打开 Codex（ChatGPT 桌面端）？" buttons {"稍后","重新打开"} default button "重新打开" with title "codex 双边回滚"' >/dev/null 2>&1; then
      RELAUNCH=yes
    fi
    if [[ "$RELAUNCH" == "yes" ]]; then
      open -a ChatGPT
      say "🚀 已重新打开 Codex（ChatGPT 桌面端），登录到 $TO 账号"
    fi
  fi
else
  osascript -e "display notification \"切换失败，见 $LOG\" with title \"codex 双边回滚\"" >/dev/null 2>&1
fi

echo ""
echo "完成。日志: $LOG"
exit $RC
