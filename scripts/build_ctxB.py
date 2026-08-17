#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T050 — Phase B ctxB 数据集构建。

以 ctxA（T045, 108 条 7 标签）为基础，扩充非 DROP 样本，满足 T050 验收：
  1) 7 标签每类 >= 10 条
  2) 硬负样本（alteration）>= 48 条
  3) DROP 占比 <= 25%
  4) 字段完整（id/type/input_context/decision/reason/alteration/provenance）
  5) 统计记录到 logs/ctxB_stats.json

方法：
  - 基础 = ctxA context_decision_all.jsonl（108 条）；
  - DROP 只保留 48 条硬负样本（其余 1 条非硬负 DROP 移除，防 DROP 占比超限）；
  - 扩充 = 全部为非 DROP 类（PIN/KEEP/VERBATIM/COMPRESS/REFRESH/RETRIEVE）；
  - 输出 = data/ctxB.jsonl + logs/ctxB_stats.json（不修改任何 ctxA 产物）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "outputs", "context_decision_dataset"))

import generate_dataset as gd  # noqa: E402  (素材池复用)

VALID_DECISIONS = {"PIN", "KEEP", "VERBATIM", "COMPRESS", "DROP", "REFRESH", "RETRIEVE"}
HARD_TYPES = {"wrong_info", "stale_version", "backup", "semantic_similar", "redundant_log"}
OUT_JSONL = os.path.join(PROJECT_ROOT, "data", "ctxB.jsonl")
OUT_STATS = os.path.join(PROJECT_ROOT, "logs", "ctxB_stats.json")

