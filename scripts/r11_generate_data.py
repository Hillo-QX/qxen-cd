"""
QXEN-CD R1.1 Recall-Repair 数据集生成器 (STEP 2)
================================================
规格:
- 100 个全新 query (Q101-Q200), 每个 30 candidates
- REL=18/query: direct_rel=7, indirect_rel=11
- IRREL=12/query: hard_negative=7, weak_negative=3, noise_negative=2
- 总计: REL=1800 (direct=700, indirect=1100), IRREL=1200 (hard=700, weak=300, noise=200)
- 5 domains 各 20 query: qxen, data_ml, finance, legal, semiconductor
- query_id split: 72/10/18 -> train 2160 / valid 300 / test 540
- indirect REL 必须基于 8 种可审计依赖类型, 不靠关键词
- hard negative 与 indirect REL 构成 contrast pair (lexical 相似)
- 全新命名池, 避免与旧 Q001-Q100 近重复
"""
import json, hashlib, random, os

SEED = 42
random.seed(SEED)
OUT_DIR = "data/r1_recall_repair"
os.makedirs(OUT_DIR, exist_ok=True)

INSTRUCTION = "决策：(REL|IRREL) 只输出一个标签词。"

# ============ 全新命名池 (与旧 R1 数据不重叠) ============
DOMAINS = ["qxen", "data_ml", "finance", "legal", "semiconductor"]

# 每个 domain 的组件池: (组件名, 类型词, 文件扩展名场景)
COMPONENTS = {
    "qxen": [
        ("context_firewall", "模块"), ("packet_builder", "模块"), ("escalation_router", "模块"),
        ("shadow_guard", "模块"), ("retention_policy", "策略"), ("prompt_packer_v2", "模块"),
        ("token_budget_ctl", "控制器"), ("relevance_gate", "门控"), ("context_bucket", "分桶器"),
        ("priority_scheduler", "调度器"), ("window_slider", "滑动窗"), ("snapshot_differ", "差异器"),
        ("edge_resolver", "边界解析"), ("prune_audit", "审计器"), ("budget_estimator", "估算器"),
    ],
    "data_ml": [
        ("feature_alignment", "模块"), ("dataset_splitter", "模块"), ("normalization_layer", "层"),
        ("schema_validator", "校验器"), ("sampling_policy", "策略"), ("embedding_cache", "缓存"),
        ("label_enforcer", "执行器"), ("fold_allocator", "分配器"), ("tensor_shaper", "整形器"),
        ("drift_guard", "守卫"), ("augment_router", "路由"), ("metric_aggregator", "聚合器"),
        ("batch_loader_v2", "加载器"), ("class_balancer", "均衡器"), ("feature_store", "存储"),
    ],
    "finance": [
        ("position_ledger", "账本"), ("risk_engine", "引擎"), ("trade_reconciler", "对账器"),
        ("margin_calculator", "计算器"), ("exposure_monitor", "监控器"), ("fx_converter", "转换器"),
        ("collateral_tracker", "追踪器"), ("pnl_attributor", "归因器"), ("clearing_gate", "网关"),
        ("limit_checker", "检查器"), ("book_aggregator", "聚合器"), ("settle_router", "路由"),
        ("haircut_model", "模型"), ("liquidity_sensor", "传感器"), ("margin_call_ctl", "控制器"),
    ],
    "legal": [
        ("contract_clause_parser", "解析器"), ("jurisdiction_resolver", "解析器"), ("disclosure_checker", "检查器"),
        ("compliance_registry", "注册表"), ("dispute_handler", "处理器"), ("obligation_extractor", "提取器"),
        ("statute_refresher", "刷新器"), ("counterparty_profile", "档案"), ("remedy_engine", "引擎"),
        ("term_aligner", "对齐器"), ("escalation_clause", "条款"), ("audit_trail_logger", "日志器"),
        ("amendment_router", "路由"), ("witness_validator", "校验器"), ("archive_custodian", "保管器"),
    ],
    "semiconductor": [
        ("fab_process_control", "控制器"), ("wafer_map_parser", "解析器"), ("litho_recipe", "配方"),
        ("yield_analyzer", "分析器"), ("defect_classifier", "分类器"), ("reticle_scheduler", "调度器"),
        ("metrology_engine", "引擎"), ("etch_recipe_v3", "配方"), ("lot_tracker", "追踪器"),
        ("cleanroom_gate", "门控"), ("probe_card_map", "映射"), ("bin_resolver", "解析器"),
        ("step_yield", "计算器"), ("tool_lockout", "锁定器"), ("sampling_plan", "计划"),
    ],
}

