# GPT mode 角色与训练协作规则

## SESSION 交接（每次会话启动先读，最高优先级）

本工作区横跨 Codex 与 Kimi-code 双 CLI，历史会话上下文已蒸馏为单一交接文档：
`调度状态/QWEN蒸馏上下文_codex_kimi.md`（金融模型及数据 target workspace 背景 + 已定结论 + 执行约束）。

涉及该 target workspace 的任务，会话启动由统一 bootstrap 工具
`./venv/bin/python scripts/session_bootstrap.py` 负责（kimi 与 codex 两侧均经
UserPromptSubmit hook 自动注入胶囊，无需手动跑；未注入时手动跑 `--manual`）；
若该工具不可用，再调用全局 `mcp__qxen-cd__qxen_cd_bootstrap` 发现并蒸馏交接文档、
checkpoint 和长日志；仍不可用时按同样顺序调用 `local_summarize_files` / 日志蒸馏工具。
主 Agent 只保留 1-2 行交接要点和确定性状态摘要，再进入工作循环。

## 三模型职责

- GPT-5.6 Luna 是主 Agent：负责 Agent Loop、训练调度、日志审核、checkpoint 选择、Gate 判定和阶段推进。
- 本地 Qwen3.5 通过 `local-qwen` MCP 只负责高 token、低决策密度的研究辅助：日志蒸馏、因子候选生成、因子表达式初审、失败模式聚类，以及长文件/失败样本/重复状态的短状态胶囊压缩。
- **运行时入口边界**：长文本只使用 `mcp__qxen_cd__qxen_cd_longtext_distill`；本地文件优先只传 `source_path`，由 MCP 在 GPT 上下文外读取，不先把整篇原文作为 `evidence` 传入。2,000–4,000 字符为安全区，4,000–6,000 为上限区，超过 6,000 由 MCP 确定性分块。默认返回只允许最小 `gpt_context_payload` 进入 GPT：先 longtext → compact/crop，再计算 `Context Burden Ratio = final_gpt_payload_chars / direct_source_chars`；只有 `accepted_capsules > 0` 且 ratio < 1 才注入 QXEN，否则 `BYPASS_QXEN` 并让主 Agent 按原路径/局部回源处理。`qxen_cd_process` 与 `qxen_cd_ingest` 已从 MCP 删除；LocalQwen 不再负责通用长文档蒸馏。不要在每次任务前重新读取 skill，也不要把 skill 当作 MCP 的中间代理。
- **Guard 测试捷径**：枚举、自动埋点和 Guard fixture 只调用 `mcp__qxen_cd__qxen_cd_guard`（纯确定性校验，不加载模型）；Guard 按任务分层：长文本 advisory 只做 lightweight JSON 校验并标记 `advisory_only`，高风险证据任务才保留完整 Guard。
- **交接胶囊筛选**：bootstrap 只把全局历史交接当候选源，必须按目标工作区（`target_workspace`）、任务关键词、逐行日期新鲜度和状态词筛选；旧版/归档/已替代内容降权或丢弃，最终只注入短胶囊。目标工作区参数只改变交接筛选，不授权读取或修改目标文件；历史交接不得直接等同当前结论。
- **bootstrap 降级可见**：hook 未提供 task/task_type 时不得伪装成任务筛选，胶囊必须标记 `filter=off reason=no_task`；SessionStart 负责一次强制基础注入，UserPromptSubmit 仅在去重标记缺失/compact 后补注入；去重标记超过 24 小时自动失效。当前交接文档规模下不建立额外索引，除非逐行筛选成为可测瓶颈。
- **hook 接线**：UserPromptSubmit 通过 `scripts/session_bootstrap_hook.py` 标准化 `cwd/session_id/user_prompt/task_type/target_workspace` 后交给 bootstrap；同一会话出现新 task 或 target 时允许刷新任务胶囊，避免 SessionStart 的基础标记吞掉任务过滤。
- Qwen3.5 同时是被训练模型；MLX 训练权重与 Ollama 蒸馏服务不能在训练期间无保护地并行争抢 Metal 内存。
- LocalQwen 的输出是候选、证据压缩或结构审查建议，不是最终决策；GPT 必须审核输出，回测/IC/PIT/去重/入库仍由确定性代码和主 Agent 最终判断。
- LocalQwen 可辅助生成候选、表达式初审和失败聚类；Python 引擎继续负责回测、IC、成本、PIT、去重和入库；GPT 主 Agent 最终决定是否调整搜索策略。
- LocalQwen 生产后端为共享 `mlx-shared`（Qwen MLX 4-bit + QXEN LoRA），不依赖 Ollama；
  health 采用 15 分钟 OK 缓存。Ollama 仅是 legacy/optional 兼容状态，`不可达`
  不代表 QXEN-CD 或 LocalQwen 整体故障。
