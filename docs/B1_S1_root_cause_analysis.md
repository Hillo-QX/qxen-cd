# R1.3 B1-S1 — hard_irrel 持久 False REL 根因分析（R12-VALL-0005）

**日期**: 2026-08-13
**依据**: `reports/R1.2_FINAL_EVALUATION.md` §5、`docs/R1.3_PLAN.md` §3
**范围**: 纯分析文档，无代码/数据操作

---

## 1. 案例事实（实测数据）

| 项 | 值 |
|---|---|
| 样本 ID | R12-VALL-0005（valid held-out，与训练零重叠） |
| TASK | 扩容 packet_builder：packet_builder 对候选的 rel 判断过保守 |
| 材料元素 | packet_builder（修复目标本体，必须读取）；packet_builder 调用链（调用/依赖入口，决定修复路径） |
| 候选 | `qxen/context_bucket_neighbor.py`（内容为存根 `def context_bucket(*args, **kw)`，无函数体） |
| 场景/来源 | hard_irrel / r11_real_fragment_valid |
| 真实标签 | IRREL |
| 预测 | **REL（误判）** |

**误判跨 checkpoint 一致性**（全部实测）：

| checkpoint | epoch | adapter sha256 | 预测 |
|---|---|---|---|
| T349 首次训练 | 1 | `6db5441b` | REL ❌ |
| 多 epoch 续训 | 2 | `2e456c49` | REL ❌ |
| 多 epoch 续训 | 3（最终） | `0c0ead0d` | REL ❌ |
| Shadow 验证（最终 adapter） | 3 | `0c0ead0d` | REL ❌ |

同一阶段其余样本：False IRREL = 0、invalid = 0（valid 20 条与 Shadow 5 条两个口径），即**唯一系统性误判**。

## 2. 表层相似性分析

候选与目标的关联线索（仅名称/路径层，无内容证据）：

1. 路径含 **`neighbor`**：`context_bucket_neighbor.py` 在命名上暗示与目标模块邻接/相关；
2. 目标材料元素含 **packet_builder 调用链**：调用链类元素在 R1.2 训练中大量对应 indirect_rel 正例（"间接相关"），模型易将"邻接/调用链"泛化为 REL 信号；
3. 候选函数名 **`context_bucket`** 与项目既有真实模块同名，存在"该模块确有功能"的背景先验。

**内容证据面**：候选为**空存根**（`def context_bucket(*args, **kw):` 无函数体），不含任何与 packet_builder 的功能/数据/调用证据；按 Rule–Element–Evidence 判定应否决表层相似 → IRREL。

## 3. 根因假设（按证据强度排序）

1. **训练数据中"同名/邻接存根"类 hard_irrel 覆盖不足（主因）**：R1.2 训练集 hard_irrel 231/420（55%），但其中"候选名称与目标/项目强相似、内容却为空存根"的子类样本占比低（见 B1-S2 量化），模型未学到"内容证据否决表层相似"的判别边界；
2. **调用链元素的 REL 先验过强**：材料元素含"调用链"时，模型对候选名称中 neighbor/调用类线索过度上纲，形成 over-recall 偏向（与 R1.1 3 例 False REL 同向，但无数据泄漏级案例）；
3. **hard_irrel 与 indirect_rel 边界样本缺少对比对**：同一候选在不同 TASK 下的 REL/IRREL 对比样本不足，无法建立"证据决定相关性而非名称"的对比信号。

## 4. 影响评估

- 真实 Agent 流程表现：多读一个无关文件 → 轻微上下文污染，非阻断性；
- 与 R1.1 Shadow 3 例 False REL（含 TR-07 冻结测试集混入训练 = 数据泄漏级）相比严重度显著降低；
- 不构成模型退化：多 epoch 精度恒定 0.95，误判为 hard_irrel 固有挑战（见 R1.2 报告 §5 结论）。

## 5. 结论与对策映射

| 根因 | 对策 | 承接文档 |
|---|---|---|
| 同名/邻接存根类样本不足 | 数据增补（方案 A） | `docs/B1_S2_data_augmentation.md` |
| REL 先验过强 / 阈值问题 | 判定校准（方案 B） | `docs/B1_S3_calibration.md` |
| 判别不可审计、边界无对比 | 证据引用 + 协议增强（方案 C/D） | `docs/B1_S4_evidence_protocol.md` |

**根因结论**: R12-VALL-0005 持久 False REL 的主因是**训练分布中"表层相似+空存根"hard_irrel 子类覆盖不足**，叠加调用链元素的 REL 先验过强；非模型退化，可通过 B1-S2 数据增补 + B1-S3 校准 + B1-S4 可审计协议联合缓解。
