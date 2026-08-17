#!/usr/bin/env node
// kimi-expert MCP server — stdio JSON-RPC (NDJSON), zero dependencies.
// Exposes one tool "ask-expert": forwards a distilled question to the local
// Kimi Code CLI (`kimi -p`) with the expert role brief injected.
import { spawn } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import readline from 'node:readline';

const KIMI = '/Users/hillo/.kimi-code/bin/kimi';
const ROLE_FILE = '/Users/hillo/Desktop/任务调度器/kimi_expert_role.md';
const DEFAULT_CWD = '/Users/hillo/Desktop/任务调度器';
const TIMEOUT_MS = 600000;
const CAPSULE_MAX = 1500;
const CAPSULE_VALUE_MAX = 80;

function readJson(path) {
  try { return existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : {}; }
  catch { return {}; }
}

function safe(value, max = CAPSULE_VALUE_MAX) {
  let s = String(value ?? '').replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (/sk-|api[_-]?key|token|password|\.ssh/i.test(s)) return '[filtered]';
  if (s.length > max) s = s.slice(0, max - 1) + '…';
  return s;
}

function metricsFrom(report) {
  const metrics = report?.results?.r3a || report?.results?.r3b || report?.results?.r3c;
  if (!metrics) return '无最新 Gate 指标';
  const keys = [
    'operative_status_accuracy', 'superseded_rejection',
    'wrong_authority_preference_rate', 'critical_t0_t1_miss',
    'invalid_output', 'authority_ranking_accuracy', 'material_conflict_recall'
  ];
  const found = keys.filter(k => metrics[k] !== undefined).map(k => `${k}=${metrics[k]}`);
  const verdict = report?.gate?.verdict || 'UNKNOWN';
  return safe(`${found.join(', ')}; verdict=${verdict}`, 240);
}

function globalContextCapsule() {
  const pipelinePath = `${DEFAULT_CWD}/调度状态/r3_pipeline_state.json`;
  const workingPath = `${DEFAULT_CWD}/调度状态/working_loop_state.json`;
  const pipeline = readJson(pipelinePath);
  const working = readJson(workingPath);
  const reportPath = pipeline.gate_report ? `${DEFAULT_CWD}/${pipeline.gate_report}` : '';
  const report = reportPath ? readJson(reportPath) : {};
  const lines = [
    '# 全局任务上下文胶囊（自动生成，非原始日志）',
    `goal: 完成 QXEN R2-R7 训练主线；当前先完成 R3A，再按 Gate 推进 R3B/R3C`,
    `phase/adapter: ${safe(pipeline.current_stage || 'unknown')} / ${safe(pipeline.adapter || working.adapter || 'unknown')}`,
    `next_action: ${safe(pipeline.next_action || 'unknown')}`,
    `stage_status: ${safe(pipeline.stage_status || working.phase || 'unknown')}`,
    `last_gate_metrics: ${metricsFrom(report)}`,
    `train_progress: ${safe(`${working.last_iter ?? '?'} / ${working.target_iters ?? '?'}; loss=${working.train_loss ?? '?'}; val=${working.val_loss ?? '?'}; mem=${working.peak_mem_gb ?? '?'}GB`, 180)}`,
    'frozen_constraints: fresh 数据、Gate 协议和 Base 权重不改；训练/评估不并行；R3A Gate 未过不得启动 R3B',
    'key_decisions: Gate FAIL 不无限 resume；优先失败聚类/数据修复；专家给方向建议，GPT 保留执行权',
    'context_policy: 仅指标/结论；不含 raw 日志、完整账本、API key；超限截断'
  ];
  let capsule = lines.join('\n');
  if (capsule.length > CAPSULE_MAX) capsule = capsule.slice(0, CAPSULE_MAX - 25) + '\n[truncated=true]';
  return capsule;
}

const TOOL = {
  name: 'ask-expert',
  description:
    'Consult Kimi-Expert (Kimi Code CLI) on architecture/direction questions, ' +
    'failure escalation review, or token-economy audit. The expert role brief is ' +
    'injected automatically. Pass a DISTILLED question + evidence, never raw dumps. ' +
    'Returns VERDICT/REASON/ACTION advice; the caller keeps all execution rights.',
  inputSchema: {
    type: 'object',
    properties: {
      prompt: { type: 'string', minLength: 1, description: 'Distilled question with key evidence.' },
      cd: { type: 'string', description: 'Working directory for the Kimi session (optional).' },
    },
    required: ['prompt'],
  },
};

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

function askExpert(prompt, cd) {
  const role = readFileSync(ROLE_FILE, 'utf8');
  const capsule = globalContextCapsule();
  const fullPrompt = role + '\n\n---\n\n' + capsule +
    '\n\n# 来自 Codex 主 Agent 的当前问题\n\n' + prompt;
  return new Promise((resolve) => {
    const child = spawn(KIMI, ['-p', fullPrompt, '--output-format', 'text'], {
      cwd: cd || DEFAULT_CWD,
      env: process.env,
    });
    let out = '', err = '';
    const timer = setTimeout(() => { child.kill('SIGTERM'); }, TIMEOUT_MS);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', (e) => { clearTimeout(timer); resolve({ ok: false, text: String(e) }); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0 && out.trim()) resolve({ ok: true, text: out.trim() });
      else resolve({ ok: false, text: `kimi exited ${code}\n${err.trim()}\n${out.trim()}`.trim() });
    });
  });
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  line = line.trim();
  if (!line) return;
  let req;
  try { req = JSON.parse(line); } catch { return; }
  const { id, method, params } = req;
  if (method === 'initialize') {
    send({ jsonrpc: '2.0', id, result: {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'kimi-expert', version: '1.0.0' },
    }});
  } else if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: [TOOL] } });
  } else if (method === 'tools/call' && params?.name === 'ask-expert') {
    const r = await askExpert(params.arguments?.prompt ?? '', params.arguments?.cd);
    send({ jsonrpc: '2.0', id, result: {
      content: [{ type: 'text', text: r.text }],
      isError: !r.ok,
    }});
  } else if (id !== undefined) {
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: `unknown method: ${method}` } });
  }
});