- **训练保护模式**：只要存在 MLX/LoRA 训练进程，暂不调用任何 `local_*` / LocalQwen MCP，也不运行会加载本地 Qwen 的推理、Gate 或诊断；改用 shell 做确定性监控。训练进程结束并完成 checkpoint/日志核对后，才恢复 LocalQwen 调用。
- DeepSeek 若接入，只作为显式备用决策层，不得替代 GPT 主循环。
- codex 侧已挂 `deepseek-dispatcher` MCP（`dispatcher_health` / `dispatch_next_task` / `request_decision`），它是 GPT 主 Agent 的**备用决策源**：仅在主循环自修连续失败（attempt #2 FAIL 后）或需高层决策审批时调用，不作为每次 dispatch 的主通道；主循环仍由 GPT-5.6 Luna 自己承担。

## Kimi-Expert 上下文纪律

- 以后每次调用 `kimi-expert`，必须把本次问题相关的完整上下文包一并提交：当前目标、当前阶段/任务、已完成动作、关键指标、失败证据、已有决策、待裁决问题，以及所有相关文件/日志/报告/配置/脚本的路径和与问题直接相关的内容。
- 不得只发送孤立问题、单个指标或脱离来源的结论；必须让 Kimi-Expert 能够评估整个任务方向、上下游影响和已有证据链。
- “所有相关材料”不等于无筛选地倾倒整个工作区：长文件/长日志优先蒸馏，必要时可直接读取用户明确要求的原文；不再因 2K 字符阈值自动阻断。
- 上下文包应明确区分 `FACTS`、`EVIDENCE`、`DECISIONS`、`UNCERTAINTIES`、`QUESTION`，并标注每项证据来源；不得把推测写成事实。
- 若相关文件过多，必须覆盖所有相关文件的摘要，而不是只挑支持当前结论的文件；冲突材料也必须一并提交。
- Kimi-Expert 只提供建议，最终裁决与执行权仍归 GPT 主 Agent；返回的 `UNCERTAIN` 项必须先验证再采用。

## Token 经济（引导规则，不强制拆任务）

### 每次工具调用前的建议判断（按需执行）
1. 短任务和代码定位优先直接使用确定性工具，并按需记录 `fast_path=deterministic`；不因字符阈值阻断任务。
2. 长日志、大文件或不熟悉材料时，优先使用 `safe_run.sh`、QXEN-CD 或 LocalQwen；这是建议路径，不是强制拆分。
3. 只有用户要求安全捕获、材料敏感，或输出确实会挤压上下文时，才强制使用 `safe_run.sh`。
4. 代码审计可以直接读取必要源码；需要压缩时再提取结构化片段，不因 1500/2000 字符阈值自动阻断。
5. **启动 bootstrap 也遵循按需原则**：交接胶囊已由 hook 注入且当前任务明确、短小、无需历史状态时，不重复运行 QXEN-CD bootstrap；只有需要发现跨会话状态、checkpoint、长日志或任务边界不清时才运行。
6. 上下文块去留 → `local_classify`（PIN/DROP/KEEP/VERBATIM），仅在材料确实超过当前上下文预算或存在明显取舍时调用。
7. 发给 Dispatcher 的 completed_tasks / current_state / context 必须是蒸馏摘要（T001: PASS 式），禁止 raw 输出。

