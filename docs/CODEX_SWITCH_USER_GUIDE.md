# codex 双边回滚 app —— 用户操作手册

> 本手册面向非技术用户。跟着步骤做即可安全地在 **ChatGPT 官方账号** 和
> **necodex 代理账号** 之间来回切换 codex，切换时两边侧边栏的会话记录会自动
> 互相迁移——切到哪个账号，侧边栏就显示哪个账号自己的会话列表。

---

## 1. 这个 app 能做什么

| 功能 | 说明 |
|---|---|
| 双向切换 | chatgpt 官方账号 ⇄ necodex 代理账号，两个方向都能切 |
| 侧边栏互相迁移 | 切换时自动保存"离开方"的侧边栏会话列表，切回时自动恢复它自己的列表 |
| 自动备份 | 每次切换前都先备份配置，可追溯、可回滚 |
| 幂等安全 | 重复执行同一方向不会产生副作用，也不会丢数据 |
| 断点可查 | 随时查看当前账号与历史侧边栏快照 |

## 2. 前置条件

- macOS 电脑，已安装 codex（ChatGPT 桌面版）。
- 两个账号都已配置过（历史上使用过 ChatGPT 官方，也使用过 necodex 代理）。
- 有一个终端（Terminal.app 或 iTerm 均可）。Finder 双击 `.command` 文件也会自动打开终端。
- 本项目目录：`/Users/hillo/Desktop/任务调度器`

## 3. 三种打开方式（任选其一）

### 方式 A：Finder 双击（最省事）

在项目目录找到 **`codex双边回滚.command`**，双击。弹出窗口选：

- **「切换账号」**：自动切到"另一边"（当前是 chatgpt 就切 necodex，反之亦然）。
- **「只看状态」**：只查看当前账号和快照，不改任何东西。

### 方式 B：统一入口命令行（推荐，最灵活）

```bash
cd /Users/hillo/Desktop/任务调度器

# 切到 ChatGPT 官方账号
./scripts/codex-switch.sh --to chatgpt

# 切到 necodex 代理账号
./scripts/codex-switch.sh --to necodex

# 只看当前状态（不改任何东西）
./scripts/codex-switch.sh --status

# 如果历史配置里保存过 Kimi 明文 token，只需执行一次迁移
./venv/bin/python scripts/codex_switch.py --migrate-auth kimi
```

Kimi 迁移后会改用 `KIMI_API_KEY` 环境变量，并由 macOS launchd 提供给桌面端；
当前正在使用的 OpenAI provider 不会因此切换。若 Kimi 返回“usage limit”，这是
Kimi 订阅额度问题，不是 Codex 账号路由问题。

如果 macOS 登录或重启后再次提示“未找到 KIMI_API_KEY”，统一入口会自动从
`~/.codex` 下的受保护认证备份恢复到 launchd，再继续切换；token 不会写回配置，
也不会输出到日志。若备份已被删除，才需要重新设置 `KIMI_API_KEY`。

### 方式 C：旧入口（保留原名，行为已升级）

- **`回滚ChatGPT官方.command`**：双击 → 确认 → 切回 ChatGPT 官方账号（现在也带侧边栏迁移）。

## 4. 切换账号（详细步骤）

以「切到 necodex 代理账号」为例：

1. 打开终端，进入项目目录。
2. 执行：`./scripts/codex-switch.sh --to necodex`
3. 屏幕会弹出确认对话框（在终端里操作时也可能直接是 `y/N` 询问），输入 `y` 确认。
4. 终端会依次显示：
   - `正在退出 Codex 桌面端...`（如果 Codex 正在运行，会先弹窗/询问是否退出——**必须退出**，否则聊天列表不会切换）
   - `已备份 config.toml -> ...` （配置已备份）
   - `已快照 chatgpt 侧边栏 -> ...` （当前账号的会话列表已保存）
   - `[model_providers.necodex] 段已就绪` （代理账号配置就位）
   - `切换成功：codex 已切到 necodex 账号`
5. 切换成功后，如果刚才退出了 Codex，会询问是否**重新打开** Codex——选「重新打开」即可看到目标账号自己的聊天列表。

切回 ChatGPT 官方同样操作：`./scripts/codex-switch.sh --to chatgpt`

> 小技巧：不指定目标也可以——`./scripts/codex-switch.sh` 会自动切到"另一边"。

## 5. 侧边栏迁移是怎么发生的

切换动作分三步自动完成，你不需要手动做任何事：

0. **退出 Codex 桌面端**（如果它正在运行）：Codex 桌面端运行时会自己把聊天列表
   写回配置文件，如果它开着，切换进去的聊天列表会被它立刻覆盖回去，等于没切。
   app 会先弹窗/询问并帮你退出 Codex，切换成功后再问你要不要重新打开。
1. **保存离开方**：切换前，把当前账号的侧边栏会话列表保存为快照文件：
   `~/.codex/backups/sidebar-<账号>-<时间戳>.json`
