# 任务调度器（Dispatcher Infrastructure）— QXEN-CD Codex Stable 0.2.0

> **QXEN-CD 方法论状态：R1.9 CLOSED（2026-08-13）**
> 冻结资产 28 项 SHA256 全 OK、79 条终态回归 78/79 PASS（唯一失败 RV17-E007 为
> 已决策豁免的已知限制）。详见 [`reports/r1.9/closure_report.md`](reports/r1.9/closure_report.md)
> 与 [`CLOSED.md`](CLOSED.md)。

这是一个**通用任务调度基础设施**，不是任何项目的一部分。
它与业务项目完全解耦，可以服务任何 target workspace。

## Stable release

`0.2.0` is the current Codex Stable release. It is the recommended public
baseline for Codex integrations and includes the deterministic P0/P1 capsule
lifecycle, active-turn context-pressure routing, source-safe Guard behavior,
and long-text advisory compaction.

The stable production path is:

```text
long text -> provider (advisory) -> lightweight validation -> ADVISORY capsule
          -> deterministic compact/crop -> Context Burden gate
          -> INJECT_QXEN only when final_gpt_payload_chars/direct_source_chars < 1
```

High-risk evidence tasks use the full deterministic Guard. Long-text advisory
capsules do not require `key_evidence`; missing that optional field does not
create a false fallback. The public package remains provider-neutral and does
not load model weights.

Context savings are measured only by the payload that actually enters the main
agent context. Full MCP envelopes, debug metadata, raw model output, and rolling
`compact_state` are not counted as savings payload. If compacting produces no
accepted capsule, or if the minimal `gpt_context_payload` is not shorter than
directly reading the source, the adapter returns `BYPASS_QXEN` and the host
should use the source path or targeted retrieval instead of injecting QXEN text.

## Codex integration boundary

The Codex adapter is the most stable documented host integration in this
release. It is intentionally host-neutral: Codex supplies the provider and
hooks, while QXEN-CD supplies deterministic validation, compaction, capsule
state, and audit primitives. The public repository does not include personal
Codex configuration, SessionStart/UserPromptSubmit hooks, credentials, local
paths, or private session state. See `integrations/codex/README.md` and
`docs/architecture.md` for the exact boundary.

系统有两种决策层后端 mode：

- **deepseek mode**：DeepSeek API（`deepseek_dispatcher_mcp.py`，需 `.env.local` 中的 API key）
- **kimi mode**：Kimi Code 订阅版 K3（`kimi_dispatcher_mcp.py`，走 kimi CLI 的 OAuth 登录，无 API key）

两种 mode 的 MCP 工具接口、prompt、schema 校验、严格重试纪律完全一致，可互换。

入口分工（v2 为当前推荐架构，v1 入口保留作对照）：
- **v2 deepseek mode**：`cn --config .continue/dispatcher-agent-v2.yaml`
  （Continue CLI + DS flash agent）
- **v2 kimi mode（B 路线）**：Kimi CLI + K3 agent，见下方"kimi mode v2（B 路线）"一节
- **v2 kimi mode（Continue 版，备选）**：`cn --config .continue/dispatcher-agent-kimi-v2.yaml`
- v1 deepseek mode（qwen 当 agent，已证明不可靠）：`cn --config .continue/dispatcher-agent.yaml`
- v1 kimi mode（同上）：`cn --config .continue/dispatcher-agent-kimi.yaml`

（kimi mode 早期曾用 Kimi Code CLI 做执行层 harness——`kimi-mode/executor-agent.md`
+ target workspace 的 `.kimi-code/mcp.json`——因 kimi 0.35.0 对 ollama 的思考关闭与
effort 注入不生效、print 模式工具调用不稳定，已弃用，文件保留作参考。
注意：弃用的是"qwen 9B 经 kimi CLI 当 executor"，不是 kimi CLI 本身；
K3 经 kimi CLI 当 agent 是其原生用途，即 B 路线。）

## v2 架构（当前推荐，2026-08-13）

