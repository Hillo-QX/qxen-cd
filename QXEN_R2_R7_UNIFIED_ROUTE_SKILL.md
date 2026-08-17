# QXEN R2–R7 Unified Route Training Skill

> **本文件为统一路线版（REVISION 2026-08-15，Kimi-Expert redefine 后重写）。**
> 覆盖并取代旧版 QXEN_R2_R7_LEGAL_ELEMENT_TRAINING_SKILL.md（7 阶段顺序范式）。
> 备份：`调度状态/QXEN_R2_R7_LEGAL_ELEMENT_TRAINING_SKILL.bak_20260815_091326.md`
> 失败教训附录见文末「附录 A：历史范式与失败教训」。

## 0. 统一路线（单一来源）

### 0.1 裁决来源
Kimi-Expert redefine（2026-08-15，两轮） + R3A 合成主线冻结报告（reports/r3/r3a_synthetic_freeze.md）+ 用户授权架构重构。

### 0.2 核心裁决

> **合并为一条主路线：A 是架构栈（数据如何进模型），B 是能力契约（Gate 考什么）。**
> 放弃「每阶段独立 adapter + 3000 条」旧范式，改走 MVP 小闭环。

依据（已实证）：
- R3A 合成大数据 LoRA 对细粒度时序判别（STALE）负迁移：0-shot 35% → LoRA 15.6%，合成路线失效。
- 12 条真实锚点无法分层留出 20 条 fresh test（数据缺口分析 2026-08-15）。
- ec_v1 40 iters/20 条 19/20 通过率 = **过拟合假象**，不作能力证据；pool 与契约本身是有效资产。

### 0.3 统一路线（可执行）

```
架构栈 A（CD 实现顺序）        能力契约 B（Gate 维度）
──────────────────────        ──────────────────────
1. 证据胶囊生成（R3/R2 子集）   operative_status / authority /
                               evidence-element relation
2. 状态更新（R6 子集）          state patch（ADD/SUPERSEDE/REOPEN…）
3. 最小 agent 闭环              capsule → state → action
R4 / R5 / R7 ──► 规则兜底，不训练（schema 校验、superseded 链校验、冲突校验全部确定性代码）
```

### 0.4 硬性要求

1. **只训练一个 adapter**：`qxen_joint_v1`（evidence_capsule + state_update 联合 LoRA）。不拆 R3A/R3B/R3C/R2/R6 多 adapter。
2. **数据**：100–200 条（真实优先），分 `task_type: capsule | state_patch`，interleave 进同一训练集。数据扩充前取得用户授权。
3. **强验证器兜底**：契约 JSON schema 校验、SUPERSEDED 链校验、冲突对校验全部确定性代码。模型只生成，规则只拒绝。
4. **ec_v1 处置**：`models/ec_v1` 归档为 T001 过拟合快照，不作种子、不 resume。
5. **R3B/R3C 不独立 adapter**：并入统一胶囊契约作 schema 扩展字段，Gate 按 B 维度分别打分。
6. **UNCERTAIN 处理**：100–200 条是否足够无先验，先跑 100 条档验证，Gate FAIL 优先查数据真实性而非加量。

---

# 1. 目标与 Gate 协议

## Objective

统一路线把 R2-R7 的法律推理映射为两个可训练能力 + 五个确定性兜底：

| 能力 | 训练 | Gate 维度 |
|---|---|---|
| 证据胶囊生成（R3/R2） | qxen_joint_v1 (capsule) | operative_status / authority / evidence-element relation |
| 状态更新（R6） | qxen_joint_v1 (state_patch) | patch 操作正确性 |
| R4 保留/保真 | 不训练（规则兜底） | 不可改写字段保真 = 0 篡改 |
| R5 充分性 | 不训练（规则兜底） | 证据不足时 next_step 必须检索非行动 |
| R7 管辖 | 不训练（规则兜底） | 确定性安全覆盖 |

## Gate 协议（双层）

```text
模型输出 → 确定性验证器拦截 → e2e 指标
```

两层指标都报告，**晋级门槛设在 e2e**：

```text
model_raw_accuracy    模型原始正确率（参考）
e2e_accuracy          含规则兜底的端到端正确率（晋级门槛）
invalid_output        = 0（模型非法 JSON 必须为 0）
critical_t0_t1_miss   = 0（兜底不得掩盖关键源缺失）
```

Gate 阈值：
```text
operative_status_accuracy        >= 0.90
authority_accuracy               >= 0.90
evidence_element_relation_acc    >= 0.90
conflict_hide_rate               <= 0.05（显式）/ 0.30（隐含）
traceability_rate                >= 0.95
premature_action_rate            <= 0.05
state_patch_schema_validity      = 1.00
wrongful_finding_overwrite       <= 0.01
invalid_output                   = 0
```

