# QXEN-CD Evidence Capsule 契约 v1

> 状态：DRAFT（T001 产物，待 smoke 测试验证）
> 定位：QXEN-CD 从"分类模型"转为"证据理解/筛选/压缩/状态汇报 sub-agent"后的统一输出协议。
> 设计原则（源自 Kimi-Expert APPROVE 条件）：
>   1. 外层固定 JSON 结构，方便 GPT 主 Agent 程序化读取；
>   2. 内层自然语言自由表达，不限制 19 类枚举（reason_code 等仅作参考字段，不再是强制生产职责）；
>   3. 每模块（R1-R7）可独立训练/评估，组合由 GPT 主 Agent 完成；
>   4. 多维 Gate 指标全部可在冻结 eval 集上确定性脚本测量。

---

## 一、顶层结构

```json
{
  "capsule_id": "string, 必填",
  "source_type": "string, 必填",
  "relevance": "low|medium|high, 必填",
  "key_evidence": ["EvidenceItem, 必填, ≥0"],
  "timeline": ["string, 可填"],
  "relations": ["string, 可填"],
  "conflicts": ["ConflictItem, 可填"],
  "uncertainty": ["string, 可填"],
  "immutable_fields": ["string, 可填"],
  "compressible": ["string, 可填"],
  "sufficiency": "insufficient|sufficient, 必填",
  "next_step": "string, 可填",
  "evidence_links": ["EvidenceLink, 可填, 证据→判断点定向支撑"],
  "reference": ["string, 可填, 引用溯源"],
  "metadata": {
    "model": "string",
    "contract_version": "string",
    "created_at": "ISO8601 string",
    "as_of": "ISO8601 string|null"
  }
}
```

### 必填字段（缺任一 → 契约不合法）
`capsule_id` / `source_type` / `relevance` / `key_evidence` / `sufficiency`

### 类型与取值范围

| 字段 | 类型 | 取值范围/规则 |
|---|---|---|
| capsule_id | string | 非空，唯一标识 |
| source_type | string | 枚举：`data_file` / `config` / `report` / `code` / `model_weights` / `log` / `doc` / `env_check` / `other` |
| relevance | string | `low` / `medium` / `high` |
| key_evidence | EvidenceItem[] | ≥0 项；每项必须含 `text` + `source` |
| timeline | string[] | 每行形如 `"事件：日期"`，日期为 ISO8601 或可读日期 |
| relations | string[] | 自由文本，描述时间/版本/权威关系 |
| conflicts | ConflictItem[] | 可选；`{a, b, note}` 三字段 |
| uncertainty | string[] | 自由文本，列出不确定点 |
| immutable_fields | string[] | 必须原文保留的字段名（如 `日期`、`版本号`、`来源路径`、`哈希`） |
| compressible | string[] | 可压缩的内容类型（如 `背景说明`、`重复描述`） |
| sufficiency | string | `insufficient` / `sufficient` |
| next_step | string | 提议下一步行动（检索/行动），由 GPT 最终决定 |
| evidence_links | EvidenceLink[] | 可选；`{target, evidence_refs, support, note}`，见下 |
| reference | string[] | 来源路径引用，用于可追溯率校验 |
| task_type | string | 可选；`capsule` / `state_patch`（训练分流标签，skill §4.1） |
| anchor_id | string | 可选；锚点标识（生成器溯源用） |
| data_source | string | 可选；来源分类：`existing_manual` / `external_real` / `trajectory_real` / `r1x_real` / `derived` / `manual` |
| event_date | string | 可选；事件日期 |
| provenance | string | 可选；溯源标记 |
| operative_status | string | 可选；`CURRENT` / `SUPERSEDED` / `STALE`（Gate 维度） |
| authority | string | 可选；权威等级（如 `T1` / `T2`，Gate 维度） |
| evidence_observed_at | string | 可选；证据观测时间 |
| materiality_label_original | string | 可选；原始重要性标注 |
| source_sha256 | string | 可选；来源哈希（防篡改校验） |
| metadata | object | 见下 |

### EvidenceItem 结构
```json
{
  "text": "string, 必填, 证据原文摘录",
  "source": "string, 必填, 来源路径(可溯源)",
  "preserve_verbatim": "bool, 默认false, true=不可改写必须原文保留"
}
```

### ConflictItem 结构
```json
{
  "a": "string, 冲突方A描述",
  "b": "string, 冲突方B描述",
  "note": "string, 冲突说明(可选)"
}
```