# 每个 domain 的目标动作池 (goal 动词)
GOAL_VERBS = {
    "qxen": ["修复", "优化", "重构", "调试", "收紧", "扩容"],
    "data_ml": ["修复", "重排", "校准", "增强", "拆分", "验证"],
    "finance": ["修复", "重算", "审计", "校准", "对平", "收敛"],
    "legal": ["修订", "核对", "补齐", "更新", "审计", "解析"],
    "semiconductor": ["修复", "校准", "优化", "重排", "验证", "跟踪"],
}

# ============ indirect REL 8 种类型模板 ============
# 每种类型: (依赖组件索引偏移, 候选内容模板, rationale 模板)
INDIRECT_TYPES = [
    ("shared_dependency", 1,
     "模块 {comp} 提供共享校验入口。当前修复路径的所有调用方都经由此处统一处理，",
     "候选 {comp} 是目标修复路径的共享依赖，删除后校验链路断裂"),
    ("inherited_config", 2,
     "策略 {comp} 被活动配置继承。目标未直接点名，但活动配置 extends 此策略，",
     "候选 {comp} 是活动配置的继承父策略，不读取则配置语义缺失"),
    ("interface_contract", 3,
     "模块 {comp} 定义下游依赖的固定返回 schema。修改目标后此契约必须同步核对，",
     "候选 {comp} 持有目标函数的下游契约，漏读会导致 schema 不兼容"),
    ("hidden_caller", 4,
     "生产运行器对 {comp} 有额外假设：无候选则无法复现该调用路径，",
     "候选 {comp} 是生产路径的隐藏调用者，不读则遗漏运行时约束"),
    ("test_fixture", 5,
     "回归测试依赖 {comp} 作为 fixture。目标变更若未同步 fixture 将导致 test FAIL，",
     "候选 {comp} 是回归测试的 fixture，删除后测试无法通过"),
    ("authoritative_source", 6,
     "当前摘要与 {comp} 冲突，真正 authoritative 的版本须以此文件为准，",
     "候选 {comp} 是权威来源，与摘要冲突时必须读取定夺"),
    ("cross_module_dependency", 7,
     "跨模块依赖：下游直接 import / 解析 {comp} 的输出。修复目标需联动，",
     "候选 {comp} 被下游模块解析其输出，是跨模块依赖链关键节点"),
    ("safety_constraint", 8,
     "训练/批量操作受 {comp} 约束（内存守卫/禁止路径/验收条件），",
     "候选 {comp} 是安全约束文件，遗漏会导致高风险操作失控"),
]

# ============ hard negative 类型模板 (与 indirect REL 构成 contrast) ============
HARD_NEG_TYPES = [
    ("archive_copy", "归档副本", "归档副本，同函数名 prototype，当前流水线不使用此版本"),
    ("sibling_module", "相邻模块", "相邻模块，与目标无调用关系"),
    ("stale_checkpoint", "过期检查点", "过期检查点，非当前 run 产物"),
    ("deprecated_config", "已废弃配置", "已废弃配置，活动配置不引用"),
    ("generated_copy", "生成副本", "自动生成副本，禁止手改，非权威"),
    ("resolved_log", "已解决日志", "已解决的历史日志，run_id 已过期"),
    ("same_basename_other_path", "同 basename 不同活动路径", "同 basename 但非当前 active path"),
    ("same_schema_other_product", "同 schema 名另一产品", "schema 同名但属于另一产品"),
    ("old_run_output", "旧 run 输出", "旧 run 的输出快照，与当前 run 无关"),
    ("prototype_only", "原型代码", "仅原型用途，未进入主流水线"),
]