2. **切换账号**：改 `model_provider`，并确保 necodex 代理段存在。
3. **恢复目标方**：把目标账号的最近一次快照**整段替换**到侧边栏
   （离开方的会话记录会被清掉，不会混进目标账号的列表里）。

效果：

- 第一次切到 necodex 时，necodex 还没有自己的快照 → 侧边栏保留当前列表作为它的初始基线。
- 之后你在 necodex 下产生的会话列表，会在下次切走时被保存为 necodex 的快照。
- 每次切回某个账号，都自动显示它**自己的**会话列表，不会混入另一个账号的会话。

只会迁移侧边栏相关字段（会话列表、置顶、项目顺序、项目归属等 10 项），
窗口大小等无关设置不会被碰。

## 6. 查看当前状态与快照

```bash
./scripts/codex-switch.sh --status
```

会显示：

- `当前 provider: chatgpt`（或 `necodex`）
- 已有的侧边栏快照列表（`sidebar-<账号>-<时间戳>.json`）

这是只读操作，随时可以执行。

## 7. 回滚 / 恢复

### 切换回滚

直接反方向再切一次即可，例如：

```bash
# 刚才切到了 necodex，现在想回 chatgpt：
./scripts/codex-switch.sh --to chatgpt
```

### 配置手动恢复（极端情况）

每次切换都会备份配置到 `~/.codex/config.toml.bak-switch-<账号>-<时间戳>`。
如果出现异常想手动还原：

```bash
cp ~/.codex/config.toml.bak-switch-<账号>-<时间戳> ~/.codex/config.toml
```

> 正常情况下不需要手动恢复——app 的切换本身就是安全的。

### 侧边栏快照恢复

快照文件在 `~/.codex/backups/` 下。手动恢复方法是：把某个快照文件里
`keys` 字段的内容合并回 `~/.codex/.codex-global-state.json`。
正常情况下不需要手动做——app 会自动用目标账号的最近快照。

## 8. 故障排查

| 现象 | 可能原因 | 处理办法 |
|---|---|---|
| 提示 `已是 X 配置，无需切换` | 当前就是这个账号 | 正常，幂等保护，无需处理 |
| 提示 `缺少 [model_providers.necodex] 段且无备份可恢复` | 历史备份被清理过 | 手动把含 necodex 段的旧备份 `cp` 回 config 再切，或重新配置代理 |
| 切完没生效 | codex 还在运行 | 重启 codex / 新开终端 |
| 侧边栏没变成目标账号的列表 | 目标账号此前没有快照 | 正常：首次切换会用当前列表作基线，下次就有了 |
| 终端里没有弹出确认框 | 环境不支持 osascript | 直接看终端里的 `y/N` 询问，输 `y` |
| 担心出问题 | — | 切换前都会自动备份，可随时用第 7 节恢复 |

日志文件：`/Users/hillo/Desktop/任务调度器/日志/codex-switch.log`
（每次操作的完整记录都在这里）。

## 9. 安全注意事项

- 切换会修改 `~/.codex/config.toml` 和 `~/.codex/.codex-global-state.json`。
  **每次都先自动备份**，可追溯。
- 本 app **不会**修改 `~/.codex/sessions/` 下的会话数据文件（只读，不动）。
- 不要手动删除 `~/.codex/backups/` 里的快照，它们是侧边栏迁移的依据。
- 切换后**重启 codex** 才生效。
- 想保留旧行为（只切账号、不动侧边栏）：
  `./scripts/codex-switch.sh --to <账号> --no-sidebar`

## 10. 验收清单

照着做，能勾的都勾上，说明 app 工作正常：

- [ ] 1. 执行 `./scripts/codex-switch.sh --status`，能看到当前 provider（chatgpt 或 necodex）
- [ ] 2. 切换前记录当前 provider 与侧边栏有哪些会话
- [ ] 3. 执行 `./scripts/codex-switch.sh --to <另一账号>`，输出包含「已备份 config.toml」
- [ ] 4. 切换输出包含「已快照 <离开方> 侧边栏」
- [ ] 5. 切换输出包含「切换成功：codex 已切到 <目标> 账号」
- [ ] 6. `~/.codex/` 下出现 `config.toml.bak-switch-*` 备份文件
- [ ] 7. `~/.codex/backups/` 下出现 `sidebar-<离开方>-*.json` 快照文件
- [ ] 8. 切换后 `--status` 显示的 provider 与目标一致
- [ ] 9. 再次执行同一方向切换，提示「已是 X 配置，无需切换」，且不产生新备份
- [ ] 10. 反方向切回，输出包含「已恢复 <目标> 侧边栏」，侧边栏会话列表回到之前的样子
- [ ] 11. `~/.codex/sessions/` 下的会话文件在切换前后内容不变
- [ ] 12. 重启 codex 后账号身份与侧边栏列表均符合预期
- [ ] 13. 日志 `日志/codex-switch.log` 有本次操作的完整记录
- [ ] 14. 全程未出现任何「❌ 切换失败」或数据丢失提示

---

*开发者文档见 [`docs/CODEX_SWITCH_APP.md`](CODEX_SWITCH_APP.md)。*