### EvidenceLink 结构（可选字段，证据→判断点定向支撑）
```json
{
  "target": "string, 必填, 当前任务关键判断点描述",
  "evidence_refs": ["string, 必填, 必须引用 key_evidence 中的 source 值"],
  "support": "supports|contradicts|partial|insufficient",
  "note": "string, 可选, 该证据支持或限制什么"
}
```

**边界规则**：
- `evidence_refs` 必须引用 `key_evidence[]` 中某项的 `source` 值；
- 禁止与 `conflicts[]` 重复表达同一互斥对；
- "与目标无关"的证据不建 link（由 `relevance` 字段承载）；
- 本字段为**存储契约层**可选字段：不纳入 §4.1 训练 completion，不进双层 Gate 计分（仅结构校验）。

### 功能字段（可选，非普通元数据）
以下字段被训练分流或 Gate 直接消费，单列语义、枚举与消费方（不再只在 schema description 隐性定义）：

| 字段 | 语义 | 枚举/格式 | 消费方 |
|---|---|---|---|
| `task_type` | 训练任务分流标签 | `capsule` / `state_patch` | skill §4.1 联合训练 interleave 分层 |
| `operative_status` | 证据/事实当前有效状态 | `CURRENT` / `SUPERSEDED` / `STALE` / `unknown` | 双层 Gate：CURRENT/STALE/SUPERSEDED 准确率 |
| `authority` | 来源权威等级 | `T1`（一级权威）/ `T2` / `T3`（推导/低权威） | 双层 Gate：authority 准确率 |
| `data_source` | 数据来源分类 | `existing_manual` / `external_real` / `trajectory_real` / `r1x_real` / `derived` / `manual` | 溯源审计（provenance 可追溯） |
| `provenance` | 溯源标记 | 同 `data_source` 分类 | 溯源审计 |

> 注：`event_date` / `anchor_id` / `evidence_observed_at` / `materiality_label_original` / `source_sha256` 为溯源/元数据辅助字段（见 §一 类型表），不参与 Gate 判定。

---

## 二、多维 Gate 字段定义

多维指标全部在冻结 eval 集上确定性脚本测量（哈希比对/路径校验）。

| Gate 维度 | 测量方式 | 建议门槛 |
|---|---|---|
| 关键事实篡改率 | 对 immutable_fields 标记项做原文哈希比对 | **= 0** |
| 不可改写字段丢失率 | preserve_verbatim=true 项是否全部出现 | **= 0** |
| 冲突隐藏率 | gold conflicts 集合 ∩ 输出 conflicts 集合召回 | **≤ 基线后定阈值**（expert UNCERTAIN 项） |
| 证据引用可追溯率 | reference/source 路径校验存在性 | **≥ 0.95** |
| 证据不足过早行动率 | gold insufficient 样本中 next_step 为行动而非检索 | **≤ 0.05** |
| 材料召回率 | relevance=high 的 gold 样本是否被选入 key_evidence | 参考 |
| 关键证据召回率 | gold key_evidence 命中率 | 参考 |
| 保真率 | 日期/版本/哈希/路径字段逐项比对 | 参考 |
| 重复上下文压缩率 | compressible 标记覆盖度 | 参考 |
| CURRENT/STALE/SUPERSEDED 准确率 | 仅作参考指标，不再作上线标准 | 参考 |

### 聚合规则
- 硬门槛（violation）：篡改>0 或 丢失>0 → 该样本 FAIL
- 软指标：其余维度按样本级聚合（正确数/样本数），报告全量分布
- 最终上线判定：**硬门槛全过 + 软指标达标率**，由 GPT 主 Agent 综合决定

---

## 三、R1-R7 模块契约映射

每个模块独立训练/评估，输出统一胶囊，组合由 GPT 完成：

| 阶段 | 模块输入 | 胶囊输出字段（模块负责填充） |
|---|---|---|
| R1 | 任务+候选材料 | relevance, capsule_id, source_type |
| R2 | R1 输出+材料 | key_evidence, reference |
| R3A | R2 输出 | timeline, relations, sufficiency |
| R3B | R3A 输出 | source_type, reference（权威线索交 GPT 复核） |
| R3C | R2 输出 | conflicts |
| R4 | R2 输出 | immutable_fields, compressible, key_evidence[].preserve_verbatim |
| R5 | R2-R4 输出 | timeline, relations（滚动上下文/归档摘要） |
| R6 | 全模块输出 | uncertainty, sufficiency |
| R7 | R6 输出 | next_step（由 GPT 最终决定） |

---

## 四、JSON Schema（机器可校验）

本契约文档对应的 JSON Schema 见 `configs/evidence_capsule_v1_schema.json`（T001 伴随产物，smoke 测试直接校验）。