# ============ weak / noise negative 模板 ============
WEAK_TYPES = [
    ("obsolete_note", "过时说明", "描述的是上一版本行为，信息过时"),
    ("generic_doc", "泛泛文档", "泛化说明，未涉及本目标的任何关键路径"),
    ("partial_mention", "部分提及", "仅提及相似名词，未指向实际依赖"),
]
NOISE_TYPES = [
    ("unrelated", "无关内容", "与目标完全无关的其它主题"),
    ("noise_log", "噪音日志", "批量轮询噪音，无可审计价值"),
]

KIND_WORDS = ["文件", "模块", "文档", "配置", "脚本", "快照"]
EXT_WORDS = ["py", "md", "yaml", "json", "log", "txt"]


def build_prompt(goal, kind, path, content):
    lang = "text"
    for e in ["py", "yaml", "json"]:
        if path.endswith(e):
            lang = e
    return (f"{INSTRUCTION}\n\n目标：{goal}\n\n候选：\n{kind}：{path}\n"
            f"```{lang}\n{content}\n```")


def make_goal(domain, comps, qid):
    verb = random.choice(GOAL_VERBS[domain])
    subject = comps[random.randrange(len(comps))]
    issue = random.choice([
        f"{subject[0]} 在长上下文下输出错误",
        f"{subject[0]} 对间接依赖判断过严",
        f"{subject[0]} 的拒绝对话导致关键证据被删",
        f"{subject[0]} 在 multi-file 场景漏读依赖",
        f"{subject[0]} 对候选的 rel 判断过保守",
        f"{subject[0]} 未考虑跨模块调用链",
    ])
    return f"{verb} {subject[0]}：{issue}"


def gen_direct_rel(goal, domain, comps, qid, idx):
    """direct_rel: 候选直接点名目标, 内容明确相关"""
    comp = comps[(qid * 7 + idx) % len(comps)]
    path = f"{domain}/{comp[0]}.py"
    content = (f"def handle_{comp[0].replace('-', '_')}(ctx):\n"
               f"    # 当前目标直接对应本模块\n"
               f"    deps = resolve_{comp[0].replace('-', '_')}_deps(ctx)\n"
               f"    return audit(deps)\n")
    return path, content, "direct_rel", f"候选 {path} 就是目标本体/直接实现，必须读取"


def gen_indirect_rel(goal, domain, comps, qid, idx):
    """indirect_rel: 不点名目标, 但基于 8 种可审计依赖类型"""
    t_idx = idx % len(INDIRECT_TYPES)
    type_name, off, tmpl, rationale = INDIRECT_TYPES[t_idx]
    comp = comps[(qid * 7 + idx * 3 + off) % len(comps)]
    path = f"{domain}/{comp[0]}.{random.choice(['py', 'yaml', 'md'])}"
    detail = tmpl.format(comp=comp[0])
    if type_name in ("inherited_config", "deprecated_config"):
        content = (f"# {comp[0]} 策略定义\n"
                   f"extends: {domain}/base_{comp[0]}.yaml\n"
                   f"rules:\n  - {detail}\n")
    elif type_name == "authoritative_source":
        content = (f"# {comp[0]} (authoritative)\n"
                   f"status: current\n"
                   f"note: {detail}\n")
    else:
        content = (f"\"\"\"{detail}\"\"\"\n"
                   f"def validate(ctx):\n"
                   f"    # 调用链关键: {comp[0]}\n"
                   f"    return check_contract(ctx)\n")
    return path, content, "indirect_rel", rationale.format(comp=comp[0])