v1 把最弱的模型放在最难的位置（qwen 9B 当 agent 面对用户 + 长协议 + 工具编排），
实测全面失败（幻觉、非法工具调用、空回合）。v2 把角色反转：**强模型做 agent，
弱模型做函数**。

```
User
  ↓
Continue CLI（cn --config .continue/dispatcher-agent-*-v2.yaml）
  ↓
deepseek-v4-flash  = Agent（唯一的 agent：面对用户、跑循环、做验收）
  │
  ├─ MCP: Dispatcher（deepseek mode / kimi mode，不变）
  │    dispatcher_health / dispatch_next_task / request_decision
  │
  └─ MCP: LocalQwen（local_qwen_mcp.py → ollama qwen3.5:9b，think:false，零额度）
       local_health / local_distill(source_path) / local_summarize_files(paths)
       local_extract_failure(log_path) / local_classify
       每个工具 = 单轮窄 prompt + 输出 schema 硬限长 + 失败重试 1 次 + FALLBACK 降级
```

关键机制：

- **路径优先的上下文外蒸馏**：本地大文件、safe_run 产物和日志只传
  `source_path`/`log_path`，由 MCP 内部读取；短文本参数仅作兼容。进入 Agent 历史的应只是
  有界蒸馏结果和必要的局部回源片段。
- **qwen 永远无状态**：不给历史、不给工具、不给协议，只需"读懂一段文本并按
  格式输出"——base 评估已证明它能做到（`enable_thinking=False` 下出标签词）。
- **降级路径**：任一 local_* 工具连续 2 次失败返回 `status=FALLBACK`，DS 自行
  小批量读原文兜底并在批次汇报中注明降级；系统永远不死在 9B 手上。
- **QXEN 咬合**：LoRA adapter 练出后设 `LOCAL_QWEN_MODEL` 即可替换底层模型，
  框架零改动，训练效果直接变成系统能力。

### 如何确认 DS 真的在走 v2 管道（审计方法）

1. **启动锁定**：v2 管道只在 v2 yaml 里注册。用 `cn --config .continue/dispatcher-agent-*-v2.yaml`
   启动；TUI 顶部的 config 名应为 `Dispatcher Executor v2 (...)`。
2. **启动自检**：rules 要求 DS 在会话首次实际工作前确认 `local_health`（返回
   `server=local-qwen, status=OK`）和 `dispatcher_health`。同一会话内不应在每轮
   dispatch 或轮询时重复 local health。
   `local_health` 的 OK 结果会缓存 15 分钟；缓存有效时返回 `cached=true` 与
   `next_probe_after_s`，不访问 Ollama 探针。缓存过期、模型/地址变化或明确异常时
   才重新检查；异常排查可调用 `local_health(force=true)`。
3. **审计日志**：每次 local_* 调用向 `日志/local_qwen.log` 追加一行 JSON
   （time / tool / status / input_chars / output_lines / attempt / latency_s）。
   跑完一个批次后 `tail 日志/local_qwen.log` 即可核查 DS 是否真的把 raw 内容
   走了本地蒸馏、降级了几次。
4. **会话核对**：`~/.continue/sessions/*.json` 中所有 assistant 消息的
   `usage.model` 应全部为 `deepseek-v4-flash`；local_* 工具的输出受 schema
   限长（distill ≤20 行、summarize 每文件 ≤3 行、extract_failure 3 个字段、
   classify 1 个标签词）。
5. **已知边界（2026-08-16 已升级）**：hooks 层强制（UserPromptSubmit →
   `session_bootstrap.py`、PreToolUse → `force_distill.py`）已把"自觉"变成工具层
   确定性守卫：Read 大文件 / Bash 大输出 / 训练进程存在时自动注入蒸馏胶囊，
   触发记录到 `日志/force_distill_guard.log`。仍非完全沙箱——DS 可用内置工具
   绕过；兜底强度 = PreToolUse 守卫 + rules 软约束 + `audit_v2_session.py`
   raw_bypass 事后审计（>20K FAIL 禁止收工）。

