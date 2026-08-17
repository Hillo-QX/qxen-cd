# QXEN 训练项目进度 (PROGRESS.md)

> 维护：Executor 按 Dispatcher 任务更新。最新在前。

## 2026-08-13 — T053: HQ1700 分布偏移分析与 Phase B 决策（分析任务，PASS）

- **分布对比**（held-out gold vs HQ1700 vs T052 mixed）：
  - KEEP 占比：held-out **62.2%** / HQ1700 14.3% / mixed 14.9% → **最大偏移维度**
  - PIN/VERBATIM/DROP：held-out 各 10-17%，训练集各 13-16%，基本对齐
  - COMPRESS/REFRESH/RETRIEVE：held-out 为 0（评估盲区），训练集各 13-16%
- **T051 失败归因确认**：HQ1700 7 类均衡使 KEEP 表征不足，held-out 上 KEEP 仅预测 22/120、DROP 过度预测 110（57%）、PIN 32 条中 14 条误判 DROP → constraint_recall 0.5625 劣化。**分布偏移是直接原因**。
- **T052 混合缓解确认**：KEEP 预测 22→70、DROP 110→60、PIN 误判 DROP 14→0、constraint_recall→1.0。T049 balanced（P1 场景 KEEP 主导）补足 KEEP 表征，**混合训练已显著缓解偏移**。
- **Phase B 策略建议（明确）**：
  1. **继续混合路径**，不以 HQ1700 全量单独训练（已被 T051 证伪）；
  2. HQ1700 以增量方式（如与 P1 场景数据按比例混合、KEEP 主导样本加权）纳入 Phase B，而非全量均衡；
  3. 优先扩充评估集覆盖 COMPRESS/REFRESH/RETRIEVE（当前 held-out 有盲区，无法验证 HQ1700 这三类收益）；
  4. 若评估集扩充后 KEEP 召回仍低，考虑增加 KEEP 主导样本权重。
- 依据：QXEN SKILL quality tiers——HQ1700 为 SILVER（teacher-generated partially verified），仅 GOLD 应主导最终 SFT，故 Phase B 需以 GOLD/P1 场景数据为主、HQ1700 为增量补充。

## 2026-08-13 — T052: 混合训练重训并门控（PASS，Phase A 正式产物，Dispatcher 决策选项 C）

- **背景**：T051 HQ1700 单独训练 constraint_recall 0.5625 < base 0.6562 门控 FAIL，根因分布偏移（HQ1700 新场景 vs held-out P1 场景）。
- **数据**：`data/mixed_train/train.jsonl` = HQ1700 抽取 400 条（7 标签均衡每类 57）+ T049 balanced 78 条 = **477 条**，DROP 15.9%（修复过度预测 DROP）。`scripts/build_mixed_train.py`。
- **训练**：`outputs/lora_adapters_ctxA_mixed/`，完整 500 iters，loss 4.51→1.43（min 1.08@440），Peak mem 9.862GB 零内存触发，产物 7 文件 62 keys 无 NaN 验证 PASS。
- **held-out 193 条评估**（`outputs/eval_ctxA/comparison_summary_mixed.json`）：
  - base:    acc=0.1865, critical_recall=0.5849, constraint_recall=0.6562, stale_rejection=1.0, n_invalid=13
  - mixed:   acc=**0.3212**(+72%), critical_recall=**1.0**, constraint_recall=**1.0**, stale_rejection=**1.0**持平, n_invalid=**0**
  - 4 指标全达标：constraint_recall 0.6562→1.0 修复 T051 劣化，T051 根因（PIN 32 条误判 DROP）消除。
- **门控判定 PASS**：登记为 **Phase A 正式产物**（替代 T049 balanced 基线），回退条件未触发。
- 详见 `logs/T052_summary.md`。

## 2026-08-13 — T049: 修正 ctxA 数据 DROP 过度占比并重训（PASS，Dispatcher 决策）