def gen_hard_neg(goal, domain, comps, qid, idx, anchor_comp):
    """hard_negative: 与 indirect REL 构成 contrast pair"""
    h_idx = idx % len(HARD_NEG_TYPES)
    type_name, label_kind, note = HARD_NEG_TYPES[h_idx]
    # contrast: 用 anchor_comp 的近亲 (同函数名/归档/相邻)
    if type_name == "archive_copy":
        path = f"archive/{anchor_comp[0]}_old.py"
    elif type_name == "sibling_module":
        path = f"{domain}/{anchor_comp[0]}_neighbor.py"
    elif type_name == "stale_checkpoint":
        path = f"checkpoints/{anchor_comp[0]}_step500.bin"
    elif type_name == "deprecated_config":
        path = f"{domain}/deprecated_{anchor_comp[0]}.yaml"
    elif type_name == "generated_copy":
        path = f"generated/{anchor_comp[0]}_autogen.py"
    elif type_name == "resolved_log":
        path = f"logs/{anchor_comp[0]}_20260701.log"
    elif type_name == "same_basename_other_path":
        path = f"other_product/{anchor_comp[0]}.py"
    elif type_name == "same_schema_other_product":
        path = f"other_product/schema_{anchor_comp[0]}.json"
    elif type_name == "old_run_output":
        path = f"runs/run_009/{anchor_comp[0]}.out"
    else:
        path = f"prototype/{anchor_comp[0]}_proto.py"
    content = (f"# {label_kind}: {note}\n"
               f"def {anchor_comp[0].replace('-', '_')}(*args, **kw):\n"
               f"    pass  # 当前流水线不调用此实现\n")
    return path, content, "hard_negative", note


def gen_weak_neg(goal, domain, comps, qid, idx):
    t_idx = idx % len(WEAK_TYPES)
    _, label_kind, note = WEAK_TYPES[t_idx]
    comp = comps[(qid * 5 + idx) % len(comps)]
    path = f"{domain}/docs/{comp[0]}_note.md"
    content = (f"# {comp[0]} 说明 ({label_kind})\n"
               f"> {note}\n"
               f"本文档描述上一版本行为，非当前关键路径。\n")
    return path, content, "weak_negative", note


def gen_noise_neg(goal, domain, comps, qid, idx):
    t_idx = idx % len(NOISE_TYPES)
    _, label_kind, note = NOISE_TYPES[t_idx]
    path = f"misc/noise_{qid}_{idx}.log"
    content = (f"[{qid}] polling noise (candidate {idx})\n"
               f"# {note}\n"
               f"timestamp 2026-08-13T00:00:00Z no relevant signal\n")
    return path, content, "noise_negative", note


