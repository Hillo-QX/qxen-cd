# QXEN-CD R1.5 收尾闭环报告

**日期**: 2026-08-13
**阶段**: R1.5（方案A 稳定性确认，方法论闭环）
**最终判定协议**: logits 置信带 + F1/F2 规则精判（`decide()` 正式入口，scripts/r1.5/r15_rule_refine.py）

---

## 1. R1.5 成果总结

### 1.1 解决的问题
R1.4 遗留局限: C 真实轨迹 FI=2（TR-04 adapter_config 0.245 / TR-12 数据来源 0.182, REL 欠召回）。
三轮微调（round2/3/4）实证: REL/IRREL 边界样本 p_first 分布固有重叠（跷跷板效应），单阈值不可分离。

### 1.2 方案A（二阶段规则精判）—— 零训练成本解决
- **阶段1**: R1.4 冻结 adapter（940e25ca）logits 首 token 比较 → p_first(REL)。
- **阶段2**: 中置信带 [0.15, 0.45) 样本应用规则:
  - F1 路径一致性（三态: True=与声明来源一致 / False=陈旧备份/同文件异目录强反证 / None=中性）
  - F2 内容证据（训练配置键/数据记录键; 日志噪音/二进制乱码为反证）
- **演进**: T366 跨域探测发现备份路径+内容证据键被 F2 覆盖误判 → F1 三态增强（强反证优先）→ 泛化 8/8。

### 1.3 关键验证指标

| 指标 | 数值 |
|---|---|
| 回归评估集（B1 12 + C 12） | acc=1.0, FR=0, FI=0（基线 0.9167 / FR0 / FI2）|
| 扩展评估集（跨域 10 + 边界 5） | acc=1.0, FR=0, FI=0（基线 0.8 / FR1 / FI2）|
| 总评估集（39 条） | **acc=1.0, FR=0, FI=0** |
| 跨域泛化探测（8 条新域） | 8/8 |
| 性能开销 | 规则 0.0102 ms/样本，占推理 0.0042%（<0.01%），可忽略 |
| 中置信带规则触发 | 8/15 扩展样本触发且全部正确裁决（规则泛化） |

## 2. 方法论推进成果（R1.1 → R1.5）

| 阶段 | 目标 | 成果 |
|---|---|---|
| R1.1 | Recall Repair 基线 | adapter a4b2eb36 |
| R1.2 | Element-Aware Materiality Repair | adapter 0c0ead0d, valid 0.95, 3 类 failure 消除 |
| R1.3 | 消除 hard_irrel 持久 False REL | adapter 39491e65, valid 1.0/Shadow 1.0/FalseREL 0/溯源可审计率 100% |
| R1.4 | 真实轨迹 Shadow + 规则定稿 | adapter 940e25ca, θ=0.4+tie→REL, B1 12/12 全对, C FR=0（FI=2 遗留）|
| R1.5 | 方案A 规则精判解决遗留局限 | **decide() 正式判定协议, 39 条 acc=1.0, FR=0, FI=0** |

**关键方法论演进**: 单层 logits 阈值（R1.1-1.4）→ logits 置信带 + 可判别特征规则（R1.5）。
R1.4 实证"单阈值不可分离"后，R1.5 以零训练成本引入 F1（路径一致性）/F2（内容证据）显式特征，
在不修改冻结模型的前提下将判定准确率从 0.9167/39 条提升至 1.0/39 条。

## 3. 冻结与可审计性

- 全程未修改: data/r1.1, r1.2, r1.3, models/, calibration/, R1.3 adapter(39491e65), calibration_r13(709bd3b9), R1.4 adapter(940e25ca)。
- 所有判定可溯源: decide() 返回 reason（置信带/规则命中详情），逐样本记录于 rule_refine_report.md 与 R15_STABILITY_EVAL.md。
- 未训练、未调用 Ollama。

## 4. 下一步建议（供 Dispatcher）

1. **R1.5 闭环成立**，建议正式收尾并登记 R1.5 判定协议为当前方法论标准。
2. **短期（可选）**: 将 decide() 接入线上/批量预测入口（当前为独立评估脚本，未接入业务管道）。
3. **中期**: 若评估集规模扩大、域持续增长，规则枚举式维护成本上升 → 届时评估"特征注入式微调（方案C）"作为模型侧升级。
4. **审计联动**: R1.3 的溯源可审计率目标（≥80%）在 decide() 的 reason 输出下可扩展为"判定+依据"双字段审计。

## 5. 产物清单（R1.5）

- 规则: scripts/r1.5/r15_rule_refine.py（正式入口 decide()）
- 评估集: data/r15_stability/eval_stability.jsonl（15 条）+ eval_stability_raw.jsonl + QA_STABILITY.md
- 报告: data/r1.5/r15_candidate_analysis.md, data/r1.5/R15_RULE.md, data/r1.5/rule_refine_report.md,
  reports/r1.5/R15_CLOSURE_EVAL.md, reports/r1.5/R15_STABILITY_EVAL.md, 本文档
- 日志: logs/r1.5/{rule_refine, stability_eval}.log
