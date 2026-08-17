#!/usr/bin/env python3
"""cn CLI 1.5.47 bootstrap-patch 幂等安装/卸载/验证脚本。

背景：@continuedev/cli 1.5.47 的 hooks 框架（UserPromptSubmit/SessionStart/
PreToolUse 等）只实现了配置加载+执行管道，但主循环从不调用 fireEvent()，
导致 settings.json 里注册的 session-bootstrap / force-distill hooks 永不触发。

本补丁绕过 hooks 框架，直接在两处打补丁：
  1. dist/cn.js —— 启动 runCli 前调用 session_bootstrap.py --hook，
     把胶囊写入 process.env.BOOTSTRAP_CAPSULE（fail-open）。
  2. dist/index.js —— streamChatResponse 主循环（TUI+headless 共同发送入口）
     首次取 system message 时把胶囊拼接到 system message 尾部（每进程一次）。

用法：
  python3 scripts/patch_cn_cli.py install   # 安装（幂等）
  python3 scripts/patch_cn_cli.py verify    # 验证补丁状态
  python3 scripts/patch_cn_cli.py uninstall # 回滚到 .bak-bootstrap-patch
"""

import os
import sys
import shutil
from pathlib import Path

CLI_DIR = Path.home() / ".npm-global/lib/node_modules/@continuedev/cli/dist"
INDEX = CLI_DIR / "index.js"
CN = CLI_DIR / "cn.js"
INDEX_BAK = CLI_DIR / "index.js.bak-bootstrap-patch"
CN_BAK = CLI_DIR / "cn.js.bak-bootstrap-patch"

MARK_INDEX = "__BOOTSTRAP_CAPSULE_INJ__"
MARK_CN = "[bootstrap-patch v1]"

INDEX_OLD = (
    "let c=await jn.systemMessage.getSystemMessage(jn.toolPermissions.getState().currentMode)"
    ",d=await xti(s),p=NSr(d,t.chatOptions?.toolOverrides)"
    ",f=await Iti(e,{model:t,llmApi:n,isCompacting:i,isHeadless:s,callbacks:o,systemMessage:c,tools:p})"
)
INDEX_NEW = (
    "let c=await jn.systemMessage.getSystemMessage(jn.toolPermissions.getState().currentMode)"
    ";if(!globalThis.__BOOTSTRAP_CAPSULE_INJ__&&process.env.BOOTSTRAP_CAPSULE)"
    "{globalThis.__BOOTSTRAP_CAPSULE_INJ__=1;c=c+String.fromCharCode(10,10)+process.env.BOOTSTRAP_CAPSULE}"
    "let d=await xti(s),p=NSr(d,t.chatOptions?.toolOverrides)"
    ",f=await Iti(e,{model:t,llmApi:n,isCompacting:i,isHeadless:s,callbacks:o,systemMessage:c,tools:p})"
)

CN_TEMPLATE = """#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { runCli } from "./index.js";

// [bootstrap-patch v1] 每进程启动注入 session-bootstrap 胶囊到 env（fail-open，不阻断启动）
try {{
  const out = execFileSync(
    "/Users/hillo/Desktop/任务调度器/venv/bin/python",
    ["/Users/hillo/Desktop/任务调度器/scripts/session_bootstrap.py", "--hook"],
    {{
      input: JSON.stringify({{ session_id: "cn-patch-" + process.pid, cwd: process.cwd() }}),
      encoding: "utf8",
      timeout: 15000,
    }},
  );
  const cap = (out || "").trim();
  if (cap) process.env.BOOTSTRAP_CAPSULE = cap;
}} catch (e) {{
  console.error("[bootstrap-patch] 胶囊注入失败(fail-open):", e.message);
}}
await runCli();
"""


def status() -> dict:
    index_patched = INDEX.exists() and MARK_INDEX in INDEX.read_text(encoding="utf-8", errors="replace")
    cn_patched = CN.exists() and MARK_CN in CN.read_text(encoding="utf-8", errors="replace")
    return {"index": index_patched, "cn": cn_patched}


def install() -> int:
    st = status()
    if st["index"] and st["cn"]:
        print("已安装（幂等跳过）: index.js + cn.js")
        return 0

    if not INDEX.exists():
        print(f"ERROR: {INDEX} 不存在", file=sys.stderr)
        return 1

    # --- index.js ---
    if not st["index"]:
        s = INDEX.read_text(encoding="utf-8", errors="replace")
        cnt = s.count(INDEX_OLD)
        if cnt != 1:
            print(f"ERROR: 注入点匹配 {cnt} 处（期望 1），中止。", file=sys.stderr)
            return 1
        if not INDEX_BAK.exists():
            shutil.copy2(INDEX, INDEX_BAK)
        s2 = s.replace(INDEX_OLD, INDEX_NEW)
        INDEX.write_text(s2, encoding="utf-8")
        print("index.js 已打补丁（备份: index.js.bak-bootstrap-patch）")

    # --- cn.js ---
    if not st["cn"]:
        if not CN_BAK.exists() and CN.exists():
            shutil.copy2(CN, CN_BAK)
        CN.write_text(CN_TEMPLATE, encoding="utf-8")
        print("cn.js 已打补丁（备份: cn.js.bak-bootstrap-patch）")

    # 语法校验
    import subprocess
    for f in (INDEX, CN):
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"ERROR: {f.name} 语法校验失败:\n{r.stderr[:500]}", file=sys.stderr)
            return 1
    print("语法校验通过: index.js / cn.js")
    print("补丁完成。下次重启 cn 会话生效。")
    return 0


def uninstall() -> int:
    st = status()
    if INDEX_BAK.exists():
        shutil.copy2(INDEX_BAK, INDEX)
        print("index.js 已回滚")
    elif st["index"]:
        print("未找到备份，index.js 保持现状（可手动还原）", file=sys.stderr)
    if CN_BAK.exists():
        shutil.copy2(CN_BAK, CN)
        print("cn.js 已回滚")
    elif st["cn"]:
        print("未找到备份，cn.js 保持现状（可手动还原）", file=sys.stderr)
    return 0


def verify() -> int:
    st = status()
    print(f"index.js 补丁: {'已装' if st['index'] else '未装'}")
    print(f"cn.js 补丁:   {'已装' if st['cn'] else '未装'}")
    return 0 if st["index"] and st["cn"] else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    return {"install": install, "uninstall": uninstall, "verify": verify}.get(cmd, verify)()


if __name__ == "__main__":
    sys.exit(main())