### 实时区分 qwen / DS（运行中可见）

- **cn TUI 内**：qwen 只以 `local_*` 工具形式出现——工具调用行带 `local_distill` /
  `local_summarize_files` 等名字的就是 qwen 在跑；其余文本生成全是 DS
  （TUI 顶部模型名锁定 deepseek-v4-flash）。
- **实时观察窗**（推荐，另开一个终端）：

```bash
./venv/bin/python scripts/watch_local_qwen.py        # 跟随新事件
./venv/bin/python scripts/watch_local_qwen.py --all  # 含历史
```

每次 qwen 调用先打 START（`→ ... qwen 开始处理...`），结束时打结果行
（耗时 / 输出量 / 重试次数 / FALLBACK）。此窗口刷的每一行 = qwen 在运行；
DS 的生成不会出现在这里。物理佐证可同时跑 `ollama ps` 看 qwen3.5:9b 驻留。

v2 运行与测试：

```bash
# 离线测试（schema / 重试 / 降级 / 工具注册，不需要 ollama）
./venv/bin/python -m pytest 测试/test_local_qwen.py -v

# 真实 ollama 端到端（需本地 qwen3.5:9b 在线，零额度）
LOCAL_QWEN_RUN_REAL=1 ./venv/bin/python -m pytest 测试/test_local_qwen.py -v

# LocalQwen 模型 / 超时 / 日志路径可用环境变量覆盖：
#   LOCAL_QWEN_MODEL（默认 qwen3.5:9b，QXEN adapter 练出后在此替换）
#   LOCAL_QWEN_TIMEOUT（默认 120 秒）、LOCAL_QWEN_BASE_URL、LOCAL_QWEN_LOG
```

### 自动审计（token 经济）

```bash
./venv/bin/python scripts/audit_v2_session.py                 # 审计最新会话
./venv/bin/python scripts/audit_v2_session.py <session.json>  # 审计指定会话
./venv/bin/python scripts/audit_v2_session.py --json          # 只输出机器可读报告
```

读 Continue 会话文件 + `日志/local_qwen.log`，输出 6 项 PASS/WARN/FAIL 判定，
报告写入 `日志/audit/`，存在 FAIL 时退出码为 1。核心经济指标：

- **avoided_retransmit_tokens**：agent 循环的 prompt 费用是重发费。每次 local_*
  调用让 `input_chars − output_chars` 的 raw 内容免于进入历史，这些字节在之后
  每次 LLM 调用都不再计费：`Σ (避免字节/4) × 该调用之后的剩余调用数`。
- **distill_ratio**：蒸馏输出/输入比，≥50% 判 WARN（蒸馏太弱）。
- **est_savings_usd / est_cost_usd**：费率用 f0b59135 基线隐含混合价
  （$5.67 / 5.29M tokens ≈ $1.07e-6/token），不依赖可能变动的官方报价。
- **raw_bypass**：非 local_* 工具的 >2K 输出总量——未经蒸馏直接进上下文的
  raw 内容，超过 20K 字符判 FAIL。

阴性对照（v1 会话 f0b59135）实测：审计重建出 qwen 乱入 10 次、无 boot 自检、
183K 字符 raw bypass（单文件 53KB 被重复读入 2 次）、est_cost $5.71 对实际
$5.67（误差 <1%），overall FAIL。

---

## kimi mode v2（B 路线：K3 回归 agent，2026-08-13）

Continue 无法直接调 K3（订阅走 OAuth，无 API key），所以 v2 kimi mode 的
Continue 版 agent 只能委屈用 DS flash。B 路线把 agent 还给 K3——
**kimi CLI 本身就是 K3 的原生 harness**：

```
User
  ↓
Kimi CLI（K3 = agent，订阅额度，原生 agent 能力）
  ├─ MCP: kimi-dispatcher（决策层 = K3 经 kimi -p 子进程，有界批次/升级审批，不变）
  └─ MCP: local-qwen（本地蒸馏工具集，零额度——省 K3 上下文重发 = 省订阅额度）
```