---

# 2. 数据三层来源与扩量阶梯

## 2.1 来源优先级（固定顺序）

```text
1. 真实锚点（pool / real_timeline）—— 已验证
2. 真实轨迹挖掘（dispatcher.log / local_qwen.log / 任务账本）—— 需蒸馏→审核
3. 人工标注（覆盖失败模式）—— 需用户授权
4. 规则化扰动扩增（真实种子上变体）—— 禁止新合成主线
```

## 2.2 扩量阶梯

### 100 档（先做）

```text
capsule 任务：80 train + 20 fresh
  现有 pool 20 条（real 12 + manual 8）
  + 轨迹挖掘 ~46 条（dispatcher.log 1632 行 / local_qwen.log 183 行）
  + anchor_derived 筛选 ~20 条（需人工核验可逆推性）
  + 定向人工 ~14 条（补足 80 train，按 STALE / t0_t1 失败模式）
fresh test 20 条：训练前从真实锚点分层留出（按 task_type 配比），一经划定即冻结

state_patch 任务：冷启动 30–50 条（独立验证可行性）
  来源：任务账本 396 条状态变迁 diff → 脚本抽候选 + 人工校验
  验证通过后再入联合训练，避免污染首轮信号
```

### 200 档（后置，无新机制）

```text
按同配比扩量，不规划新数据源。
仅当 100 档 Gate PASS 且用户授权扩量时启动。
```

## 2.3 数据校验规则

```text
样本可逆推：每个样本能回溯到真实来源（provenance 完整）
fresh 零重叠：fresh test 与 train 零重叠，不得用扩增样本充数
禁纯合成：不得复用 R3A 式纯合成时序
```

---

# 3. Fresh 分层留出规范

## 3.1 留出时机

```text
数据扩充完成后、训练开始前
```

## 3.2 留出方法

```text
按 task_type（capsule / state_patch）分层随机
比例：capsule fresh 20 / 80 train；state_patch fresh 独立留出
一经划定即冻结，写入 manifest（含 sha256）
```

## 3.3 Fresh 纪律（继承旧 §16）

```text
fresh 用于：最终评估
不得用于：checkpoint 选择 / 阈值选择 / prompt 设计 / 标签修正
一旦用于诊断 → 标记 DIAGNOSTIC_ONLY，另建新 fresh
冻结 test 永不移入 replay/training
```

---

# 4. Task-Type 分流联合训练

## 4.1 数据格式

每样本 prompt 头部带 task_type 标签：

```json
{"task_type": "capsule", "prompt": "...", "completion": "..."}
{"task_type": "state_patch", "prompt": "...", "completion": "..."}
```

## 4.2 联合训练

```text
两种 task_type interleave 进同一训练集
同 adapter（qxen_joint_v1）训练
验证按 task_type 分层报指标（不合并）
```

## 4.3 样本配比建议

```text
capsule : state_patch ≈ 2 : 1（首轮）
若 state_patch 验证 30–50 条失败 → 先只训 capsule，state_patch 延后
```

---

# 5. qxen_joint_v1 训练流程

## 5.1 命名与拓扑

```text
唯一 adapter：models/qxen_joint_v1/
基座：models/qwen3.5-9b-mlx-4bit（4bit）
LoRA：rank 8 / num_layers 2 / lr 4e-6 / max_seq_length 448
```

## 5.2 训练纪律

```text
不 resume 失败 adapter
每个训练进程配 memory_monitor.sh（wired ≤ 18GB）
训练与评估不并行（防 Metal 争抢）
每次 checkpoint 只汇报增量
```

## 5.3 训练前置条件（全部 PASS 才启动）

```text
DATA_INVENTORY          100 档配比表确认
FRESH_LAYOUT            fresh 20 条划定并冻结
PROVENANCE              全样本可回溯
TASK_TYPE_BALANCE       capsule:state_patch ≈ 2:1
VERIFIER_READY          契约 schema / 链 / 冲突校验代码就绪
USER_AUTHORIZATION      数据扩充已授权
```

---

# 6. 双层 Gate 评估

## 6.1 指标分层

```text
model_raw_accuracy   模型直接输出正确率（不含兜底）
e2e_accuracy         含规则拦截后的端到端正确率（晋级）
```

## 6.2 兜底拦截点

```text
契约 JSON schema 校验（非法输出 → invalid_output 计数）
SUPERSEDED 链校验（superseded 证据不得当 current）
冲突对校验（显式冲突不得隐藏）
不可改写字段保真（哈希/原文比对）
证据不足 → next_step 必须是检索非行动
```