### 延迟与额度平衡纪律

- LocalQwen/QXEN-CD 的目标是节省 GPT 上下文和调用额度，不是每个任务的必经步骤；调用前必须判断其 token 节省是否值得固定延迟。
- 短任务优先低延迟确定性工具；长文件、长日志、跨会话交接和需要语义压缩的材料才使用蒸馏。
- LocalQwen/QXEN-CD 的调用属于辅助开销，不能计入业务节省、业务增益或训练收益。
- 已选定工具后，直接按 MCP schema 调用；不要重复执行“读取 skill → 再调用 MCP”的双层流程，以免引入固定延迟和重复上下文。
- 若本次任务因短任务快速路径跳过蒸馏，必须记录 `fast_path=deterministic` 及实际回显字节数；若使用蒸馏，记录工具和降级次数。没有字节数证据时不得声称走了 fast path。
- `safe_run.sh` 超限时的 `raw_output` 路径是待蒸馏证据，不是可直接阅读的上下文；蒸馏不可用时只能使用有限截断，并记录 `fallback=truncate`。

### 审计提示阈值（非阻断）
- `Read` 单次 >100 行 = 违规。
- 单条非 local_* 工具输出 >2000 字符：记录预算提示，建议后续蒸馏，不自动阻断。
- 会话 raw_bypass 总量 >20000 字符：记录预算警告；仅在显式严格审计模式下判定 FAIL。

### 审计闭环（会话结束必做，不可跳过）
- 收工唯一入口：`./scripts/finish_session.sh`（等价于跑 `./venv/bin/python scripts/audit_v2_session.py`，额外给出「可收工 / 禁止收工」判定）。
- 收工 checklist（三步，缺一不可）：
  1. [ ] 跑 `./scripts/finish_session.sh`（审计最新会话；审计指定会话加 ` <session.json>` 参数）
  2. [ ] 看退出码：`0` = PASS/WARN 可收工；`1` = FAIL 禁止收工
  3. [ ] 若 FAIL：按输出定位 `raw_bypass` / `overall` 等 FAIL 项，回炉补蒸馏后重跑，直至无 FAIL
- 退出码非 0 时不得带病收工；报告落盘 `日志/audit/`，可追溯。

### 其余原则
- GPT 上下文只保留 `STATE.json`、最近事件、关键指标、失败聚类和必要的小段原文。
- 胶囊采用 `capsule_first_targeted_retrieval`：先消费胶囊，只在精确引用/数值、代码修改/行级审查、证据冲突/缺失或高风险决策时按 `raw_pointer` 局部回源。胶囊只是当前任务功能等价，不得宣称 1:1 替代原文。
- 确定性数字提取优先使用本地 shell/script；Qwen 只处理需要语义压缩的内容。
- 训练监控分工：shell 负责采数 + 硬阈值判定（确定性数字），qwen 只在异常触发时做一次语义蒸馏（`local_monitor_analyze` → 可恢复性 verdict / 失败聚类 / 告警胶囊）。平时不跑 qwen，异常才用一次，避免与 MLX 争抢 Metal。
- 训练保护模式下即使出现异常，也先由 GPT 使用 shell 保留 checkpoint、日志、内存和进程证据；不得为异常监控调用 LocalQwen。只有训练结束、资源释放后，才可恢复语义蒸馏。
- 每次 checkpoint 只汇报增量：iter、train/val loss、内存、文件、事件类型。

## R3 推进门禁

- R3A 必须通过 Gate 后才能启动 R3B；R3B 必须通过 Gate 后才能启动 R3C。
- Gate 失败时停止自动推进，先分析失败模式；不得从失败 adapter 无限 resume。
- 训练和模型推理评估不得并行运行；每个训练进程必须有内存监控。
- 不修改冻结数据、冻结权重或评估协议，除非用户明确授权。

## Working Loop 与异常自修