### 启动方法

```bash
# 0. 一次性准备：
#    a. ollama 在线且 qwen3.5:9b 可用
#    b. target workspace 已被 kimi CLI 信任（在该目录交互式跑一次 kimi 并确认信任；
#       无头模式无法弹信任确认，未信任时项目级 .kimi-code/mcp.json 被静默忽略）
# 1. 注册双 MCP server（kimi-dispatcher + local-qwen）：
mkdir -p <target_workspace>/.kimi-code
cp /Users/hillo/Desktop/任务调度器/kimi-mode/mcp.json <target_workspace>/.kimi-code/mcp.json

# 2. 启动 K3 执行层会话：
cd <target_workspace>
kimi --agent-file /Users/hillo/Desktop/任务调度器/kimi-mode/executor-agent-k3.md -m kimi-code/k3-256k
```

两个 MCP server 的工具已全局放行（`~/.kimi-code/config.toml`：
`mcp__kimi-dispatcher__*` 与 `mcp__local-qwen__*`，均只读无副作用）。
文件/Shell 操作仍在交互式会话中按需批准。

### Continue CLI vs Kimi CLI（实测对比）

| 维度 | Continue CLI (cn 1.5.47) | Kimi CLI (0.35.0) |
|---|---|---|
| agent 模型 | DS flash + 任意 OpenAI 兼容 + ollama，模型随便换 | K3 原生最强；第三方 provider 不成熟 |
| 本地 qwen 适配 | `think:false` 经 extraBodyProperties 已验证生效 | `default_effort` 注入对 ollama 失效，9B executor 已证不可用 |
| 成本模型 | DS API 按 token 计费（f0b59135 单 $5.67） | 订阅沉没成本；但有周额度 + 5 小时限流窗 |
| 审计链 | session json 含 usage.model/tokens，audit_v2_session.py 已建 | wire.jsonl 格式不同，审计适配是已知缺口（当前靠 local_qwen.log + 批次汇报） |
| 配额风险 | 无窗口限制，重负载随便跑 | 重负载批次可能撞 5h 限流 |
| MCP 注册 | yaml `mcpServers`，无信任概念 | 项目级 mcp.json + workspace trust + 细粒度 permission rules |

### deepseek mode 要不要也换到 Kimi CLI？

**不换。** 理由：

1. DS flash 在 Continue 上已验证能干活（f0b59135 整单），审计链完整——
   换掉等于把唯一有数据背书的执行链拆了重建；
2. Kimi CLI 接 DeepSeek API（自定义 openai provider）**零验证**，
   而它在第三方 provider 上的前科就是 ollama 思考关不掉那件事；
3. 两个 harness 并存没有实际成本：MCP server（dispatcher / LocalQwen）是两个
   harness 共享的，审计脚本针对 Continue，kimi mode 的额度账看 local_qwen.log。

选型建议：订阅额度充裕、想省 DS API 钱 → kimi mode B 路线；
要跑重负载长批次、不想撞 5h 窗口 → deepseek mode（Continue）。


## v1 架构（已废弃，仅作历史对照）

> v1 让 qwen3.5:9b 当 agent 跑 dispatch 循环，实测全面失败（2026-08-13：
> 幻觉状态更新、幻觉工具名 `read_file`、非法参数、空回合零输出）。
> 以下内容仅保留作设计演化的对照，当前请使用上方 v2 架构。

```
User
  ↓
Continue CLI           = Agent Harness
  ↓
Qwen3.5 9B             = Executor（执行层：扫描/阅读/检索/压缩/执行/验证/蒸馏）
  │
  ├─ 本地处理高 token 低智力密度工作（grep / read / pytest / lint / 简单修复 / checkpoint）
  │
  ├─ dispatch_next_task → DeepSeek（有界批次拆分，只读蒸馏 STATE）
  │
  └─ request_decision  → DeepSeek（架构抉择 / 连续失败升级 / 高风险审批）
```

