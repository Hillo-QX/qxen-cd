# R3B 能力覆盖样本设计（Context Capability Data Design）

状态: DRAFT — 仅设计，未落地数据
日期: 2026-08-14
依据: Kimi-Expert 裁决（2026-08-14）+ 用户指令（v5 价值有限，能力缺失需新样本）
前置: r3a_cot_v5 训练窗口期间做 CPU/数据层设计；落地前需用户授权

---

## 1. 问题定义

v5（及 R3A/B/C 全部现役数据）只训练了"单候选点模板判定"：
- prompt 已内置完整要素/来源/权威链，模型只需套 <think> 模板输出单词语判定（T1 / STALE / false）
- 输出 completion 是单一枚举词，无证据筛选、无冲突解释、无不确定性、无状态更新

**缺失能力**（对齐 QXEN_distiller_training_SKILL.md §3/§10/§16）:
| 能力 | 缺失表现 | SKILL 对应 |
|---|---|---|
| 相关性判断 | 不能从多证据中选 relevant，会被相似文件名误导 | §1/§10 hard negative |
| 证据筛选 | 不能区分 current/backup/broken schema | §10 |
| 冲突解释 | 不能解释 material_conflict 的成因与权威取舍 | §17 failure mining |
| 不确定性 | 证据不足时不能表达不确定性/继续检索 | §3 RETRIEVE |
| 摘要保真 | 不能对长证据做保真压缩 | §3 COMPRESS/VERBATIM |
| 证据充分性 | 不能判断当前证据是否足够下判定 | §1 |
| 是否继续检索 | 不能决定 REFRESH vs RETRIEVE vs 直接判定 | §3 |
| 滚动上下文管理 | 不能从 OLD_STATE + NEW_EVENTS 产生 NEW_STATE | §3/§11 dataset 03 |

## 2. 数据源约束

- 冻结数据 `data/r3/`（3000 条, 8 任务族, sha 75c282dd）只读，不修改
- 现有 r3a/r3b/r3c split 保留不动（对照基线）
- 新样本从冻结数据**重组 + 合成**，provenance 记录来源 query_id
- 落地路径 `data/r3/staging/r3b_cap/`（新目录，不影响现有 split）

## 3. 样本 Schema（JSONL, {prompt, completion}）

### 3.1 类型 A — 证据筛选 (evidence_selection)
对应: SKILL §1/§10

```json
{
  "type": "evidence_selection",
  "prompt": "工作流上下文（原始证据块，未筛选）:\n[E1] current schema: config/phase0.yaml (T0)\n[E2] backup schema: config/phase0.yaml.bak (已归档 2023-08)\n[E3] broken config: config/broken.yaml (解析失败)\n[E4] 训练日志提及 phase0.yaml (T4)\n任务: 判断权威 schema。\n请先列出需要的证据（只保留对判定必要的），再给出判定与依据。",
  "completion": "<think>E1 是当前生效 T0; E2 已归档应排除; E3 解析失败无权威; E4 是间接提及(低于直接权威)</think>{\"selected\": [\"E1\"], \"status\": \"ACTIVE\", \"authority\": \"T0\", \"reason\": \"E1 为当前生效 schema, 直接权威层级最高\"}"
}
```

### 3.2 类型 B — 冲突解释 (conflict_explanation)
对应: SKILL §17, 利用冻结数据 material_conflict=true 样本

```json
{
  "type": "conflict_explanation",
  "prompt": "两份来源对同一配置给出冲突判定:\n- 来源 A: config/lora_train_safe.yaml (当前生效) 判定 ACTIVE\n- 来源 B: 训练日志 (历史提及) 判定 STALE\n当前状态: 判定时点 as_of=2023-09-08, 后续版本 v5 已发布\n任务: 判定 conflict 是否 material, 并解释取舍。",
  "completion": "<think>A 是当前生效源(T0), B 是间接日志(T4); 且 v5 已取代目标, 故 target 实为 STALE, 冲突不material</think>{\"material_conflict\": false, \"resolution\": \"以当前生效源为准但按时间线取 STALE\", \"explanation\": \"版本时间线使 B 的结论正确, 权威层级使 A 优先; 二者结论经时间线校正后一致\"}"
}
```