## 6.3 晋级判定

```text
e2e_accuracy 全部达标 AND invalid_output=0 AND critical_t0_t1_miss=0
→ Gate PASS
任一 FAIL → 先查数据真实性（R3A 教训），不先加 epochs
```

---

# 7. 失败自修与归档纪律

## 7.1 失败分类

```text
可恢复：临时进程退出 / 启动参数错误 / 单次非破坏性命令失败 → 自修一次
需决策：第二次失败 / 架构方向 / 数据源变更 → request_decision
危险：修改冻结资产 / 批量删除 / 跨阶段改动 → 停，升级
```

## 7.2 归档纪律

```text
任何不再推进的 adapter/数据/评估 → 移入 models/_archive/ 只读
归档说明含：是什么、为何归档、失败证据链接
禁止删除失败证据链（用于审计与教训）
```

## 7.3 冻结资产（零改动）

```text
models/r3a_cot_v5/（含全部 checkpoints + 最终权重）
data/r3/r3a_gate_test_ext/（eval_pool 192 + conflict 20）
旧 eval 数据集 / real_timeline 原始文件
评估报告：r3a_realistic_gate_v5_pool_v6.json / conflict20 / zeroshot_stale
```

---

# 8. 规则兜底模块（不训练）

以下能力全部用确定性代码，不进入 LoRA：

| 模块 | 实现 | 校验内容 |
|---|---|---|
| R4 保留/保真 | contract schema | immutable_fields 保真 = 0 篡改 |
| R5 充分性 | 关键词规则 | 证据不足时 next_step 必须检索非行动 |
| R7 管辖 | 确定性路由 | LOCAL / DS / K3 / ABSTAIN 安全覆盖 |
| SUPERSEDED 链 | 链校验 | superseded 不得当 current |
| 冲突对 | 文本比对 | 显式冲突隐藏率 ≤ 0.05 |

---

# 9. 通用纪律（继承旧 §15–§25，保留原文）

- **Promotion Ladder**：Train → Valid → Freeze → Fresh → Shadow → Canary → Production。从不 Train→Production。
- **Fresh-Test Discipline**：见 §3.3。
- **Downstream Impact Metrics**：阶段高准确率仍可能伤害 Agent（wrong source / stale accepted / conflict hidden）。
- **Global Objective**：Reliability > Compression；LoRA 训决策不训劳动。
- **Directory Convention**：adapter/data/eval 目录按版本命名，禁止歧义。
- **Model Version Library**：每次模型版本记录 base/adapter/dataset/config/date/eval/失败模式。
- **Failure Escalation**：Qwen 失败两次 → 升级；确定性易修失败不升级。
- **Never Merge Again**：合成数据主线不得重启；独立 adapter 范式不得回归。
- **Execution Rule for Agent**：见 §0.3 统一路线执行顺序。

---

# 附录 A：历史范式与失败教训

## A.1 R3A 合成主线（已冻结）

```text
v5：operative=0.510（修复 superseder 泄漏后）
v6：operative=0.547，STALE 15.6% 纹丝不动（语义标注只反转错误方向）
0-shot Base：STALE=35%
→ LoRA 微调对 STALE 细粒度时序判别负迁移（35%→15.6%）
conflict 20 条：operative=1.0（显式取代证据判别良好）
```

结论：**合成时序大数据对细粒度时序判别负迁移**；显式冲突判别可训练但非瓶颈。

## A.2 ec_v1 / T001（过拟合快照）

```text
pool 20 条（real 12 + manual 8）LoRA 40 iters（epochs=2）
训练 loss 2.584→2.367，peak mem 11.6GB
推理 19/20 parse（EC-R-TR-09 因 max_tokens 截断失败）
19/20 通过率 = 过拟合假象（40 iters 对 20 条），不作能力证据
```

处置：`models/ec_v1/` 归档为 T001 过拟合快照，只读，不 resume、不作种子。

## A.3 数据缺口分析（2026-08-15）

```text
capsule 任务：现有 20 + 轨迹挖掘 ~46 + derived ~20 + 人工 ~14 = 100 档可行
state_patch 任务：0 → 任务账本 396 条可抽取 ~79 候选，冷启动 30–50 验证
fresh test：12 条 real 无法分层留出 20 条 → 必须新增真实标注
```

## A.4 禁止事项

```text
禁止重启 R3A 式合成时序主线
禁止 resume 失败 adapter（r3a_* 全部归档）
禁止把 ec_v1 19/20 当作能力证据
禁止 fresh test 用扩增样本充数
```