| 角色 | 职责 |
|------|------|
| DeepSeek | 决策层：只做高价值决策，只读蒸馏后的 STATE（2K~8K token），绝不执行任何工作 |
| Qwen3.5 | 执行层：唯一执行者，处理高 token 消耗工作；本地自修最多 2 次，之后升级 DeepSeek |
| Continue CLI | Harness：Agent Loop，连接二者 |

**核心原则：**
```
高 token × 低智力密度  -> Qwen 本地处理
低 token × 高决策密度  -> DeepSeek 处理（绝不接收 raw context）
```
DeepSeek 不是"每步都问的大脑"，而是"决策节点才问的战略调度器"。
100 个原子任务 ≈ 25 个有界批次 ≈ 25 次 dispatch + 少量 escalation，而非 100 次调用。

## MCP Tools

```
dispatcher_health()
    -> 健康检查，不调 DeepSeek API

dispatch_next_task(overall_goal, completed_tasks=None, current_state=None, constraints=None)
    -> 返回唯一一个"有界批次" TASK（allowed_actions + stop_conditions）/ DONE / BLOCKED
    -> completed_tasks 必须是蒸馏摘要（T001: PASS ...），禁止 raw output

request_decision(question, context, options=None, constraints=None)
    -> 返回 DECISION / BLOCKED
    -> 用于：架构/方向选择、连续失败升级（自修 2 次后）、高风险操作审批
```

**有界批次 TASK 格式：**

```json
{
  "status": "TASK",
  "task_id": "T001",
  "title": "完成 smoke workspace 基础文件初始化",
  "goal": "...",
  "reason": "...",
  "inputs": [],
  "allowed_paths": [],
  "forbidden_paths": [],
  "actions": [],
  "allowed_actions": [
    "授权本地完成的确定性子步骤1",
    "子步骤2",
    "子步骤3"
  ],
  "stop_conditions": [
    "任一验收失败即停止",
    "需要操作 allowed_paths 之外路径即停止"
  ],
  "acceptance_criteria": ["可客观验收的标准"],
  "do_not_do": []
}
```

每次调用只允许返回**一个**有界批次，禁止 "tasks" 数组、禁止提前分发未来任务。
`allowed_actions` 应为 3~5 个确定性子步骤，全部由 Executor 本地完成并逐一客观验收。

## DeepSeek 调用门控（Executor 侧）

```
1. 批次内还有授权子步骤未完成           -> 本地继续执行（不问 DS）
2. 批次内出现任一 stop_condition        -> 立即停止，dispatch_next_task 报告状态
3. 下一步任务有歧义                     -> dispatch_next_task（蒸馏 STATE）
4. 需要架构/方向/兼容性/数据结构决策    -> request_decision
5. 连续失败 >= 2 次（自修 2 次仍 FAIL） -> request_decision（升级）
6. 高风险操作（删大量文件/迁移/核心重构/改 Dispatcher/改 schema）-> request_decision（审批）
7. checkpoint 需要重新规划              -> dispatch_next_task
8. 其它：确定 + 低风险 + 验收明确       -> 本地执行（仅限已授权子步骤）
```

自修纪律：attempt #1 FAIL → 读错误自行修复；attempt #2 FAIL → ESCALATE。禁止第三次盲目重试。

## STATE 蒸馏格式（发给 DeepSeek 的唯一输入）

```
goal:              总目标
completed:         T001: PASS alpha.txt 已创建并验证
current_failure:   test / expected / actual（各一行）
relevant_code:     file / lines / 一句话 summary
verified_facts:    [已证实的客观事实]
constraints:       [约束]
question:          需要 DeepSeek 决定的具体问题
```

**DeepSeek 输入禁止出现：** 完整聊天记录、完整 Bash 历史、完整源码、完整 pytest 输出、所有项目文件。
**质量保障靠验证器：** 单元测试 / schema validation / diff check / 文件 hash / compiler > 昂贵模型判断。

## 目录结构