### 3.3 类型 C — 不确定性/继续检索 (uncertainty_retrieve)
对应: SKILL §3 RETRIEVE/REFRESH

```json
{
  "type": "uncertainty_retrieve",
  "prompt": "任务: 判定 config X 当前是否生效。\n当前证据: [E1] 项目文档提到 config X 但无版本/时间戳; [E2] 3 天前的 README 列表含 X。\n无更多证据。\n任务: 是否足以判定? 若不足, 应检索什么?",
  "completion": "<think>E1/E2 均无时间戳与权威层级, 证据不足; 需检索当前文件系统的 config X 状态</think>{\"sufficient\": false, \"action\": \"RETRIEVE\", \"target\": \"config X 当前文件状态(是否存在/是否被 v2 取代)\"}"
}
```

### 3.4 类型 D — 滚动上下文更新 (state_update)
对应: SKILL §11 dataset 03 (OLD_STATE + NEW_EVENTS → NEW_STATE)

```json
{
  "type": "state_update",
  "prompt": "OLD_STATE: {working_state: {current_task: T42, config: phase0.yaml(ACTIVE)}}\nNEW_EVENTS: [工具结果: config/phase0.yaml 已被 phase0_v2.yaml 取代]\n任务: 输出更新后的状态。",
  "completion": "{\"working_state\": {\"current_task\": \"T42\", \"config\": \"phase0_v2.yaml(ACTIVE)\", \"superseded\": [\"phase0.yaml(STALE)\"]}}"
}
```

### 3.5 类型 E — 摘要保真 (fidelity_compress)
对应: SKILL §3 COMPRESS/VERBATIM, 从冻结数据 text 生成长上下文

```json
{
  "type": "fidelity_compress",
  "prompt": "以下是 40 行配置环境说明(略)... 任务: 压缩为 ≤5 行工作状态, 保留: 关键路径(VERBATIM)、生效版本、归档标记。",
  "completion": "{\"current\": \"config/lora_train_safe.yaml v5 (生效)\", \"archived\": [\"...v1..v4\"], \"critical_paths\": [\"config/lora_train_safe.yaml\"]}"
}
```

## 4. 生成管线（CPU/数据层，不抢 Metal）

1. `scripts/r3b_cap_gen.py`（新脚本）:
   - 读冻结数据 8 任务族, 按 query_id 分组
   - 类型 A/B: 从现有 (text, authority_type, operativeness, material_conflict, reason_code) 重组多证据
   - 类型 C: 从冻结数据缺失时间戳的样本合成"证据不足"场景
   - 类型 D: 从成对反事实样本 (同 query 不同 candidate) 构造状态转移
   - 类型 E: 从长 text 构造压缩任务
2. 每类 300-500 条, 总量 ~1500-2000; train/valid 8:2 按 query 组隔离
3. manifest.json: 分布统计 + sha256 + provenance (source query_id 映射)
4. 校验: 结构 schema 校验 + 比例平衡 + 无 leakage (query 组隔离)

## 5. 与 Gate 的关系

- 类型 A/B 训练出的能力可评估 R3A/R3B/R3C 现有指标 (operative_status/authority/conflict)
- 类型 C/D/E 能力新指标待定 (RETRIEVE 正确率 / state 保真率), 属于 R3B Gate 扩展
- v5 Gate 结果出来后与 v4 (invalid=0, reason_wrong=341) 对比, 再定 R3B_cap 是否吸收新能力样本

## 6. 授权门禁

⚠️ 落地条件（全部满足才执行）:
- [ ] 用户授权新数据目录 `data/r3/staging/r3b_cap/` 创建
- [ ] 不修改冻结数据与现有 split
- [ ] v5 训练完成进入 Gate 评估后

## 7. 决策记录

- 2026-08-14 Kimi-Expert VERDICT=APPROVE (方向B): v5 跑完作对照基线; 3h 窗口做能力样本设计
- 2026-08-14 用户指令: v5 价值有限, 能力不在样本里; 咨询 kimi