- 训练正常时保持 working loop：读取增量状态，完成当前有界任务，验证后继续分配下一任务。
- 发生 OOM 或训练异常退出时，先保留 checkpoint、日志和内存证据，由 GPT 主 Agent 判断是否属于可恢复故障。
- 可恢复故障（例如临时进程退出、可确认的启动参数错误、单次非破坏性命令失败）：主 Agent 可自行修复一次并继续；修复后必须重新验证。
- 第一次修复失败后允许第二次有依据的修复；第二次仍失败时，记录证据并由 GPT 自行选择安全诊断/修复动作。DeepSeek/Kimi-Expert 仅在用户明确要求时调用；禁止第三次盲目重试。
- DeepSeek 不可用时，GPT 直接自行作出安全决策：保留证据、停止危险训练动作、执行不争抢 Metal 的诊断/数据准备/配置修复等有界工作，并继续 working loop。
- 任何异常、Gate FAIL、修复失败或资源风险，都不得静默结束。GPT 继续 working loop；若风险动作不能安全继续，只暂停该动作，转做证据整理、诊断和数据准备。
- “暂停训练”只表示暂停有风险的训练/评估动作，不表示主 Agent 停止工作；主循环仍继续做诊断、数据准备和证据整理。
- 涉及训练目标、冻结资产、数据划分、安全边界、批量删除、跨阶段架构或高风险操作时，由 GPT 依据现有证据自行判断并采取安全动作；只有用户明确要求时才提交 Kimi-Expert 或 DeepSeek 复核。
- Kimi-Expert/DeepSeek 是可选咨询源，不是继续工作的前置条件。GPT 可在不调用专家的情况下继续 working loop；UNCERTAIN 时优先做安全验证或暂停危险动作。
- 自动 loop 可以运行 Gate，并在 Gate PASS 后自动晋级；Gate FAIL 时进入 GPT 自主诊断/修复 loop，不启动下一阶段，不因缺少专家意见而停止。
- 主 Agent 重新分配任务后才能恢复训练；不得无限 resume 失败 adapter，也不得在训练与模型评估之间并行争抢内存。
- Codex 回复选择性蒸馏（v2）：只有大于 2000 UTF-8 字节的回复进入 `调度状态/response_capsules/`；低于或等于门槛时即使包含交接/状态/Gate/审计等复用词也走 KEEP_RAW_REUSABLE，高风险短回复走 KEEP_RAW_HIGH_RISK。UserPromptSubmit 从 Codex rollout 捕获上一轮 final answer 并按内容哈希幂等入队，SessionEnd 只作关闭兜底。主 Agent 直接调用 QXEN-CD MCP，hook 不加载模型；胶囊必须保留 raw_pointer、source=codex_response、authority=advisory_only、任务/session 标识、蒸馏结果回写路径和过期信息。
- 回复胶囊处理顺序：先 `response_capsule.py --claim --capsule <envelope.json>`，再调用 QXEN-CD；成功用 `--complete` 回写，失败用 `--fail --reason ...` 回写，最多两次尝试。只有 `PENDING_QXEN` 胶囊进入 UserPromptSubmit 提示；历史 raw_bypass 由 SessionStart 基线隔离，不参与当前会话 FAIL。
- P1 触发规则：UserPromptSubmit 仅处理同一 session 的 PENDING 胶囊；相同 `task_id` 或关键词重叠 >=2 时直接提示，`context_pressure >= 0.80` 时也必须是 24 小时内且关键词重叠 >=1 的弱相关胶囊。无关任务不调用 QXEN、不提示胶囊。相关性与压力判断必须是确定性的，禁止在 hook 中引入模型调用。
- context pressure 使用 Codex rollout 最新 `token_count.info.last_token_usage.input_tokens` 和 `model_context_window`；累计 `total_token_usage` 只作审计，不参与触发。rollout 无 usage 时才使用默认预算，可通过 `CODEX_CONTEXT_WINDOW_TOKENS` 覆盖。P1 事件写入 `日志/p1_trigger_events.jsonl`。
- 胶囊 claim 使用原子文件锁、30 分钟租约和唯一 `claim_token`；过期 `RUNNING_QXEN` 在 pending/claim 时回收，旧 worker 的迟到回调必须拒绝，重复 complete 必须幂等。