```
任务调度器/
├── deepseek_dispatcher_mcp.py    # Dispatcher MCP Server（决策层）
├── smoke_dispatcher_mcp.py       # 确定性 smoke Dispatcher（隔离故障层，同构）
├── README.md
├── .env.local                    # DeepSeek API 配置（勿提交/勿泄露）
├── requirements.txt
├── venv/                         # 独立 Python 环境（不与其他项目共用）
├── .continue/
│   ├── dispatcher-agent-v2.yaml      # [推荐] v2 deepseek mode：DS 当 agent + LocalQwen
│   ├── dispatcher-agent-kimi-v2.yaml # [推荐] v2 kimi mode：同上，决策层 = K3
│   ├── dispatcher-agent.yaml         # v1 deepseek mode（qwen 当 agent，保留对照）
│   └── dispatcher-agent-kimi.yaml    # v1 kimi mode（同上）
├── local_qwen_mcp.py           # v2 LocalQwen MCP Server（本地蒸馏工具集，零额度）
├── scripts/
│   └── audit_v2_session.py     # v2 自动审计：token 经济引擎 + 6 项 PASS/WARN/FAIL
├── 调度状态/
│   ├── 任务账本.json              # 总体目标、当前任务、已完成状态
│   └── QWEN执行规则.md            # Qwen Worker 执行纪律（蒸馏 / 门控 / 自修 ×2）
├── kimi-mode/                    # kimi mode（订阅版 K3）
│   ├── dispatcher-agent.md       # 决策层 agent 定义（tools: [] 禁用工具）
│   ├── executor-agent-k3.md      # [B 路线] K3 执行层 agent 定义（kimi CLI harness）
│   ├── mcp.json                  # B 路线注册模板（kimi-dispatcher + local-qwen 双 server）
│   ├── executor-agent.md         # [已弃用] 旧 qwen 执行层 agent 定义（保留参考）
│   └── empty-skills/             # 空 skills 目录（决策层 --skills-dir 用）
├── kimi_dispatcher_mcp.py        # kimi mode Dispatcher MCP Server（决策层 = K3）
├── 日志/                         # Dispatcher / LocalQwen 运行日志（不含 secret）
│   ├── local_qwen.log            # v2 审计日志：每次 local_* 调用一行 JSON
│   └── audit/                    # 自动审计报告（audit_v2_session.py 输出）
└── 测试/
    ├── test_dispatcher.py        # schema / 安全 / 真实 API 测试（deepseek mode）
    ├── test_kimi_dispatcher.py   # 同上 + parse_stream_json（kimi mode）
    ├── test_local_qwen.py        # LocalQwen schema / 重试 / 降级测试（默认离线）
    ├── test_audit_v2.py          # 自动审计测试（合成会话，全部离线）
    ├── end_to_end_test.py        # 无害端到端测试
    └── 临时工作区/               # 无害端到端测试用
```

## 配置

复制 `DEEPSEEK_API_KEY` 与 `DEEPSEEK_MODEL` 到 `.env.local`：

```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-v4-flash
```

API key 仅从 `.env.local` 读取，禁止写入源码 / README / 测试 / 日志 / stdout。

Qwen 模型配置中已加 `stop: ["<|endoftext|>"]`，防止特殊 token 进入历史导致 CLI 崩溃。

## kimi mode v1（qwen 当 executor，已废弃，仅作对照）

> 当前 kimi mode 请用上方「kimi mode v2（B 路线）」：K3 经 kimi CLI 当 agent。
> 本节保留的原因是决策层调用实现（call_kimi）在 B 路线中完全不变。
> 执行层部分（Continue/qwen 当 executor）已随 v1 一并废弃。

kimi mode 把决策层从 DeepSeek API 换成本机 Kimi Code CLI（订阅 OAuth，无 API key）：