- **根因修复**：T047 训练数据 DROP 占 45.4%（49/108）导致模型过度预测 DROP（70%）。新增 `scripts/balance_ctxA_training.py`（seed=42 欠采样 DROP，其余 6 类全保留），平衡集 78 条 DROP 45.4%→**24.4%**，输出 `data/distill_ctxA/train_balanced.jsonl` + manifest。
- **重训**：`outputs/lora_adapters_ctxA_balanced/`（新目录，不覆盖 T047）。训练在 **iter325/550 被 memory_monitor 按设计 SIGTERM**（free<500MB 连续 3 次，wired 峰值 14.6GB，防内核 panic 保护），iter300 checkpoint 已保存；loss 4.33→0.511，Peak mem 7.16GB，无重启。
- **held-out 193 条评估**（`outputs/eval_ctxA/comparison_summary_t049.json`）：
  - base:    acc=0.1865, critical_recall=0.5849, constraint_recall=0.6562, stale_rejection=1.0, n_invalid=13
  - ctxA平衡: acc=**0.2953**, critical_recall=**1.0**, constraint_recall=**1.0**, stale_rejection=**1.0**, n_invalid=**0**
  - 预测 DROP 70%→30%，过度丢弃被纠正。
- **Dispatcher 决策**：PASS（临时）。stale_rejection 1.0 持平满分属**天花板效应非退化**，判定规则更新：base 满分时持平视为通过。门控脚本 `scripts/eval_decision.py` 已更新该规则。
- **进入 Phase B**，以 iter300 checkpoint 为基线。**登记条件**：用户 1750 条高质量数据集到位后，用该数据重训并重新门控判定。

## 2026-08-12 — T031: 第十次 LoRA 训练（500 iters）收敛推进（PASS，Dispatcher 决策）

- **配置 v11**：iters 450→**500**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 500 iters，**整机未重启**（boot 19:06:35），Peak mem 13.627GB（≤24GB），正常退出 train_rc=0；memory_monitor 全程（22:09~22:46）**零 WARN**。
- **loss**：**min=0.506@iter490**（<T030 的 0.518@445），iter450-500 段（455=0.935, 465=0.691, 490=0.506）持续下降，证明仍在收敛；val 曲线 3.244→1.661(200)→1.573(400)→**1.586(500)**；同采点 iter400 与 T029/T030 完全一致（1.573，确定性 seed=0）；T030 final(450)=1.567，本次 final(500)=1.586（+1.2%）。
- **产物验证 PASS**：新增 5 个 adapter（0000460~0000500），全 50 个 adapter key 集一致（62 keys）、大小 5,417,491B、无 NaN/Inf、可加载、全 rank=4；adapters.safetensors==0000500。
- **验收判定**：val 1.586 略高于 T030 的 1.567，未满足原验收标准"≤1.5 或较 1.567 下降"。request_decision 后 **Dispatcher 判定 PASS**：val +1.2% 属 12 batch/batch_size=1 验证噪声，min train 0.506<0.518 证明仍收敛，stop_condition 未触发。**决策指令**：继续训练至 iter550，验收标准放宽为 **iter550 val≤1.586 则继续，>1.586 则判定收敛完成停止延长**并转入最终评估。

## 2026-08-12 — T030: 第九次 LoRA 训练（450 iters）收敛推进（PASS）

- **配置 v10**：iters 400→**450**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 450 iters，**整机未重启**（boot 19:06:35），Peak mem 13.644GB（≤24GB），正常退出 train_rc=0；memory_monitor 全程（21:37~22:07）**零 WARN**。
- **loss**：**min=0.518@iter445**（<T029 的 0.621）；val 3.244→**1.567**（<1.573，持续下降，逼近目标 ≤1.5）；iter450 终值 0.793。iter400 后 loss 继续下降（405=0.599, 445=0.518, 450=0.793）。
- **产物验证 PASS**：新增 5 个 adapter（0000410~0000450），全 45 个 adapter key 集一致（62 keys）、大小 5,417,491B、无 NaN/Inf、safetensors 可加载、全 rank=4（min(shape) 判定）；adapters.safetensors 与 0000450 逐键相等。注：首次验证 rank4=False 为脚本 bug（lora_a 误取 shape[0]），改用 min(shape) 后全 PASS，产物本身无误。
- **全部验收标准客观满足**：iters=450、无重启、Peak mem<24GB、5 新 adapter 验证通过、min 0.518<0.621 且 val 1.567 无劣化。无需 escalate。

## 2026-08-12 — T029: 第八次 LoRA 训练（400 iters）收敛推进（PASS）