# ---------------------------------------------------------------------------
# selection 扩充：全部非 DROP，覆盖 PIN/KEEP/VERBATIM/COMPRESS/REFRESH/RETRIEVE
# ---------------------------------------------------------------------------
SELECTION_EXTRA = [
    ("mcp", "CHUNK: 用户硬性要求：任何修复动作前必须先确认 dispatcher 当前版本号。",
     "PIN", "用户操作前置硬约束"),
    ("mcp", "CHUNK: 本轮修复的验收标准：dispatcher_health 返回 status=OK。",
     "PIN", "验收标准硬约束，不可丢弃"),
    ("mcp", "CHUNK: dispatcher 进程 PID 当前为 94042。",
     "KEEP", "当前进程标识，监控/管理所需"),
    ("mcp", "CHUNK: 错误栈第 1 行 'MCP server not responding'（原文）。",
     "VERBATIM", "错误原文是诊断锚点，逐字保留"),
    ("lora", "CHUNK: 训练配置中 adapter_path=outputs/lora_adapters_ctxA_balanced（原文）。",
     "VERBATIM", "精确路径，逐字保留"),
    ("lora", "CHUNK: 用户要求训练期间 Peak memory 不得超过 18GB。",
     "PIN", "安全边界硬约束"),
    ("data", "CHUNK: ctxB 数据集版本号登记为 ctxB-v1。",
     "VERBATIM", "精确版本标识"),
    ("data", "CHUNK: 当前待标注 jsonl 共 1750 条（来自用户提供的高质量数据集）。",
     "KEEP", "当前数据源规模事实"),
    ("eval", "CHUNK: 本次评估仅剩 3 条未推理。",
     "KEEP", "当前进度状态"),
    ("eval", "CHUNK: 评估脚本 --runs 参数合法值为 base/ctxA/ctxB。",
     "VERBATIM", "脚本接口定义，精确保留"),
    ("ledger", "CHUNK: T050 完成后需在账本登记 PASS 并调用 dispatch_next_task。",
     "PIN", "调度闭环硬约束"),
    ("ledger", "CHUNK: 账本 completed_tasks 记录格式 {task_id,status,summary}。",
     "VERBATIM", "精确格式要求"),
    ("mem", "CHUNK: 当前 free=2.1GB 未达 500MB 保护线。",
     "KEEP", "当前内存实时状态"),
    ("mem", "CHUNK: memory_monitor 采样间隔 2s（脚本默认值）。",
     "VERBATIM", "精确配置值"),
    ("data", "CHUNK: 需读取 data/distill_ctxA/valid.jsonl 统计验证集标签分布。",
     "RETRIEVE", "验证集信息不在当前 state，需读取文件"),
    ("eval", "CHUNK: 需确认 eval_decision.py 是否支持 --runs ctxB 参数。",
     "RETRIEVE", "脚本参数细节需读取脚本确认"),
    ("lora", "CHUNK: 需确认 T049 adapter 的最终 loss 记录。",
     "RETRIEVE", "训练日志信息需从磁盘读取"),
    ("data", "CHUNK: 需重新读取 manifest.json 核对 ctxB 的 provenance 版本。",
     "RETRIEVE", "manifest 内容需读取确认"),
    ("mcp", "CHUNK: README 记载的端口 8899 与最新配置 8891 可能不一致。",
     "REFRESH", "文档信息可能过期，需重新读取权威配置"),
    ("eval", "CHUNK: compute_metrics.py 的指标公式在 v3 可能有改动。",
     "REFRESH", "脚本可能变化，需重新核对"),
    ("lora", "CHUNK: 旧 adapter 的 README 描述可能与当前实现不符。",
     "REFRESH", "文档可能滞后于实现"),
    ("data", "CHUNK: ctxA 标签统计表（dataset_version=ctxA-v1）已过时。",
     "REFRESH", "旧版本统计，需按 ctxB-v1 重新统计"),
    ("mcp", "CHUNK: 修复方案有 A/B 两个候选，需根据最新日志决定。",
     "REFRESH", "决策依据需以最新状态为准"),
    ("lora", "CHUNK: 训练日志可能需要重新读取确认是否触发过内存保护。",
     "REFRESH", "历史日志可能更新，需重新确认"),
    ("data", "CHUNK: 计划说 92 条正样本来自 data/eval_set/train.jsonl（引用原文）。",
     "VERBATIM", "计划原文引用，保留精确表述"),
    ("eval", "CHUNK: 评估必须对比 base 与 ctxA，禁止只看训练 loss。",
     "PIN", "评估方法硬约束"),
    ("lora", "CHUNK: 训练优先级：可靠性 > 压缩率（原文规则）。",
     "PIN", "训练原则硬约束"),
    ("data", "CHUNK: ctxB 需要 7 标签每类至少 10 条（T050 验收标准原文）。",
     "PIN", "验收标准硬约束"),
    ("eval", "CHUNK: 评估脚本路径 scripts/eval_decision.py（原文）。",
     "VERBATIM", "精确脚本路径"),
    ("lora", "CHUNK: 训练超参 rank=8, grad_accum=4, lr=1e-5, iter=550（原文）。",
     "VERBATIM", "精确训练参数"),
    ("data", "CHUNK: ctxB 输出文件为 data/ctxB.jsonl（原文路径）。",
     "VERBATIM", "精确输出路径"),
    ("mem", "CHUNK: 训练前须 ollama stop 释放 5.5GB（硬约束）。",
     "PIN", "内存安全前置硬约束"),
    ("data", "CHUNK: 当前 ctxB 构建进度：基础 108 条已加载。",
     "KEEP", "当前进度状态"),
    ("eval", "CHUNK: held-out 评估集固定为 data/ctxA/heldout_ctxA.jsonl 193 条。",
     "KEEP", "固定评估集事实"),
    ("lora", "CHUNK: T049 平衡集 DROP 占比 24.4%（实测）。",
     "KEEP", "历史训练数据分布事实"),
    ("data", "CHUNK: ctxA 基础数据 DROP=49 其中 48 条硬负样本。",
     "KEEP", "数据构成事实"),
    ("mcp", "CHUNK: dispatcher MCP 端口 8891（权威配置确认）。",
     "VERBATIM", "精确端口值"),
    ("eval", "CHUNK: 门控脚本新规则：base 满分持平视为通过。",
     "PIN", "判定规则硬约束"),
    ("data", "CHUNK: 用户 1750 条高质量数据集尚未到位，ctxB 暂用现有数据。",
     "KEEP", "数据源现状"),
    ("ledger", "CHUNK: 账本 current_task 需更新为 T050 状态。",
     "KEEP", "账本维护状态"),
    ("mcp", "CHUNK: 需读取 scripts/memory_monitor.sh 确认保护线参数。",
     "RETRIEVE", "脚本参数需读取确认"),
    ("eval", "CHUNK: 需读取 logs/ctxA_training_balanced.log 的最终 loss。",
     "RETRIEVE", "训练日志需读取"),
    ("data", "CHUNK: 需读取 manifest.json 核对 1750 条数据集格式。",
     "RETRIEVE", "待确认格式需读取"),
    ("lora", "CHUNK: 需确认 T047 adapter_config 的 rank 值。",
     "RETRIEVE", "配置需读取确认"),
    ("mcp", "CHUNK: 旧文档说 health 端口 8899（可能与现行 8891 冲突）。",
     "REFRESH", "文档可能过期，需重新确认"),
    ("eval", "CHUNK: 上次评估的 comparison_summary.json 可能需重读。",
     "REFRESH", "评估结果可能更新"),
    ("data", "CHUNK: ctxA-v1 的标签统计已不适用于 ctxB-v1。",
     "REFRESH", "统计随版本更新"),
    ("lora", "CHUNK: 旧训练日志结论在平衡数据下可能已失效。",
     "REFRESH", "结论需基于最新数据重新确认"),
    ("mcp", "CHUNK: 当前 dispatcher 配置文件中注册的端口是 8891。",
     "VERBATIM", "权威配置精确值"),
    ("lora", "CHUNK: 用户明确禁止修改 scripts/memory_monitor.sh。",
     "PIN", "用户禁区硬约束"),
    ("data", "CHUNK: ctxB 不允许使用用户 1750 条数据（未到位）。",
     "PIN", "T050 范围硬约束"),
    ("eval", "CHUNK: 评估 max_tokens=4 取首词标签（原文）。",
     "VERBATIM", "精确推理参数"),
    ("mem", "CHUNK: 当前 wired=8.5GB 低于 18GB 红线。",
     "KEEP", "当前内存状态"),
    ("ledger", "CHUNK: 账本需记录 T050 的 stats 路径。",
     "KEEP", "登记信息"),
    ("data", "CHUNK: ctxB 统计需记录到 logs/ctxB_stats.json（T050 验收）。",
     "PIN", "验收要求硬约束"),
    ("lora", "CHUNK: 需读取 outputs/lora_adapters_ctxA_balanced/ 确认 checkpoint。",
     "RETRIEVE", "产物需读取确认"),
    ("eval", "CHUNK: 需重读 eval_ctxA/comparison_summary_t049.json 的 deltas。",
     "RETRIEVE", "评估结果需读取"),
    ("data", "CHUNK: distill_ctxA 的 valid 分布可能已变化。",
     "REFRESH", "数据可能更新需重新核对"),
    ("mcp", "CHUNK: MCP 服务注册表可能已更新。",
     "REFRESH", "注册表可能变化"),
    ("data", "CHUNK: 版本登记格式 dataset_version=ctxB-v1（原文）。",
     "VERBATIM", "精确版本号"),
    ("eval", "CHUNK: 评估门控标准 4 指标（原文定义）。",
     "PIN", "评估标准硬约束"),
    ("lora", "CHUNK: T047 训练数据源是 outputs/context_decision_training（原文）。",
     "KEEP", "历史数据源事实"),
]

