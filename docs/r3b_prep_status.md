# R3B 前置准备状态 (Prep Status)

更新: 2026-08-14 (夜晚自主模式)
前置文档: docs/r3b_context_capability_data_design.md (DRAFT)

---

## 1. r3a_cot_v5 训练监控

- 进程: PID 32541, mlx_lm lora, iters=1917, lr=4e-6, rank=8, max_seq=448
- 启动: 2026-08-15 02:39
- 监控方式: 后台 bash 轮询 (/tmp/r3a_cot_v5_monitor.txt, 300s 间隔)

| Iter | Train loss | Val loss | 备注 |
|---|---|---|---|
| 1 | — | 3.002 | 初始 |
| 100 | 2.695 | 2.322 | |
| 200 | 1.975 | 1.660 | |
| 300 | 1.416 | 1.177 | |
| 400 | 1.068 | 0.941 | 无 NaN |

健康度: 收敛正常, 无 NaN/发散, 进程存活, CPU 正常波动 (4.7%-68%)
预计完成: ~1917 iters, 剩余 ~1500 iters, 约 2.5h (05:30 前后)
完成动作: 按 Gate 协议评估, 与 v4 基线 (invalid=0, reason_wrong=341) 对比归档

## 2. R3B 能力样本设计 (DRAFT, 未落地)

5 类样本 (docs/r3b_context_capability_data_design.md §3):
- A evidence_selection: 多证据筛选 + hard-negative 区分
- B conflict_explanation: 冲突解释 + 权威取舍
- C uncertainty_retrieve: 证据不足 → RETRIEVE 决策
- D state_update: OLD_STATE + NEW_EVENTS → NEW_STATE (滚动上下文)
- E fidelity_compress: 长证据保真压缩 (COMPRESS vs VERBATIM)

生成管线 (design §4):
1. 读冻结数据 8 任务族, 按 query_id 分组
2. 5 类 generator 重组/合成样本
3. train/valid 8:2 按组隔离 + manifest + sha256
4. 校验: schema + 分布 + leakage 检查

## 3. 脚本骨架 (SKELETON ONLY, 已落盘)

| 脚本 | 状态 | 说明 |
|---|---|---|
| scripts/r3b_data_generator.py | 骨架完成, 语法 OK, dry-run OK | 5 个 generator 函数签名 + dataclass R3BSample + 编排占位 |
| scripts/r3b_data_validate.py | 骨架完成, 语法 OK, dry-run OK | SCHEMA_RULES 5 类 + validate_sample + validate_file 占位 |

- 均仅含骨架, 不落地数据, 未读取 data/ 或 models/
- 实现前置: 用户授权 + v5 Gate 评估完成

## 4. 待办 / 门禁

- [ ] v5 训练完成 (05:30 前后) → Gate 评估 → 与 v4 对比
- [ ] 用户授权 `data/r3/staging/r3b_cap/` 创建
- [ ] 实现 generator/validate 实际逻辑 → 生成数据 → 校验
- [ ] R3B_cap 训练 (新指标: RETRIEVE 正确率 / state 保真率)

## 5. 决策记录

- 2026-08-14 Kimi-Expert VERDICT=APPROVE (方向B): v5 跑完作对照基线; 窗口内设计能力样本
- 2026-08-14 用户指令: v5 价值有限 (仅回答模板 CoT+JSON 共存问题), 能力缺失需新样本; 咨询 kimi