- **配置 v9**：iters 350→**400**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 400 iters，**整机未重启**（boot 19:06:34），Peak mem 13.648GB（≤24GB），正常退出 train_rc=0。
- **loss**：**min=0.621@iter395**（<T028 的 0.709）；val 3.244→**1.573**（<1.575，无劣化）；iter400 终值 0.870。loss 在 iter350 后继续下降（360=0.668, 375=0.822, 395=0.621）。
- **产物验证 PASS**：新增 5 个 adapter（0000360~0000400），全 40 个 adapter key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载；adapters.safetensors 已更新。
- **全部验收标准客观满足**：iters=400、无重启、Peak mem<24GB、5 新 adapter 验证通过、min 0.621<0.709 且 val 1.573 无劣化。无需 escalate。

## 2026-08-12 — T028: 第七次 LoRA 训练（350 iters）收敛推进（PASS）

- **配置 v8**：iters 300→**350**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 350 iters，**整机未重启**（boot 19:06:34），Peak mem 13.648GB（≤24GB），正常退出 train_rc=0。
- **loss**：**min=0.709@iter310**（<T027 的 0.725）；val 3.244→**1.575**（<1.590，无劣化）；iter350 终值 0.887。loss 在 iter300 后继续下降（310=0.709, 320=0.770, 340=0.763）。
- **产物验证 PASS**：新增 5 个 adapter（0000310~0000350），全 35 个 adapter key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载；adapters.safetensors 已更新。
- **全部验收标准客观满足**：iters=350、无重启、Peak mem<24GB、5 新 adapter 验证通过、min 0.709<0.725 且 val 1.575 无劣化。无需 escalate。

## 2026-08-12 — T027: 第六次 LoRA 训练（300 iters）收敛推进（PASS）

- **配置 v7**：iters 250→**300**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 300 iters，**整机未重启**（boot 19:06:34），Peak mem 13.631GB（≤24GB），正常退出 train_rc=0。
- **loss**：**min=0.725@iter290**（<T026 的 0.794）；val 3.244→**1.590**（<1.612）；iter300 终值 0.774。loss 在 iter250 后继续下降（275=0.802, 285=0.837, 290=0.725）。
- **产物验证 PASS**：新增 5 个 adapter（0000260~0000300），全 30 个 adapter key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载。
- **全部验收标准客观满足**：iters=300、无重启、Peak mem<24GB、5 新 adapter 验证通过、min 0.725<0.794 且 val 1.590<1.612。无需 escalate。

## 2026-08-12 — T026: 第五次 LoRA 训练（250 iters）收敛推进（PASS）

- **配置 v6**：iters 200→**250**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 250 iters，**整机未重启**（boot 19:06:34），Peak mem 13.640GB（≤24GB），正常退出 train_rc=0。
- **loss**：**min=0.794@iter220**（<T025 的 0.981）；val 3.244→**1.612**（<1.661）；iter250 终值 1.305 为单 batch 噪声。loss 在 iter200 后继续下降（210=0.831, 220=0.794, 235=0.895, 245=0.908）。
- **产物验证 PASS**：新增 5 个 adapter（0000210~0000250），全 25 个 adapter key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载；adapters.safetensors 已更新（20:14）。
- **全部验收标准客观满足**：iters=250、无重启、Peak mem<24GB、5 新 adapter 验证通过、adapters.safetensors 可加载、min 0.794<0.981 且 val 1.612<1.661。无需 escalate。

## 2026-08-12 — T025: 第四次 LoRA 训练（200 iters）收敛推进（PASS）

- **配置 v5**：iters 150→**200**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。备份 `configs/lora_train_safe.yaml.bak_v4`。
- **训练**：完整 200 iters，**整机未重启**（boot 19:06:34），Peak mem 13.652GB（<20GB），正常退出 train_rc=0。
- **loss**：**min=0.981@iter195**（<T024 的 1.027）；val 3.244→**1.661**（<1.706）；iter200 终值 1.066 为单 batch 噪声回升（与 T023/T024 同模式）。loss 在 iter150 后继续下降（165=1.021, 185=1.008, 190=1.009, 195=0.981）。
- **产物验证 PASS**：新增 5 个 adapter（0000160~0000200），全 20 个 adapter key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载。
- **Dispatcher 判定 PASS**：min 0.981<1.027 与 val 1.661<1.706 达标，iter200 终值回升为单 batch 噪声非训练异常。

## 2026-08-12 — T024: 第三次 LoRA 训练（150 iters）收敛推进（PASS）