```
User
  ↓
[v1] Continue CLI + Qwen3.5 9B Executor（已废弃，qwen 当 agent 实测不可靠）
  ↓
[B 路线] Kimi CLI + K3 agent（见「kimi mode v2（B 路线）」一节）
  │
  ├─ dispatch_next_task → kimi_dispatcher_mcp.py → 子进程 kimi -p → K3（订阅额度）
  └─ request_decision  → kimi_dispatcher_mcp.py → 子进程 kimi -p → K3（订阅额度）
```

额度说明：发往 `managed:kimi-code`（K3）的请求计入订阅周额度与 5 小时限流窗；
本地 Qwen 请求发往 Ollama，不经过 Kimi 服务器，零额度消耗。

决策层调用实现（`kimi_dispatcher_mcp.py` 的 `call_kimi`，B 路线同样适用）：

- `kimi -p <prompt> --output-format stream-json`：NDJSON 输出，取最后一条
  `role=="assistant"` 的消息作为模型回复（text 模式会混入 thinking / 横幅，不可用）。
- `--agent-file kimi-mode/dispatcher-agent.md`：`tools: []` 硬性禁用决策层一切工具调用。
- `--skills-dir kimi-mode/empty-skills`：空目录，避免注入无关 skill 浪费 token。
- system prompt 与 user prompt 拼接为单条 `-p` 输入（CLI 无独立 system role）。
- 无 temperature / max_tokens 控制，靠 prompt 约束 + 既有 schema 校验兜底。
- 模型 / 超时可用环境变量覆盖：`KIMI_DISPATCHER_MODEL`（默认 `kimi-code/k3-256k`）、
  `KIMI_DISPATCHER_TIMEOUT`（默认 300 秒）。

执行层接入步骤：见上方「kimi mode v2（B 路线）→ 启动方法」。

备选入口（Continue 版 v2 kimi mode，agent 为 DS flash、决策层为 K3）：

```bash
cd <target_workspace>
cn --config /Users/hillo/Desktop/任务调度器/.continue/dispatcher-agent-kimi-v2.yaml
```

旧 Kimi CLI + qwen 执行层接入方式（workspace trust + `.kimi-code/mcp.json` +
`kimi --agent-file kimi-mode/executor-agent.md -m ollama/exec9b`）已弃用：
kimi 0.35.0 对 ollama 的 `default_effort="none"` 注入不生效、复杂 prompt 默认思考
无法从配置层关闭，且 print 模式下 9B 执行层对系统提醒固着、工具调用不稳定
（2026-08-13 实测）。`kimi-mode/executor-agent.md` 保留作参考。
注意：被弃用的只是"qwen 9B 经 kimi CLI 当 executor"——K3 经 kimi CLI 当 agent
是其原生用途，即 B 路线；决策层调用（kimi_dispatcher_mcp.py 内部的 `kimi -p`
子进程）也不受此影响，仍是 kimi mode 的正常组成部分。

运行与测试：

```bash
# 启动 kimi mode Dispatcher MCP Server（stdio）
./venv/bin/python kimi_dispatcher_mcp.py

# 离线测试（schema / 解析 / MCP 启动，不耗额度）
KIMI_DISPATCHER_SKIP_REAL=1 ./venv/bin/python -m pytest 测试/test_kimi_dispatcher.py -v

# 完整测试（含真实 K3 调用，消耗少量订阅额度）
./venv/bin/python -m pytest 测试/test_kimi_dispatcher.py -v
```

## 独立 Python 环境

```bash
cd /Users/hillo/Desktop/任务调度器
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
```

调度器拥有完全独立的 venv，不与其他任何项目共享。

## 运行与测试

```bash
# 启动 Dispatcher MCP Server（stdio）
./venv/bin/python deepseek_dispatcher_mcp.py

# 运行自动化测试
./venv/bin/python -m pytest 测试/test_dispatcher.py -v
```

## 可服务的目标工作区

本 Dispatcher 可以服务：

- `/Users/hillo/Desktop/金融模型及数据`（作为 target_workspace）
- 以及未来任何其他 workspace

调度器本身不依赖、不修改任何业务项目。业务项目只是被服务对象。
