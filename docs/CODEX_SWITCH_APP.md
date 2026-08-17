# codex 双边回滚 app（codex_switch）

> 目标：在 **ChatGPT 官方账号** 与 **necodex 代理账号** 之间双向切换 codex，
> 并把两边 Codex 桌面端侧边栏的会话/thread 记录**互相迁移**，切换后侧边栏显示
> 目标账号自己的会话列表。

## 背景与现状（2026-08-16 盘点）

- 两个 legacy 单向脚本只切换 `~/.codex/config.toml` 的 `model_provider`：
  - `回滚ChatGPT官方.command`：necodex → chatgpt（会**删除** `[model_providers.necodex]` 段）
  - `scripts/rollback_codex_necodex.sh`：chatgpt → necodex（只插 provider 行，**不重建段**）
- 侧边栏数据存于 `~/.codex/.codex-global-state.json`，是**单一共享副本**，
  无按账号分侧存储；`sessions/` 下的会话 jsonl 不随账号切换而迁移。
- legacy 的"切回 chatgpt 删段 / 切到 necodex 不建段"导致**双向不可回环**：
  一旦删掉 necodex 段，再切回 necodex 时 provider 指向不存在的段。

## 统一入口

### `scripts/codex-switch.sh`（推荐，GUI + CLI 均可）

```bash
./scripts/codex-switch.sh --to chatgpt          # 切到 ChatGPT 官方（含侧边栏迁移）
./scripts/codex-switch.sh --to necodex          # 切到 necodex 代理（含侧边栏迁移）
./scripts/codex-switch.sh --to chatgpt --no-sidebar   # 只切 provider（旧行为）
./scripts/codex-switch.sh --status              # 只看状态与快照
./scripts/codex-switch.sh --to chatgpt --yes    # 跳过 GUI 确认（自动化/测试）
./venv/bin/python scripts/codex_switch.py --migrate-auth kimi  # 迁移旧明文 Kimi token
./scripts/codex-switch.sh                       # 未指定目标 -> 自动切到"另一边"
```

### GUI（Finder 双击）

- `codex双边回滚.command`：弹窗选择「切换账号 / 只看状态」。
- `回滚ChatGPT官方.command`：legacy 入口，保留原名与 GUI 确认，委托统一入口切到 chatgpt。

### 后端 `scripts/codex_switch.py`

| 参数 | 说明 |
|---|---|
| `--to openai\|necodex\|kimi\|deepseek` | 目标 provider |
| `--status` | 显示当前 provider 与侧边栏快照列表 |
| `--no-sidebar` | 只切 provider，不做侧边栏迁移 |
| `--migrate-auth kimi\|deepseek` | 将旧的文件内 token 迁移为 `env_key`，并交给 macOS launchd 供桌面端继承 |
| `--log <path>` | 追加日志 |

环境变量（测试/自动化可覆盖）：`CODEX_HOME`（默认 `~/.codex`）、
`SIDEBAR_BACKUP_DIR`（默认 `<CODEX_HOME>/backups`）。

## 工作流程（切换一次）

0. **前置：退出 Codex 桌面端**（侧边栏迁移时）。桌面端运行时会把内存/sqlite
   里的聊天状态写回 `.codex-global-state.json`，会覆盖第 6 步的恢复 → 聊天列表
   不切换。GUI 流程会先弹窗确认并退出 ChatGPT.app（`--yes` 自动化直接退出）；
   无法退出或未退出时，后端用 `--force` 放行但明确告警。
1. **幂等检查**：当前已是目标 provider → 直接退出，不产生新备份。
2. **备份 config.toml** → `~/.codex/config.toml.bak-switch-<target>-<时间戳>`。
3. **快照离开方侧边栏** → `~/.codex/backups/sidebar-<leaving>-<时间戳>.json`
   （只提取 `SIDEBAR_KEYS` 字段，非侧边栏字段不动）。
4. **确保目标 provider 段存在**：段缺失时从注册表或历史备份恢复；Kimi
   同时挂载本地 model catalog，避免端点收到不支持的 `web_search` 工具。
5. **切换 provider**：更新 `model`/`model_provider`，并按目标 provider 维护
   `model_catalog_json`；Kimi/DeepSeek 认证使用 `env_key`，不把 token 留在配置文件。
6. **恢复目标账号侧边栏**：取目标账号最近快照**整段替换**回
   `.codex-global-state.json`（快照未覆盖的 `SIDEBAR_KEYS` 一律清除，
   值为 None 的字段也清除）——离开方的聊天/thread 数据不残留；
   无快照则保留当前共享副本作为基线。
7. **验证** provider 已切换。

### 侧边栏迁移的字段（SIDEBAR_KEYS）

`projectless-thread-ids`、`pinned-thread-ids`、`project-order`、
`thread-workspace-root-hints`、`thread-project-assignments`、`selected-project`、
`active-workspace-roots`、`electron-saved-workspace-roots`、`local-projects`、
`queued-follow-ups`。

快照/恢复只覆盖这些字段；`electron-main-window-bounds` 等非侧边栏字段永不触碰。

## 安全边界

- 只写 `~/.codex/config.toml` 与 `~/.codex/.codex-global-state.json`。
- **不触碰** `~/.codex/sessions/`、`global-state/` 等会话 jsonl 数据目录（只读）。
- 写文件用原子替换（tmp + `os.replace`）；每次写前先备份。
- 幂等：重复同方向切换不产生额外备份、不丢数据。
- 切到 necodex 时若无段且无备份可恢复 → 中止并报错（不写坏配置）。

## 测试

```bash
./venv/bin/python -m pytest tests/test_codex_switch.py -v   # 或用内置 runner
./venv/bin/python tests/test_codex_switch.py
```

覆盖（沙箱 `CODEX_HOME`，不触碰真实 `~/.codex`）：

1. 首次双向切换 + 备份/快照；
2. 双向幂等（重复同方向零副作用）；
3. 侧边栏记录互相迁移（round-trip：chatgpt 快照 → necodex 恢复 → 切回还原）；
4. `--no-sidebar` 保留旧行为；
5. necodex 段缺失时从备份恢复（双向可回环）；
6. `--status`；
7. 恢复为整段替换（离开方聊天数据不残留、None 字段清除）；
8. Codex 桌面端运行时拒绝切换 + `--force` 放行。

真实环境验证方法：切换前自动退出 Codex 桌面端，切换后可选择重新打开；
日志 `日志/codex-switch.log`；状态与快照用 `./scripts/codex-switch.sh --status` 查看。