- **清理**：删除 T021 步长5残留（0000005/0000015/0000025/0000035/0000045），仅保留步长10文件。
- **配置 v4**：iters 100→**150**，其余不变（seq=512 / layers=4 / rank=4 / save_every=10 / steps_per_report=5）。
- **训练**：完整 150 iters，**整机未重启**（boot 19:06:34），Peak mem 13.566GB，正常退出 train_rc=0。
- **loss**：前50 iters 与 T023 完全一致（确定性 seed=0，min 1.507@iter50，非回归）；全程 **min=1.027@iter150**（<T023 的 1.232）；val 3.244→**1.706**（优于 T023 的 1.792）。loss 在 iter100 后继续下降（105=1.127, 115=1.125, 125=1.062, 140=1.058, 150=1.027）。
- **产物验证 PASS**：15 个 adapter（0000010~0000150，全步长10），key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载。
- **Dispatcher 判定 PASS**：前50 iters min 1.507 为确定性 seed 复现所致非回归；全程 min 1.027 与 val 1.706 均优于验收基准，收敛意图达成。

## 2026-08-12 — T023: 第二次 LoRA 训练（100 iters）收敛验证（PASS）

- **配置**：`configs/lora_train_safe.yaml` v3：iters 50→**100**、save_every 5→10（100 iters 产 10 checkpoint）、steps_per_report 10→5（更细粒度 loss）。seq=512 / layers=4 / rank=4 保持。
- **训练**：完整 100 iters，**整机未重启**（boot 19:06:35），Peak mem 13.566GB（<20GB），正常退出 train_rc=0。
- **loss 曲线**（每5 iter，20 采样点）：train 从 3.597（iter5）下降，**min 1.232（iter75）**、iter90=1.233、iter95=1.251；iter100 终值 1.817 为单 batch 噪声回升（batch_size=1）。val：iter1=3.244 → **iter100=1.792**（<2.040）。
- **产物验证 PASS**：新 run 10 个 adapter（0000010~0000100，save_every=10），key 集一致（62 keys）、大小 5.0~6.0MB、无 NaN/Inf、safetensors 可加载。
- **Dispatcher 判定**：T023 PASS。依据 min train loss 1.232 + val loss 1.792 判定收敛；iter100 单点 1.817 不适用于单 batch 噪声场景。建议后续以 min train loss / 滑动平均作为收敛判据。

## 2026-08-12 — T022: T021 训练产物完整性验证 + 收敛趋势基线（PASS）

- **Adapter 产物验证 PASS**：`outputs/lora_adapters_safe/` 恰有 10 个 adapter 文件（0000005~0000050），每个 5,417,491 字节（≈5.2MB）非空；10 个文件 key set 完全一致（62 keys）；safetensors/mlx 加载全部成功，全部 LoRA a/b shape 与 rank=4 一致（lora_a `(4096,4)`/`(12288,4)`、lora_b `(4,*)`）；无 NaN/Inf；总参数 1,352,448 与训练日志 trainable 1.352M 吻合。注：peft 未安装（安装受禁），等价验证改用 safetensors+mlx 完成。
- **loss 收敛验证 PASS**（7 点数据基线）：train [iter10=3.149, iter20=2.125, iter30=2.149, iter40=1.931, iter50=1.534]，val [iter1=3.244, iter50=2.040]。总体下降趋势明显（train 3.149→1.534，val 3.244→2.040）；iter30 微小回升（2.125→2.149）属正常波动，不构成收敛失败。
- **已知限制（Dispatcher 已确认接受）**：configs/lora_train_safe.yaml `steps_per_report=10` 导致日志仅有 7 个 loss 采样点（不足验收预设的每5 iter / 10点）。T022 禁改 configs/scripts/ 无法重跑，粒度差异为已知限制，不影响 PASS。若后续需更细粒度 loss 曲线，可在下个训练批次调低 steps_per_report。

## 2026-08-12 — T021: 训练强度再收窄 + 强化内存保护，安全完成首次完整训练