# ---------------------------------------------------------------------------
# distillation 扩充：COMPRESS
# ---------------------------------------------------------------------------
DISTILL_EXTRA = [
    ("CHUNK: Phase B 流程包括数据构建、训练格式准备、adapter 训练、held-out 评估、"
     "门控判定五步，每步有独立验收与产物记录。",
     "COMPRESS", "可压缩为：'B 五步走：数据→格式→训练→评估→门控'"),
    ("CHUNK: ctxB 数据要求 7 标签每类至少 10 条、硬负样本至少 48 条、"
     "DROP 占比不超过 25%，字段含 id/type/input_context/decision/reason/provenance。",
     "COMPRESS", "可压缩为：'ctxB：7类各≥10、硬负≥48、DROP≤25%'"),
    ("CHUNK: 训练用 rank=8、grad_accum=4、lr=1e-5、iters=550、seq=512、"
     "batch=1、grad_checkpoint、save_every=100、seed=0。",
     "COMPRESS", "可压缩为：'超参沿用 T047 清单，仅数据源变化'"),
    ("CHUNK: 评估集为 data/ctxA/heldout_ctxA.jsonl 共 193 条，指令统一 7 标签，"
     "gold 不变，推理 max_tokens=4 取首词，关闭 thinking。",
     "COMPRESS", "可压缩为：'评估集 193 条固定 + 推理侧 disable thinking'"),
    ("CHUNK: 内存保护线 wired>18GB 或 free<500MB 连续 3 次采样即 SIGTERM，"
     "训练前必须 ollama stop，峰值预期 7-14GB。",
     "COMPRESS", "可压缩为：'内存保护线 + 训练前 ollama stop'"),
    ("CHUNK: 门控规则为 4 指标超 base 即 PASS；base 满分时持平视为通过，"
     "stale_rejection 1.0 属天花板效应非退化。",
     "COMPRESS", "可压缩为：'门控：4 指标≥base，满分持平算过'"),
    ("CHUNK: ctxB 允许路径 data/outputs/scripts/logs，禁止 config/models/src 与 "
     "用户 1750 条数据（未到位）。",
     "COMPRESS", "可压缩为：'ctxB 边界：不动 config/models/src/1750数据'"),
    ("CHUNK: 训练数据需转 chat 格式 {prompt,completion}，completion 为单标签词，"
     "指令恰出现一次，token 预算 500。",
     "COMPRESS", "可压缩为：'训练格式：单标签词 + 指令唯一 + token 预算'"),
    ("CHUNK: 评估时模型用 apply_chat_template(add_generation_prompt=True) 包裹，"
     "enable_thinking=False 防 thinking 吃掉 max_tokens。",
     "COMPRESS", "可压缩为：'推理同格式包裹 + disable thinking'"),
    ("CHUNK: 数据校验需通过 pytest 与字段完整性检查，防 P1 四 bug 复发。",
     "COMPRESS", "可压缩为：'数据校验进测试，防历史 bug 复发'"),
]