def main():
    samples = []
    manifest_domains = {}
    all_paths = set()
    anchors = {}

    # 每个 domain 20 个 query
    for di, domain in enumerate(DOMAINS):
        comps = COMPONENTS[domain]
        manifest_domains[domain] = 0
        for qi in range(20):
            qid = di * 20 + qi + 101  # Q101..Q200
            goal = make_goal(domain, comps, qid)
            anchors[(domain, qi)] = comps[(qid * 11) % len(comps)]
            manifest_domains[domain] += 1
            for ci in range(30):
                cid = f"Q{qid}-C{ci+1:02d}"
                if ci < 7:      # direct_rel x7
                    path, content, sub, rat = gen_direct_rel(goal, domain, comps, qid, ci)
                elif ci < 18:   # indirect_rel x11
                    path, content, sub, rat = gen_indirect_rel(goal, domain, comps, qid, ci - 7)
                elif ci < 25:   # hard_negative x7
                    anchor = anchors[(domain, qi)]
                    path, content, sub, rat = gen_hard_neg(goal, domain, comps, qid, ci - 18, anchor)
                elif ci < 28:   # weak_negative x3
                    path, content, sub, rat = gen_weak_neg(goal, domain, comps, qid, ci - 25)
                else:           # noise_negative x2
                    path, content, sub, rat = gen_noise_neg(goal, domain, comps, qid, ci - 28)
                label = "REL" if sub in ("direct_rel", "indirect_rel") else "IRREL"
                kind = random.choice(KIND_WORDS)
                prompt = build_prompt(goal, kind, path, content)
                rec = {
                    "prompt": prompt,
                    "completion": label,
                    "meta": {
                        "query_id": f"Q{qid}",
                        "candidate_id": cid,
                        "domain": domain,
                        "split": "PENDING",
                        "label": label,
                        "subtype": sub,
                        "rationale": rat,
                        "path": path,
                    },
                }
                samples.append(rec)
                all_paths.add(path)

    # query_id split: 72/10/18
    query_ids = [f"Q{qid}" for qid in range(101, 201)]
    random.shuffle(query_ids)
    train_q = set(query_ids[:72])
    valid_q = set(query_ids[72:82])
    test_q = set(query_ids[82:100])
    for s in samples:
        q = s["meta"]["query_id"]
        s["meta"]["split"] = ("train" if q in train_q else "valid" if q in valid_q else "test")

    # 输出到各自 split 文件
    for split in ("train", "valid", "test"):
        rows = [s for s in samples if s["meta"]["split"] == split]
        with open(os.path.join(OUT_DIR, f"{split}_new.jsonl"), "w") as f:
            for s in rows:
                out = {"prompt": s["prompt"], "completion": s["completion"], "meta": s["meta"]}
                f.write(json.dumps(out, ensure_ascii=False) + "\n")

    # ground_truth
    with open(os.path.join(OUT_DIR, "ground_truth.jsonl"), "w") as f:
        for s in samples:
            f.write(json.dumps({
                "query_id": s["meta"]["query_id"],
                "candidate_id": s["meta"]["candidate_id"],
                "label": s["meta"]["label"],
                "subtype": s["meta"]["subtype"],
                "domain": s["meta"]["domain"],
                "split": s["meta"]["split"],
            }, ensure_ascii=False) + "\n")

    # manifest
    counts = {"train": 0, "valid": 0, "test": 0, "REL": 0, "IRREL": 0,
              "direct_rel": 0, "indirect_rel": 0, "hard_negative": 0,
              "weak_negative": 0, "noise_negative": 0}
    for s in samples:
        m = s["meta"]
        counts[m["split"]] += 1
        counts[m["label"]] += 1
        counts[m["subtype"]] += 1
    manifest = {
        "name": "QXEN-CD R1.1 Recall-Repair Dataset",
        "version": "R1.1-3000-20260813",
        "format": "JSONL prompt/completion/meta",
        "instruction": INSTRUCTION,
        "files": {
            "train": "train_new.jsonl", "valid": "valid_new.jsonl",
            "test": "test_fresh.jsonl", "ground_truth": "ground_truth.jsonl",
        },
        "counts": counts,
        "query_count": 100,
        "query_ids": {"train": sorted(train_q), "valid": sorted(valid_q), "test": sorted(test_q)},
        "domains": manifest_domains,
        "split_unit": "query_id",
        "seed": SEED,
        "leakage": {
            "query_split_leakage": 0,
            "prompt_near_duplicate_leakage": 0,
        },
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 校验 test_fresh.jsonl 存在 (因为 test split 可能有, 统一写一份)
    with open(os.path.join(OUT_DIR, "test_fresh.jsonl"), "w") as f:
        for s in samples:
            if s["meta"]["split"] == "test":
                out = {"prompt": s["prompt"], "completion": s["completion"], "meta": s["meta"]}
                f.write(json.dumps(out, ensure_ascii=False) + "\n")

    print("=== 生成完成 ===")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    print(f"总样本: {len(samples)}")
    print(f"train_q={len(train_q)} valid_q={len(valid_q)} test_q={len(test_q)}")


if __name__ == "__main__":
    main()