- **背景**：T020 安全配置（seq=1024/layers=8）仍致整机 19:06 再次崩溃重启（Peak mem 33.6GB / wired 20.2GB / free 27MB）。
- **配置再收窄** `configs/lora_train_safe.yaml` v2：max_seq_length 1024→**512**、num_layers 8→**4**、LoRA rank 8→**4**（trainable 5.410M→1.352M）、clear_cache_threshold 2GB→1GB、save_every 10→5、iters 100→50。
- **内存保护强化** `scripts/memory_monitor.sh`：wired 红线 20GB→**18GB(18432MB)**、**新增 free<500MB 触发终止**、采样 5s→**2s**、连续 3 次命中才触发（防模型加载瞬态误杀）。
- **训练前检查** `scripts/train_safe.sh`：列出 Top5 高内存进程 + 内存快照，非 tty 自动跳过确认。
- **训练结果**：完整 50/50 iters，final train loss **1.534** / val loss **2.040**；**Peak mem 13.5GB**（此前 33.6GB）；free 全程 >5GB；wired 峰值 ~12.6GB。产物 `outputs/lora_adapters_safe/` 10 个 adapter（0000005~0000050，各 ~5.4MB）+ adapters.safetensors。
- **整机未重启**（boot 仍 19:06:35，load avg 23.7→1.61）。

## 2026-08-12 — T019/T020: 训练中电脑自动重启根因 + 内存保护落地

- **根因（T019）**：18:45:48 内核 panic（`IOGPUGroupMemory.cpp:528` "pending memory object unexpectedly found in non pending hash"）→ 自动重启。panic 时训练进程 python3.11 RSS=**23.15GB**（24GB 机器，wired≈21.6GB/free≈125MB）。非电源/过热问题。
- **防复发（T020）**：新增 `configs/lora_train_safe.yaml`（max_seq_length=1024、num_layers=8、grad_checkpoint=true、clear_cache_threshold=2GB、save_every=10）+ `scripts/memory_monitor.sh`（free<2GB 或 wired>20GB 自动 SIGTERM 训练）+ `scripts/train_safe.sh`（启动训练并挂监控）。全部验证通过（bash -n / dry-run 解析 / 冒烟测试）。
- 警示：当前系统空闲内存仅 ~1GB，训练前务必关闭 Codex/Chrome/WeChat/Excel 等大内存应用。

## 2026-08-12 — T017: 指标计算管线

- 新增 `scripts/compute_metrics.py`：按 SKILL §14 定义计算 CIR/CPR/压缩比。
- 结果：train(92) CIR=1.0000 CPR=1.0000 comp=0.9234；eval(12) CIR=1.0000 CPR=1.0000 comp=0.9241；test(11) CIR=1.0000 CPR=1.0000 comp=0.9428。
- 输出：`logs/metrics_report.json` + `logs/metrics_run.log`；总体 PASS，记录总数 115 一致。
- 只读数据，未修改原始文件。

## 2026-08-12 — T016: 数据划分

- 新增 `scripts/split_data.py`（固定 seed=42，80/10/10 划分）。
- 结果：train=92 / eval=12 / test=11，sum=115，偏差 0，三子集互斥无重复。
- 输出：`data/eval_set/train/train.jsonl`、`data/eval_set/eval/eval.jsonl`、`data/eval_set/test/test.jsonl`。
- 未修改原始 `real_samples.jsonl`。

## 2026-08-12 — T015: 数据质量检查 + 评估集骨架

- 新增 `scripts/data_quality_check.py`：JSONL 格式/字段完整性/标签类型校验。
- 运行结果：`data/real_samples.jsonl` 115 条记录 0 issues，Overall PASS。
- 报告：`logs/data_quality_report.md`。
- 新增 `data/eval_set/{train,eval,test}/` 骨架 + `README.md`（建议 80/10/10 划分）。
- 未修改任何原始数据。

## 2026-08-12 — T014: 真实 LoRA 训练暂缓（约束冲突，BLOCKED）

- 原因：mlx_lm 仅接受 .safetensors（本地仅 GGUF Q4_K_M）；Ollama 支持 ADAPTER 加载但无训练命令；禁装工具/禁下载 safetensors。
- Dispatcher 决策：暂缓训练，转向数据/验证管线完善。
- 待约束解除后恢复：安装 llama.cpp 或授权 HF safetensors 下载。

## 2026-08-12 — T008~T013: Phase 1 数据管线 / 真实数据 / 评估 / 框架 / 权重

- 无依赖训练管线（SimpleRegressor）验证通过；17/17 pytest PASS。
- 真实数据 115 条（本地来源）；CIR=1.0000 / CPR=1.0000（verbatim 基线）。
- mlx-lm 0.31.3 已装（GPU/Metal OK）；qwen3.5:9b 权重确认在本地 Ollama 缓存。
- 详见 `logs/first_milestone_validation.log` / `logs/first_milestone_evaluation_report.md` / `logs/T011_training_report.md` / `logs/T012_framework_install_report.md`。
