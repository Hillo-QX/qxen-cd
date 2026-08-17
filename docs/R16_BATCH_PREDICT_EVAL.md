# R1.6 批量预测入口验证报告（T370）
**adapter**: /Users/hillo/Desktop/任务调度器/data/r1.4/adapter_round3 (sha256 940e25caa8f0e1ae)
**判定协议**: scripts/r1.5/r15_rule_refine.py 的 decide()（只读引用, 未修改）
**评估集**: R1.5 总评估集 39 条（B1 12 + C 12 + stability 15）
**推理耗时**: 279 ms/样本；**规则精判耗时**: 均值 0.0190 ms/样本, 最大 0.0917 ms/样本（验收要求均值 ≤0.05ms）
**双字段审计覆盖率**: 100.0%（label+reason 均输出, 验收要求 100%）

## 1. 汇总指标

| 集合 | 条数 | acc | FR | FI | 规则均值ms |
|---|---|---|---|---|---|
| B1 | 12 | 1.0 | 0 | 0 | 0.0088 |
| C | 12 | 1.0 | 0 | 0 | 0.0185 |
| stability | 15 | 1.0 | 0 | 0 | 0.0298 |
| ALL | 39 | 1.0 | 0 | 0 | 0.0190 |

## 2. 逐样本明细（label + reason 双字段）

| id | 集合 | 期望 | label | p_first | tie | reason |
|---|---|---|---|---|---|---|
| R14B1-E001 | B1 | IRREL | IRREL | 0.0141 | False | p_first=0.014 < 0.15 低置信带 |
| R14B1-E002 | B1 | IRREL | IRREL | 0.2692 | False | 中置信带规则未命中 (F1=中性, F2=False) |
| R14B1-E003 | B1 | IRREL | IRREL | 0.0159 | False | p_first=0.016 < 0.15 低置信带 |
| R14B1-E004 | B1 | IRREL | IRREL | 0.011 | False | p_first=0.011 < 0.15 低置信带 |
| R14B1-E005 | B1 | IRREL | IRREL | 0.0124 | False | p_first=0.012 < 0.15 低置信带 |
| R14B1-E006 | B1 | IRREL | IRREL | 0.023 | False | p_first=0.023 < 0.15 低置信带 |
| R14B1-E007 | B1 | IRREL | IRREL | 0.2446 | False | 中置信带规则未命中 (F1=中性, F2=False) |
| R14B1-E008 | B1 | REL | REL | 0.5922 | False | p_first=0.592 ≥ 0.45 高置信带 |
| R14B1-E009 | B1 | REL | REL | 0.7059 | False | p_first=0.706 ≥ 0.45 高置信带 |
| R14B1-E010 | B1 | REL | REL | 0.651 | False | p_first=0.651 ≥ 0.45 高置信带 |
| R14B1-E011 | B1 | REL | REL | 0.5922 | False | p_first=0.592 ≥ 0.45 高置信带 |
| R14B1-E012 | B1 | IRREL | IRREL | 0.0052 | False | p_first=0.005 < 0.15 低置信带 |
| TR-01 | C | REL | REL | 0.8518 | False | p_first=0.852 ≥ 0.45 高置信带 |
| TR-02 | C | IRREL | IRREL | 0.0759 | False | p_first=0.076 < 0.15 低置信带 |
| TR-03 | C | REL | REL | 0.777 | False | p_first=0.777 ≥ 0.45 高置信带 |
| TR-04 | C | REL | REL | 0.2448 | False | 中置信带规则命中 (F1=中性, F2=True 内容证据) |
| TR-05 | C | IRREL | IRREL | 0.018 | False | p_first=0.018 < 0.15 低置信带 |
| TR-06 | C | IRREL | IRREL | 0.0159 | False | p_first=0.016 < 0.15 低置信带 |
| TR-07 | C | IRREL | IRREL | 0.1647 | False | 中置信带规则未命中 (F1=中性, F2=False) |
| TR-08 | C | IRREL | IRREL | 0.1479 | False | p_first=0.148 < 0.15 低置信带 |
| TR-09 | C | IRREL | IRREL | 0.245 | False | 中置信带规则未命中 (F1=中性, F2=False) |
| TR-10 | C | REL | REL | 0.6233 | False | p_first=0.623 ≥ 0.45 高置信带 |
| TR-11 | C | IRREL | IRREL | 0.0067 | False | p_first=0.007 < 0.15 低置信带 |
| TR-12 | C | REL | REL | 0.1818 | False | 中置信带规则命中 (F1=True 路径一致) |
| ST-01 | stability | REL | REL | 0.4076 | False | 中置信带规则命中 (F1=True 路径一致) |
| ST-02 | stability | IRREL | IRREL | 0.223 | False | 中置信带规则反证 (F1=False 陈旧/备份/路径不匹配, 覆盖F2=True) |
| ST-03 | stability | REL | REL | 0.4375 | False | 中置信带规则命中 (F1=中性, F2=True 内容证据) |
| ST-04 | stability | IRREL | IRREL | 0.0374 | False | p_first=0.037 < 0.15 低置信带 |
| ST-05 | stability | REL | REL | 0.437 | False | 中置信带规则命中 (F1=True 路径一致) |
| ST-06 | stability | IRREL | IRREL | 0.0374 | False | p_first=0.037 < 0.15 低置信带 |
| ST-07 | stability | REL | REL | 0.349 | False | 中置信带规则命中 (F1=中性, F2=True 内容证据) |
| ST-08 | stability | IRREL | IRREL | 0.0602 | False | p_first=0.060 < 0.15 低置信带 |
| ST-09 | stability | REL | REL | 0.7554 | False | p_first=0.755 ≥ 0.45 高置信带 |
| ST-10 | stability | IRREL | IRREL | 0.0759 | False | p_first=0.076 < 0.15 低置信带 |
| ST-11 | stability | IRREL | IRREL | 0.1333 | False | p_first=0.133 < 0.15 低置信带 |
| ST-12 | stability | REL | REL | 0.651 | False | p_first=0.651 ≥ 0.45 高置信带 |
| ST-13 | stability | IRREL | IRREL | 0.2446 | False | 中置信带规则反证 (F1=False 陈旧/备份/路径不匹配, 覆盖F2=True) |
| ST-14 | stability | REL | REL | 0.2691 | False | 中置信带规则命中 (F1=True 路径一致) |
| ST-15 | stability | IRREL | IRREL | 0.4078 | False | 中置信带规则未命中 (F1=中性, F2=False) |