# ---------------------------------------------------------------------------
# preference 扩充：PIN/KEEP/VERBATIM/REFRESH/RETRIEVE/COMPRESS
# ---------------------------------------------------------------------------
PREF_EXTRA = [
    ("mcp", "先 REFRESH 权威配置再修端口", "直接照抄 README 旧端口 8899",
     "REFRESH", "权威源优先，缓存可能过期"),
    ("mcp", "保留错误原文+精确 PID，修复可验证", "改写错误文本，丢失 PID 无法核对",
     "VERBATIM", "原文与数值是验证锚点"),
    ("lora", "保留安全边界（Peak≤18GB）+验收标准", "只记训练配置，漏安全边界",
     "PIN", "Rejected 漏硬约束，违反安全纪律"),
    ("lora", "记录 adapter 路径原文+训练参数", "缩写 adapter 路径，找不到产物",
     "VERBATIM", "路径缩写导致产物丢失"),
    ("data", "保留 1750 条数据集 provenance 原文", "省略 provenance 字段",
     "KEEP", "溯源字段是数据可信度前提"),
    ("data", "需读取 manifest 核对版本再登记", "凭记忆写版本号 ctxB-v0",
     "RETRIEVE", "Rejected 凭记忆导致版本错误"),
    ("eval", "193 条 held-out 全跑完再下结论", "只跑 10 条就断言达标",
     "KEEP", "样本不足结论不可信"),
    ("eval", "先确认脚本是否支持 ctxB 参数", "直接运行默认配置",
     "RETRIEVE", "Rejected 未确认接口，运行报错"),
    ("ledger", "T050 PASS 后立即登记再 dispatch", "PASS 后拖延登记",
     "KEEP", "账本闭环及时性要求"),
    ("mem", "达到保护线立即 SIGTERM", "等最后一个 batch 跑完再终止",
     "PIN", "Rejected 冒整机重启风险"),
    ("lora", "训练前确认 ollama 已 stop", "直接开始训练",
     "PIN", "Rejected 可能触发内存保护中断"),
    ("data", "标签统计以生成后 manifest 为准", "沿用 ctxA-v1 旧统计",
     "REFRESH", "旧统计过期，需重新核对"),
    ("mcp", "保留当前报错原文+权威路径，修复可验证", "改写报错文本，丢失原文细节，无法核对",
     "VERBATIM", "错误原文是诊断锚点"),
    ("eval", "含当前状态+base 对照，明确主次", "备份与当前混排，无法区分权威源",
     "KEEP", "Rejected 混淆权威来源"),
    ("lora", "保留验收标准并对照平衡集基线", "只记录训练配置，不记录验收标准",
     "PIN", "Rejected 漏验收标准，无法判断达标"),
    ("data", "硬负样本与正样本成对出现", "只有正样本，模型学不会拒绝",
     "KEEP", "硬负样本是选择能力必要条件"),
    ("eval", "CIR/CPR 与 base 对比后下结论", "只看 adapter 相对上版的 delta",
     "KEEP", "Rejected 缺 base 对比，无法识别回归"),
    ("mem", "达到保护线立即安全终止", "尝试完成最后一个 epoch 再终止",
     "PIN", "Rejected 违背安全边界"),
    ("ledger", "TASK PASS 后立刻登记并继续", "PASS 后拖延登记，状态丢失",
     "KEEP", "账本及时性是闭环正确性前提"),
    ("data", "200 条含 DROP 均衡样本", "150 条无 DROP，'全 keep'即达标",
     "KEEP", "Rejected 导致退化达标"),
    ("mcp", "先 REFRESH 权威配置再动手", "直接照抄 README 旧端口",
     "REFRESH", "权威源优先，缓存可能过期"),
    ("eval", "193 条端到端评估全跑完再下结论", "只跑 5 条就断言达标",
     "KEEP", "Rejected 样本量不足"),
    ("lora", "独立目录 ctxB + 完整日志 + config", "覆盖旧 adapter 目录",
     "PIN", "Rejected 覆盖历史资产"),
    ("data", "每条样本带完整 provenance", "无溯源字段，无法回溯",
     "KEEP", "provenance 是数据可信度前提"),
]


