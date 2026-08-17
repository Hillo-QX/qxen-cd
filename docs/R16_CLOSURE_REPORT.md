# QXEN-CD R1.6 收尾闭环报告

**日期**: 2026-08-13
**阶段**: R1.6（decide() 判定协议接入批量预测入口 + 双字段审计）
**状态**: CLOSED

---

## 1. 核心目标达成声明

R1.6 核心目标（docs/R16_STARTUP.md §1）: 将 R1.5 正式判定协议 `decide()` 接入批量预测入口，
对候选路径列表输出"判定标签 + 判定依据（reason）"双字段结果，且 R1.5 总评估集 39 条 acc=1.0 不回退。

**达成**: ✅ 批量预测入口 `scripts/r1.6/r16_batch_predict.py` 已实现并通过 39 条全量验证，
39 条 acc=1.0 / FR=0 / FI=0 零回退；每样本输出 `label` + `reason` 双字段（并增强
`elements`/`content_snippet` 审计输入字段）。本阶段未训练、未调用 Ollama、未修改任何冻结资产。

## 2. 验收标准逐条对照（R16_STARTUP.md §3 四条款）

| # | 验收标准（启动文档） | 实测结果 | 判定 |
|---|---|---|---|
| 1 | 入口可用: scripts/r1.6/ 下存在批量预测入口脚本，接受 jsonl 输入，输出含 label 与 reason 双字段 | `scripts/r1.6/r16_batch_predict.py` 存在且可执行；输入 R1.5 评估集 jsonl 3 份（B1/C/stability 共 39 条）；结果 `scripts/r1.6/batch_predict_results.jsonl` 39 行，每行含 `label`/`reason`（另含 p_first/tie/elements/content_snippet/rule_ms 审计字段） | ✅ 通过 |
| 2 | 零回退: 39 条经批量入口重跑 acc=1.0, FR=0, FI=0 | ALL(39): acc=1.0, FR=0, FI=0；B1(12): 1.0/0/0；C(12): 1.0/0/0；stability(15): 1.0/0/0（docs/R16_BATCH_PREDICT_EVAL.md §1） | ✅ 通过 |
| 3 | 双字段审计 100%: 每条 reason 非空可溯源（置信带/规则命中详情） | 39/39 reason 非空（置信带区间或 F1/F2 命中详情），审计覆盖率 100.0% | ✅ 通过 |
| 4 | 性能量级不变: 规则判定部分 ≤0.05 ms/样本 | 规则精判均值 **0.0190 ms/样本**（最大 0.0917 ms），≤0.05 达标；相对 R1.5 实测 0.0102 ms 保持同量级（<0.02ms），占推理（279 ms/样本）0.0068% | ✅ 通过 |

验收标准 4 条全部通过。

## 3. 停止条件确认

| 停止条件（R16_STARTUP.md §4） | 是否触发 |
|---|---|
| 39 条任一 acc < 1.0 或 FR > 0 或 FI > 0 | 未触发（acc=1.0, FR=0, FI=0） |
| 验收标准任一无法客观验证 | 未触发（4 条均以实测数据对照） |
| 需要修改冻结路径下文件 | 未触发（data/r1.1~1.3、models/、calibration/、39491e65、709bd3b9、940e25ca、decide() 本体全程只读） |
| 需要调用 Ollama 或任何模型训练 | 未触发（仅 mlx_lm 本地推理；Ollama 确认无模型加载） |
| 需要读取账本全文 | 未触发 |

全部停止条件未触发。

## 4. 交付产物清单

- **入口脚本**: `scripts/r1.6/r16_batch_predict.py`（只读引用 `scripts/r1.5/r15_rule_refine.py` 的 decide()，含 warmup 排除首次调用开销、分集合性能统计）
- **预测结果**: `scripts/r1.6/batch_predict_results.jsonl`（39 条，label+reason 双字段 + 审计输入字段）
- **验证报告**: `docs/R16_BATCH_PREDICT_EVAL.md`（汇总指标 + 逐样本明细）
- **运行日志**: `logs/r1.6/batch_predict.log`
- **阶段规划**: `docs/R16_STARTUP.md`

## 5. 方法论演进（R1.5 → R1.6）

| 阶段 | 成果 |
|---|---|
| R1.5 | 判定协议 `decide()`（logits 置信带 + F1/F2 规则精判），39 条 acc=1.0，方法论标准 |
| R1.6 | 协议接入工程管道: 批量预测入口 + label/reason 双字段审计，零回退接入、审计覆盖率 100% |

R1.5 的判定协议从"独立评估脚本"升级为"可复用批量预测入口"，同时落地 R15_CLOSURE_REPORT §4
建议第 2 条（接入线上/批量预测入口）与第 4 条（"判定+依据"双字段审计联动）。

## 6. 遗留与后续建议（供 Dispatcher）

1. **当前批量入口为本地文件 jsonl 输入输出**，尚未暴露为 HTTP 服务/CLI 命令封装（如需线上化，后续可加 `--input/--output` 参数与单条 `predict_one()` 接口）。
2. 中期方向（R15_CLOSURE_REPORT §4 建议 3）维持不变: 若评估集规模扩大、域持续增长，规则枚举式维护成本上升时，评估方案C（特征注入式微调）作为模型侧升级。
3. 双字段审计（label+reason）可作为后续阶段的统一审计标准，建议在评估集扩充时同步维护。

## 7. 审计与可追溯性

- 全部 39 条判定逐样本可溯源: 结果 jsonl 含 id/p_first/tie/label/reason/expected/rule_ms，验证报告含完整明细表。
- 冻结资产 sha256 未变（adapter 940e25ca 只读引用）。
- 未训练、未调用 Ollama、未修改 decide() 协议本体。
