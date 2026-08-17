# R1.3 B1-S3 — 判定校准方案（方案 B）详细设计

**日期**: 2026-08-13
**依据**: `docs/R1.3_PLAN.md` §3（方案 B）、`docs/B1_S1_root_cause_analysis.md`（REL 先验过强）
**范围**: 设计方案文档；**不执行训练/推理/数据操作**

---

## 1. 目标

通过推理侧置信度校准缓解 hard_irrel over-recall（REL 先验过强），使 R12-VALL-0005 类误判可被阈值拦截，同时保持 valid 精度护栏。

## 2. 校准方法

对 REL/IRREL 二分类输出引入**置信度阈值**：

- 提取输出 tokens 的归一化概率：`p(REL)`、`p(IRREL)`（softmax over 标签 token）；
- 判定规则（两档）：
  - **默认档**：`argmax(p) == label`（现行为，等价 θ=0.5）；
  - **保守档**：仅当 `p(REL) ≥ θ` 时判 REL，否则判 IRREL（对 REL 要求更高证据强度）；
- 目标场景：hard_irrel 候选表层相似时 `p(REL)` 处于中低区间（0.5–0.7），θ 抬高后可落为 IRREL。

## 3. 阈值选择策略

| θ 候选 | 依据 |
|---|---|
| 0.5 | 现状基线（不校准） |
| 0.6 | 温和拦截，低副作用 |
| 0.7 | 主要候选（预期拦截 R12-VALL-0005 类中低置信 REL） |
| 0.8 | 强拦截，需验证 False IRREL 不上升 |

- 选择准则（在 valid 20 + Shadow 5 双口径上）：
  1. **护栏**：valid acc ≥ 0.95 且 False IRREL = 0（校准不得引入漏判）；
  2. 目标：False REL 1 → 0；
  3. 若 θ 档位间无差异（模型二分类极自信），记录"校准无效"并转由 B2 数据方案主导（校准作为辅助手段，不替代数据）。

## 4. 评估指标

| 指标 | 门槛 |
|---|---|
| valid accuracy（20 条） | ≥ 0.95（护栏，不得低于 R1.2） |
| valid False REL / False IRREL | False REL ≤ 1 且 False IRREL = 0 |
| Shadow accuracy（5 条） | ≥ 0.90（R1.3 目标 2） |
| Shadow False REL | 0（R1.3 目标 1） |
| 概率分布记录 | 全部 25 样本的 p(REL)/p(IRREL) 留档，可复算 |

## 5. 协议/脚本影响（属推理侧，不改冻结 adapter）

- 现评估脚本 `scripts/r1.2/eval_valid.py` 用 `max_tokens=4` + 正则取标签；校准需扩展为**输出 logits/概率**（mlx_lm generate 取 token scores 或改用 logprobs 模式）；
- 新增 `scripts/r1.3/eval_calibrated.py`（B3 批次实现），复用 R1.2 协议（chat template + thinking off）；
- **冻结资产不动**：R1.2 adapter（sha256 `0c0ead0d`）、R1.1 冻结数据、R1.2 数据文件均不修改。

## 6. 验收标准

- [ ] `eval_calibrated.py` 输出每样本 p(REL)/p(IRREL)（留档 JSON）；
- [ ] θ=0.7 档在 valid 20 条上 acc ≥ 0.95、False IRREL = 0；
- [ ] Shadow 5 条上 False REL = 0 且 accuracy ≥ 0.90；
- [ ] 若达标：记录 θ 选择与影响；若不可达：如实记录"校准不足以消除 False REL"，转 B2 数据增补为主路径。

## 7. 风险与限制

- max_tokens=4 下模型可能只输出单标签 token，概率提取需对齐 tokenizer（REL/IRREL 可能为多 token 词，需词级映射）；
- 校准只改变判定规则，不提升模型知识 —— 对"存根无证据"类样本的拦截效果取决于 p(REL) 分布是否可分；
- 诚实声明义务：若 θ 对 25 样本预测无任何改变（全置信），结论为"置信度代理无区分度"，不可虚报校准收益（对齐 R1.1 Shadow 置信度代理失败的教训）。