def main() -> int:
    # 1) 基础 = ctxA 108 条，DROP 只保留硬负样本（48 条）
    base = []
    with open(os.path.join(PROJECT_ROOT, "outputs", "context_decision_dataset",
                           "context_decision_all.jsonl"), encoding="utf-8") as f:
        base = [json.loads(l) for l in f if l.strip()]
    base_keep = []
    for r in base:
        if r["decision"] == "DROP" and r.get("alteration") not in HARD_TYPES:
            continue  # 移除非硬负 DROP，控制占比
        base_keep.append(r)
    print(f"基础 ctxA: {len(base)} 条 -> 保留 {len(base_keep)} 条 (移除非硬负 DROP)")

    # 2) 扩充
    rows = list(base_keep)
    idx = [1000]

    def emit(task_key, chunk, decision, reason, stype, alter=None):
        rec = {
            "id": f"ctxB-{stype}-{idx[0]:04d}",
            "type": stype,
            "input_context": gd.build_input(task_key, chunk),
            "decision": decision,
            "reason": reason,
        }
        if alter:
            rec["alteration"] = alter
        rec["provenance"] = {
            "source": "qxen_trajectory_synthetic",
            "run_id": "T050-phaseB",
            "teacher": "rule-based-anchor",
            "teacher_version": "v1",
            "verified": False,
            "training_allowed": True,
            "generated_at": "2026-08-13",
            "dataset_version": "ctxB-v1",
        }
        idx[0] += 1
        rows.append(rec)

    for task_key, chunk, decision, reason, *alter in SELECTION_EXTRA:
        emit(task_key, chunk, decision, reason, "selection", alter[0] if alter else None)
    for i, (chunk, decision, reason) in enumerate(DISTILL_EXTRA):
        tk = ["mcp", "data", "lora", "eval", "mem", "eval", "data", "lora", "eval", "data"][i % 10]
        emit(tk, chunk, decision, reason, "distillation")
    for task_key, chosen, rejected, decision, reason in PREF_EXTRA:
        chunk = (f"CHUNK 候选 A（Chosen）: {chosen}\n"
                 f"CHUNK 候选 B（Rejected）: {rejected}")
        emit(task_key, chunk, decision, reason, "preference")

    # 3) 统计
    total = len(rows)
    by_dec = Counter(r["decision"] for r in rows)
    hard = sum(1 for r in rows if r.get("alteration") in HARD_TYPES)
    drop_ratio = by_dec["DROP"] / total

    print("\n=== ctxB 统计 ===")
    print(f"总数: {total}")
    for lbl in sorted(VALID_DECISIONS):
        print(f"  {lbl}: {by_dec.get(lbl, 0)} ({100*by_dec.get(lbl,0)/total:.1f}%)")
    print(f"硬负样本(alteration): {hard}")
    print(f"DROP 占比: {drop_ratio:.1%}")

    # 4) 验收
    checks = {
        "7 标签每类>=10": all(by_dec.get(lbl, 0) >= 10 for lbl in VALID_DECISIONS),
        "硬负样本>=48": hard >= 48,
        "DROP<=25%": drop_ratio <= 0.25,
        "字段完整": all(all(k in r for k in ("id", "type", "input_context", "decision", "reason", "provenance"))
                        for r in rows),
        "id 唯一": len({r["id"] for r in rows}) == total,
    }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    # 5) 写出
    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda x: x["id"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {
        "task_id": "T050",
        "dataset_version": "ctxB-v1",
        "total": total,
        "by_decision": dict(sorted(by_dec.items())),
        "hard_negative": hard,
        "drop_ratio": round(drop_ratio, 4),
        "base_ctxA": len(base),
        "base_kept": len(base_keep),
        "added": total - len(base_keep),
        "checks": checks,
        "output": OUT_JSONL,
        "generated_at": "2026-08-13",
    }
    with open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    ok = all(checks.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    print(f"写出: {OUT_JSONL}")
    print(f"统计: {OUT_STATS}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
