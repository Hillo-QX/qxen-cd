#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codex_switch.py —— codex 双边回滚 app 核心逻辑（统一入口的后端）

功能：
  1. 切换 config.toml 的 model_provider（openai / necodex / kimi / deepseek），
     双向幂等；并按 CATALOG_FILES 维护 model_catalog_json 行（kimi 直连必须
     挂本地模型目录，否则 codex 回退元数据会带 web_search 工具被 kimi 400 拒绝）。
  2. 切换前把当前 .codex-global-state.json 的侧边栏字段快照到
     <CODEX_HOME>/backups/sidebar-<account>-<ts>.json（按离开方账号命名）。
  3. 切换后把目标账号最近一次侧边栏快照合并回 .codex-global-state.json；
     无快照时保留当前共享副本（作为该账号基线）。
  4. 把历史会话重标记到目标 provider（state_*.sqlite 的 threads.model_provider
     与 rollout jsonl 首行 session_meta.model_provider），保证切换后侧边栏/
     resume 列表能看到全部聊天记录；进行中的会话文件（5 分钟内改动）跳过。

安全边界：
  - 写 config.toml、.codex-global-state.json、state_*.sqlite 与 rollout jsonl；
  - 每次写前先备份（config / 侧边栏 / state 库）；写文件用原子替换（tmp + os.replace）；
  - 幂等：已是目标 provider 时不产生新备份、不改数据。

用法：
  python3 codex_switch.py --to openai|necodex|kimi|deepseek
  python3 codex_switch.py --status
  环境变量：CODEX_HOME（默认 ~/.codex，测试沙箱可覆盖）；SIDEBAR_BACKUP_DIR
  附加参数：--no-sidebar（只切 provider，不迁移侧边栏与会话归属）

四路 provider：
  - openai / necodex：ChatGPT 官方账号与 necodex 代理（model 均为 gpt-5.6-luna）；
  - kimi：Kimi For Coding 订阅，直连 api.kimi.com/coding/v1（Responses API，
    model 默认 k3-256k），不经过 CC Switch；
  - deepseek：DeepSeek 开放平台 api.deepseek.com，直连（wire_api="responses"）。
  kimi/deepseek 的 provider 段与密钥登记在注册表文件
  <CODEX_HOME>/model_providers_registry.toml（见 REGISTRY 常量），切换时按需
  把段合并进 config.toml；密钥只存在于注册表与 config.toml，不进脚本。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 侧边栏相关字段（桌面端 thread 列表 / 项目 / pinned / assignments 等）
SIDEBAR_KEYS = [
    "projectless-thread-ids",
    "pinned-thread-ids",
    "project-order",
    "thread-workspace-root-hints",
    "thread-project-assignments",
    "selected-project",
    "active-workspace-roots",
    "electron-saved-workspace-roots",
    "local-projects",
    "queued-follow-ups",
]

PROVIDERS = ("openai", "necodex", "kimi", "deepseek")

# 各 provider 对应的 model 行值；kimi/deepseek 可被注册表里的 model 覆盖
PROVIDER_MODELS = {
    "openai": "gpt-5.6-luna",
    "necodex": "gpt-5.6-luna",
    "kimi": "k3-256k",
    "deepseek": "deepseek-v4-flash",
}

# kimi/deepseek provider 段注册表（含密钥，权限 0600，勿提交/勿外发）
REGISTRY_NAME = "model_providers_registry.toml"

# 各 provider 对应的模型目录文件（置于 CODEX_HOME 下）；切换时维护
# config.toml 的 model_catalog_json 行：目标 provider 有目录则挂上，没有则删除该行。
# kimi 直连必须挂：codex 0.148 拉取 kimi /models 形状不兼容会回退到内置元数据，
# 回退元数据带 web_search 工具，kimi 端点会 400 拒绝。
CATALOG_FILES = {
    "kimi": "model-catalog.kimi.json",
}

# provider 的认证变量只写入进程环境，不再把 token 留在 config/registry 文本中。
PROVIDER_ENV_KEYS = {
    "kimi": "KIMI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


# ---------- 路径解析（运行时读取，测试沙箱可覆盖） ----------

def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))


def config_path() -> Path:
    return codex_home() / "config.toml"


def global_state_path() -> Path:
    return codex_home() / ".codex-global-state.json"


def backup_dir() -> Path:
    return Path(
        os.environ.get("SIDEBAR_BACKUP_DIR", str(codex_home() / "backups"))
    )


def codex_desktop_running() -> bool:
    """检测 ChatGPT/Codex 桌面端主进程是否在运行。

    桌面端运行时会把内存/sqlite 里的聊天状态写回 .codex-global-state.json，
    覆盖脚本的侧边栏恢复 → 切换后聊天列表不切换。切换前应退出桌面端。
    测试可用环境变量 CODEX_DESKTOP_RUNNING=0/1 覆盖实际检测。"""
    override = os.environ.get("CODEX_DESKTOP_RUNNING")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes")
    try:
        # Codex Desktop 的请求进程通常是 Frameworks/.../Helpers/Codex，
        # 不一定存在可匹配的 Contents/MacOS/ChatGPT 主进程。
        out = subprocess.run(
            ["pgrep", "-f", r"ChatGPT\.app/Contents/"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def say(msg: str, log_path: Path | None = None) -> None:
    print(msg)
    if log_path is not None:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")


# ---------- config.toml ----------

def read_config() -> str:
    return config_path().read_text(encoding="utf-8")


def current_provider() -> str | None:
    """返回当前 model_provider 值；未设置返回 None。"""
    if not config_path().exists():
        return None
    text = read_config()
    m = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def current_model() -> str | None:
    """返回当前 model 值；未设置返回 None。"""
    if not config_path().exists():
        return None
    text = read_config()
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def registry_path() -> Path:
    return codex_home() / REGISTRY_NAME


def _load_registry() -> dict:
    """读取 provider 注册表；无文件或解析失败返回 {}。"""
    path = registry_path()
    if not path.exists():
        return {}
    try:
        import tomllib
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def provider_model(target: str) -> str:
    """目标 provider 的 model 行值：注册表优先，其次内置默认。"""
    reg = _load_registry()
    entry = reg.get(target)
    if isinstance(entry, dict) and isinstance(entry.get("model"), str):
        return entry["model"]
    return PROVIDER_MODELS[target]


def switch_provider_config(target: str) -> None:
    """把 config.toml 切到 target provider，并把 model 行切到该 provider 的模型。
    同时维护 model_catalog_json 行（按 CATALOG_FILES 挂载/移除）。
    其余配置原样保留。"""
    text = read_config()
    model = provider_model(target)
    catalog = CATALOG_FILES.get(target)
    catalog_line = (
        f'model_catalog_json = "{codex_home() / catalog}"\n' if catalog else None
    )
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    for ln in lines:
        # 删除旧 provider 行与旧 catalog 行
        if re.match(r'^\s*model_provider\s*=\s*"', ln):
            continue
        if re.match(r'^\s*model_catalog_json\s*=\s*"', ln):
            continue
        # 替换 model 行
        if re.match(r'^\s*model\s*=\s*"', ln):
            out.append(f'model = "{model}"\n')
            out.append(f'model_provider = "{target}"\n')
            if catalog_line:
                out.append(catalog_line)
            inserted = True
            continue
        out.append(ln)
    if not inserted:
        head = f'model = "{model}"\nmodel_provider = "{target}"\n'
        if catalog_line:
            head += catalog_line
        out.insert(0, head)
    config_path().write_text("".join(out), encoding="utf-8")


def _find_backup_with_necodex() -> Path | None:
    """从最近含 [model_providers.necodex] 段的 config 备份中取最近者。"""
    candidates = sorted(
        codex_home().glob("config.toml.bak-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for bak in candidates:
        try:
            if "[model_providers.necodex]" in bak.read_text(encoding="utf-8"):
                return bak
        except OSError:
            continue
    return None


def _extract_section(text: str, section: str) -> str:
    """提取 [section] 到下一个 [ 开头的完整段文本。"""
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == f"[{section}]":
            start = i
            break
    if start is None:
        return ""
    out: list[str] = []
    for ln in lines[start:]:
        if ln != lines[start] and re.match(r'^\s*\[', ln):
            break
        out.append(ln)
    return "".join(out)


def _replace_bearer_token(text: str, env_key: str) -> tuple[str, str | None]:
    """把文本中的 bearer token 改成 env_key，并返回发现的 token（不打印）。"""
    pattern = re.compile(
        r'^(\s*)experimental_bearer_token\s*=\s*"([^"]+)"\s*$', re.M
    )
    match = pattern.search(text)
    if not match:
        return text, None
    replacement = f'{match.group(1)}env_key = "{env_key}"'
    return text[:match.start()] + replacement + text[match.end():], match.group(2)


def _normalize_config_auth(text: str, target: str, env_key: str) -> tuple[str, str | None]:
    """只规范 config 中 model_providers.<target> 段。"""
    section = _extract_section(text, f"model_providers.{target}")
    if not section:
        return text, None
    normalized, token = _replace_bearer_token(section, env_key)
    if normalized == section:
        return text, token
    return text.replace(section, normalized, 1), token


def _normalize_registry_auth(text: str, target: str, env_key: str) -> tuple[str, str | None]:
    """只规范 registry 顶层 target 段，避免误改另一个 provider。"""
    marker = re.compile(
        rf'(?ms)^\[{re.escape(target)}\]\s*$.*?(?=^\[(?:kimi|deepseek)\]\s*$|\Z)'
    )
    match = marker.search(text)
    if not match:
        return text, None
    block, token = _replace_bearer_token(match.group(0), env_key)
    return text[:match.start()] + block + text[match.end():], token


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _launchd_env_available(env_key: str) -> bool:
    if os.environ.get(env_key):
        return True
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["launchctl", "getenv", env_key],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except OSError:
        return False


def _publish_launchd_env(env_key: str, value: str, log_path: Path | None) -> bool:
    """把已有密钥交给当前用户的 launchd，供 GUI 启动的 Codex 继承。"""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["launchctl", "setenv", env_key, value],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return True
        say(f"❌ launchd 环境变量 {env_key} 写入失败，请手动设置", log_path)
    except OSError as exc:
        say(f"❌ launchd 环境变量 {env_key} 写入失败（{exc}）", log_path)
    return False


def _find_legacy_provider_token(target: str) -> str | None:
    """从本地受保护备份恢复旧 token；只返回内存值，不打印、不写回配置。

    迁移到 env_key 后，macOS 登录会话重置可能清掉 launchd 环境变量。
    这里保留一次可恢复路径，优先 auth 迁移备份，再看其他 config/registry 备份。
    """
    if target not in PROVIDER_ENV_KEYS:
        return None
    candidates: list[Path] = []
    preferred = [
        *codex_home().glob(f"config.toml.bak-auth-{target}-*"),
        *codex_home().glob(f"{REGISTRY_NAME}.bak-auth-{target}-*"),
    ]
    others = [
        *codex_home().glob("config.toml.bak-*") ,
        *codex_home().glob(f"{REGISTRY_NAME}.bak-*") ,
    ]
    seen: set[Path] = set()
    for path in sorted([*preferred, *others], key=lambda p: p.stat().st_mtime, reverse=True):
        if path in seen:
            continue
        seen.add(path)
        candidates.append(path)

    section_re = re.compile(
        rf"(?ms)^\[model_providers\.{re.escape(target)}\]\s*$.*?(?=^\[|\Z)"
    )
    registry_re = re.compile(
        rf"(?ms)^\[{re.escape(target)}\]\s*$.*?(?=^\[(?:kimi|deepseek)\]\s*$|\Z)"
    )
    token_re = re.compile(
        r'^\s*experimental_bearer_token\s*=\s*"([^"]+)"\s*$', re.M
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = (section_re.search(text) if path.name.startswith("config.toml")
                 else registry_re.search(text))
        if not match:
            continue
        token = token_re.search(match.group(0))
        if token and token.group(1):
            return token.group(1)
    return None


def prepare_provider_auth(target: str, log_path: Path | None = None) -> bool:
    """迁移旧的明文 token，并确保 GUI/CLI 可通过 env_key 读取认证。"""
    env_key = PROVIDER_ENV_KEYS.get(target)
    if not env_key:
        return True

    cfg_text = read_config() if config_path().exists() else ""
    reg_path = registry_path()
    reg_text = reg_path.read_text(encoding="utf-8") if reg_path.exists() else ""
    cfg_new, cfg_token = _normalize_config_auth(cfg_text, target, env_key)
    reg_new, reg_token = _normalize_registry_auth(reg_text, target, env_key)
    token = cfg_token or reg_token

    # 先保证新启动的桌面端能拿到密钥，再移除文件中的明文 token。
    if token:
        if not _publish_launchd_env(env_key, token, log_path):
            if not os.environ.get(env_key):
                say(f"❌ 未能设置 {env_key}，保留原认证配置，未完成迁移", log_path)
                return False
    elif not _launchd_env_available(env_key):
        # 登录/重启后 launchd 环境可能丢失；从迁移前的 0600 备份恢复一次。
        recovered = _find_legacy_provider_token(target)
        if recovered and _publish_launchd_env(env_key, recovered, log_path):
            say(f"🔐 已从受保护备份恢复 {env_key}，继续启用 {target}", log_path)
        else:
            say(f"❌ 未找到 {env_key}，无法启用 {target}。请先设置该环境变量", log_path)
            return False

    if cfg_new != cfg_text:
        bak = codex_home() / f"config.toml.bak-auth-{target}-{now_ts()}"
        shutil.copy2(config_path(), bak)
        _write_text_atomic(config_path(), cfg_new)
        say(f"🔐 已将 {target} 认证改为 {env_key}（config 备份: {bak}）", log_path)
    if reg_new != reg_text:
        bak = codex_home() / f"{REGISTRY_NAME}.bak-auth-{target}-{now_ts()}"
        shutil.copy2(reg_path, bak)
        _write_text_atomic(reg_path, reg_new)
        say(f"🔐 已清理注册表中的 {target} 明文认证（备份: {bak}）", log_path)
    return True


def ensure_provider_section(target: str) -> str:
    """确保 config.toml 含 [model_providers.<target>] 段。
    返回 "ok"（已存在）/ "registry"（从注册表合并）/ "backup"（从备份恢复）/
    "missing"（缺段且无来源，且该 provider 需要段）。openai 为内置 provider，直接 ok。"""
    if target == "openai":
        return "ok"
    text = read_config()
    reg = _load_registry()
    entry = reg.get(target)
    reg_section = entry["section"].strip() if isinstance(entry, dict) \
        and isinstance(entry.get("section"), str) else ""
    if f"[model_providers.{target}]" in text:
        existing = _extract_section(text, f"model_providers.{target}")
        if "PASTE_" in existing:
            return "placeholder"
        # 注册表里有更新的段（如 wire_api 协议变更）时，原地替换旧段
        if reg_section and existing.strip() != reg_section:
            config_path().write_text(
                text.replace(existing, reg_section + "\n"), encoding="utf-8")
            return "registry-updated"
        return "ok"
    # 1) 注册表
    if reg_section:
        if f"[model_providers.{target}]" not in reg_section:
            return "missing"
        if "PASTE_" in reg_section:
            return "placeholder"
        config_path().write_text(text.rstrip() + "\n\n" + reg_section + "\n",
                                 encoding="utf-8")
        return "registry"
    # 2) necodex 历史行为：从 config 备份恢复
    if target == "necodex":
        bak = _find_backup_with_necodex()
        if bak is not None:
            section = _extract_section(bak.read_text(encoding="utf-8"),
                                       "model_providers.necodex")
            if section.strip():
                config_path().write_text(text.rstrip() + "\n\n" + section,
                                         encoding="utf-8")
                return "backup"
    return "missing"


# ---------- CC Switch 本地路由（kimi 目标依赖） ----------

CC_SWITCH_DIR = Path.home() / ".cc-switch"
CC_SWITCH_ROUTER_ADDR = ("127.0.0.1", 15721)
CC_SWITCH_KIMI_ID = "kimi"


def cc_switch_set_current(provider_id: str) -> bool:
    """把 CC Switch 的 codex 当前供应商设为 provider_id。
    路由器每个请求都实时读库（provider_router.select_providers），即时生效。"""
    import sqlite3
    db_path = CC_SWITCH_DIR / "cc-switch.db"
    if not db_path.exists():
        return False
    try:
        db = sqlite3.connect(str(db_path))
        db.execute("UPDATE providers SET is_current=0 WHERE app_type='codex'")
        db.execute("UPDATE providers SET is_current=1 WHERE id=? AND app_type='codex'",
                   (provider_id,))
        db.commit()
        db.close()
    except Exception:
        return False
    sj = CC_SWITCH_DIR / "settings.json"
    try:
        data = json.loads(sj.read_text(encoding="utf-8"))
        data["currentProviderCodex"] = provider_id
        sj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return True


def cc_switch_router_state() -> str:
    """'ready' / 'no_router'（15721 未监听）/ 'no_provider'（当前供应商不是 kimi）/
    'no_db'（CC Switch 未安装）。"""
    import socket
    import sqlite3
    db_path = CC_SWITCH_DIR / "cc-switch.db"
    if not db_path.exists():
        return "no_db"
    try:
        with socket.create_connection(CC_SWITCH_ROUTER_ADDR, timeout=2):
            pass
    except OSError:
        return "no_router"
    try:
        db = sqlite3.connect(str(db_path))
        row = db.execute(
            "SELECT id FROM providers WHERE app_type='codex' AND is_current=1"
        ).fetchone()
        db.close()
    except Exception:
        return "no_db"
    if not row or row[0] != CC_SWITCH_KIMI_ID:
        return "no_provider"
    return "ready"


# ---------- 侧边栏快照 ----------

def load_global_state() -> dict:
    if not global_state_path().exists():
        return {}
    return json.loads(global_state_path().read_text(encoding="utf-8"))


def write_global_state(state: dict) -> None:
    """原子写回 .codex-global-state.json。"""
    fd, tmp = tempfile.mkstemp(
        dir=str(codex_home()), prefix="..codex-global-state.json.tmp-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, global_state_path())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def snapshot_sidebar(account: str) -> Path | None:
    """把当前侧边栏字段快照为 backups/sidebar-<account>-<ts>.json。
    返回快照路径；global-state 不存在或没有侧边栏字段时返回 None。"""
    state = load_global_state()
    payload = {k: state.get(k) for k in SIDEBAR_KEYS if k in state}
    if not payload:
        return None
    backup_dir().mkdir(parents=True, exist_ok=True)
    path = backup_dir() / f"sidebar-{account}-{now_ts()}.json"
    path.write_text(
        json.dumps({"account": account, "snapshot_at": now_ts(), "keys": payload},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def latest_snapshot(account: str) -> Path | None:
    """返回某账号最近一次侧边栏快照；无则 None。时间戳排序即时间序。"""
    if not backup_dir().exists():
        return None
    candidates = sorted(backup_dir().glob(f"sidebar-{account}-*.json"))
    return candidates[-1] if candidates else None


def restore_sidebar(account: str) -> Path | None:
    """把目标账号最近快照的侧边栏字段**整段替换**回 global-state。

    替换而非合并：先删除当前 state 中所有快照未覆盖的 SIDEBAR_KEYS，
    再写入快照字段（值为 None 表示该账号当时无此状态，直接清除该 key）。
    这样离开方账号的 thread/聊天列表不会残留到目标账号侧边栏（修复跨账号污染）。
    返回使用的快照路径；无快照时返回 None（首次切换，保留当前共享副本作基线）。"""
    snap = latest_snapshot(account)
    if snap is None:
        return None
    data = json.loads(snap.read_text(encoding="utf-8"))
    keys = data.get("keys", {})
    state = load_global_state()
    changed = False
    # 1) 清除离开方残留：当前 state 里存在但目标快照未覆盖的侧边栏字段
    for k in SIDEBAR_KEYS:
        if k in state and k not in keys:
            del state[k]
            changed = True
    # 2) 写入目标账号快照字段（None 值 → 清除该 key）
    for k, v in keys.items():
        if v is None:
            if k in state:
                del state[k]
                changed = True
        elif state.get(k) != v:
            state[k] = v
            changed = True
    if changed:
        write_global_state(state)
    return snap


# ---------- 聊天记录 provider 重标记 ----------

# 进行中的会话 jsonl 跳过窗口（秒）：运行中的 codex 持有文件句柄，
# 原子替换会让它后续的追加写入丢失。
ACTIVE_JSONL_GRACE_SEC = 300


def _state_dbs() -> list[Path]:
    return [p for p in codex_home().glob("state_*.sqlite")
            if p.suffix == ".sqlite"]


def _retag_state_db(target: str, log_path: Path | None) -> int:
    """把 state_*.sqlite 里 threads.model_provider 重标记为 target。
    桌面端侧边栏按该列过滤聊天列表。返回改动的总行数；无库返回 -1。"""
    import sqlite3
    total = 0
    dbs = _state_dbs()
    if not dbs:
        return -1
    for db_path in dbs:
        try:
            con = sqlite3.connect(str(db_path), timeout=30)
            pending = con.execute(
                "SELECT COUNT(*) FROM threads WHERE model_provider IS NOT ? "
                "OR model_provider != ?",
                (target, target),
            ).fetchone()[0]
            if pending:
                bak = backup_dir() / f"{db_path.name}.bak-switch-{target}-{now_ts()}"
                backup_dir().mkdir(parents=True, exist_ok=True)
                try:
                    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    dst = sqlite3.connect(str(bak))
                    src.backup(dst)
                    dst.close()
                    src.close()
                except Exception as exc:
                    say(f"⚠️  备份 {db_path.name} 失败（{exc}），跳过该库", log_path)
                    con.close()
                    continue
                con.execute(
                    "UPDATE threads SET model_provider=? "
                    "WHERE model_provider IS NOT ? OR model_provider != ?",
                    (target, target, target),
                )
                con.commit()
                total += pending
            con.close()
        except Exception as exc:
            say(f"⚠️  重标记 {db_path.name} 失败（{exc}）", log_path)
    return total


def _retag_rollout_files(target: str, log_path: Path | None) -> int:
    """重写 rollout jsonl 首行 session_meta 的 model_provider。
    返回改动的文件数。"""
    import time
    roots = [codex_home() / "sessions", codex_home() / "archived_sessions"]
    now = time.time()
    changed = 0
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*.jsonl"):
            try:
                if now - f.stat().st_mtime < ACTIVE_JSONL_GRACE_SEC:
                    continue
                with f.open("r", encoding="utf-8") as fh:
                    first = fh.readline()
                d = json.loads(first)
                payload = d.get("payload")
                if not isinstance(payload, dict) or "model_provider" not in payload:
                    continue
                if payload.get("model_provider") == target:
                    continue
                payload["model_provider"] = target
                new_first = json.dumps(d, ensure_ascii=False) + "\n"
                st = f.stat()
                fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix=".retag-")
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write(new_first)
                    with f.open("r", encoding="utf-8") as fh:
                        fh.readline()
                        shutil.copyfileobj(fh, out)
                os.replace(tmp, f)
                # 保留原 mtime：避免我们自己的重写把文件标记成"进行中会话"，
                # 导致短时间内来回切换时被 ACTIVE_JSONL_GRACE_SEC 跳过
                os.utime(f, (st.st_atime, st.st_mtime))
                changed += 1
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
    return changed


def migrate_thread_provider(target: str, log_path: Path | None) -> None:
    """切换 provider 时把历史会话重标记到目标 provider，保证侧边栏/
    resume 列表在两边都能看到全部聊天记录。"""
    db_rows = _retag_state_db(target, log_path)
    files = _retag_rollout_files(target, log_path)
    db_msg = "无 state 库" if db_rows < 0 else f"state 库改动 {db_rows} 行"
    say(f"🔁 聊天记录重标记 -> {target}：{db_msg}，jsonl 改动 {files} 个",
        log_path)


# ---------- 切换流程 ----------

def backup_config(tag: str) -> Path:
    """备份 config.toml 到 CODEX_HOME（含 tag 与时间戳）。"""
    bak = codex_home() / f"config.toml.bak-{tag}-{now_ts()}"
    shutil.copy2(config_path(), bak)
    return bak


def do_switch(target: str, migrate_sidebar: bool, log_path: Path | None,
              force: bool = False) -> int:
    if target not in PROVIDERS:
        say(f"❌ 未知 provider: {target}（可选: {'/'.join(PROVIDERS)}）", log_path)
        return 1

    cur = current_provider()
    if cur == target:
        say(f"✅ 已是 {target} 配置，无需切换（幂等通过）", log_path)
        if not prepare_provider_auth(target, log_path):
            return 1
        # 幂等路径也校正历史会话归属：覆盖"配置已是目标 provider 但
        # 聊天列表还挂在旧 provider 下"的残留状态（无改动时不产生备份）
        if migrate_sidebar:
            if codex_desktop_running() and not force:
                say("ℹ️  Codex 桌面端正在运行，聊天记录重标记已跳过"
                    "（退出 Codex 后再执行一次即可补齐）", log_path)
            else:
                migrate_thread_provider(target, log_path)
        say(f"--- 当前 provider ---\n{cur}", log_path)
        return 0

    # 0) 侧边栏迁移前置条件：Codex 桌面端运行时恢复会被覆盖，切换不生效
    if migrate_sidebar and codex_desktop_running() and not force:
        say("❌ Codex 桌面端正在运行：直接切换会被 Codex 立即覆盖，聊天列表不会切换。\n"
            "   请先退出 Codex 再切换（桌面 command 会先自动退出 Codex；"
            "或确认已退出后用 --force 跳过本检查）", log_path)
        return 1

    # 1) 备份 config.toml
    bak = backup_config(f"switch-{target}")
    say(f"📦 已备份 config.toml -> {bak}", log_path)

    # 在合并 provider 段前先迁移认证，避免旧明文 token 被注册表重新写回。
    if not prepare_provider_auth(target, log_path):
        return 1

    # 2) 快照离开方账号的侧边栏（cur 可能为 None → 用当前作为默认源）
    leaving = cur if cur in PROVIDERS else (PROVIDERS[1] if target == PROVIDERS[0] else PROVIDERS[0])
    snap = None
    if migrate_sidebar:
        snap = snapshot_sidebar(leaving)
        if snap is not None:
            say(f"🗂  已快照 {leaving} 侧边栏 -> {snap}", log_path)
        else:
            say(f"⚠️  当前无侧边栏字段可快照（首次切换，{leaving} 侧无基线）", log_path)

    # 3) 切换 provider（非内置 provider 需确保段存在：注册表 > 备份恢复）
    src = ensure_provider_section(target)
    if src == "missing":
        say(f"❌ 缺少 [model_providers.{target}] 段：请先把该 provider 的段与密钥"
            f"登记到 {registry_path()}（模板见同目录 .example 文件），中止切换", log_path)
        return 1
    if src == "placeholder":
        say(f"❌ {registry_path()} 里 [{target}] 的密钥仍是占位符（PASTE_...），"
            "请先填入真实密钥再切换", log_path)
        return 1
    if src != "ok":
        say(f"✅ [model_providers.{target}] 段已就绪（来源: {src}）", log_path)

    # 3b) kimi 已改为直连 api.kimi.com/coding/v1（不再依赖 CC Switch 本地路由）
    switch_provider_config(target)

    # 3c) 重标记历史会话的 provider 归属（侧边栏/resume 列表按它过滤）
    if migrate_sidebar:
        migrate_thread_provider(target, log_path)

    # 4) 恢复目标账号侧边栏
    used = None
    if migrate_sidebar:
        used = restore_sidebar(target)
        if used is not None:
            say(f"🔄 已恢复 {target} 侧边栏 <- {used}", log_path)
        else:
            say(f"ℹ️  {target} 无历史快照，保留当前共享侧边栏作为基线", log_path)

    # 5) 验证
    new_model = current_model()
    if current_provider() == target and new_model == provider_model(target):
        say(f"✅ 切换成功：codex 已切到 {target}", log_path)
        say("--- 验证 ---", log_path)
        say(f'model = "{new_model}"', log_path)
        say(f'model_provider = "{current_provider()}"', log_path)
        return 0
    say("❌ 切换验证失败，请检查 config.toml；备份见 " + str(bak), log_path)
    return 1


def do_status(log_path: Path | None) -> int:
    cur = current_provider()
    say(f"当前 provider: {cur if cur else '<未设置>'} (model: {current_model() or '<未设置>'})", log_path)
    reg = registry_path()
    if reg.exists():
        loaded = _load_registry()
        for name in ("kimi", "deepseek"):
            entry = loaded.get(name)
            if not isinstance(entry, dict):
                say(f"注册表 {name}: ❌ 未登记", log_path)
            elif "PASTE_" in str(entry.get("section", "")):
                say(f"注册表 {name}: ⚠️ 已登记但密钥是占位符 (model={entry.get('model')})", log_path)
            else:
                say(f"注册表 {name}: ✅ 就绪 (model={entry.get('model')})", log_path)
    else:
        say(f"注册表不存在: {reg}（kimi/deepseek 切换前需先登记，模板见 .example）", log_path)
    for name, env_key in PROVIDER_ENV_KEYS.items():
        section = _extract_section(read_config(), f"model_providers.{name}") \
            if config_path().exists() else ""
        if "env_key =" in section:
            say(f"{name} auth: ✅ env_key={env_key}", log_path)
        elif "experimental_bearer_token" in section:
            say(f"{name} auth: ⚠️ 仍是文件内 token（执行 --migrate-auth {name}）", log_path)
        else:
            say(f"{name} auth: {'✅ 环境变量可用' if _launchd_env_available(env_key) else '⚠️ 未检测到环境变量'}", log_path)
    if backup_dir().exists():
        snaps = sorted(backup_dir().glob("sidebar-*.json"))
        if snaps:
            say("已有侧边栏快照:", log_path)
            for s in snaps[-10:]:
                say(f"  - {s.name}", log_path)
        else:
            say("无侧边栏快照", log_path)
    else:
        say("无侧边栏快照目录", log_path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="codex 四路切换（openai/necodex/kimi/deepseek）")
    ap.add_argument("--to", choices=PROVIDERS, help="目标 provider")
    ap.add_argument("--status", action="store_true", help="显示当前状态与快照")
    ap.add_argument("--no-sidebar", action="store_true",
                    help="只切 provider，不做侧边栏迁移（旧脚本行为）")
    ap.add_argument("--force", action="store_true",
                    help="跳过 Codex 桌面端运行检查（仅用于已确认 Codex 退出的场景）")
    ap.add_argument("--migrate-auth", choices=tuple(PROVIDER_ENV_KEYS),
                    help="把 provider 的旧明文 token 迁移为 launchd 环境变量")
    ap.add_argument("--log", default="", help="日志文件路径（可选）")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else None

    if args.status:
        return do_status(log_path)
    if args.migrate_auth:
        return 0 if prepare_provider_auth(args.migrate_auth, log_path) else 1
    if not args.to:
        say("❌ 必须指定 --to openai|necodex|kimi|deepseek 或 --status", log_path)
        return 2
    return do_switch(args.to, migrate_sidebar=not args.no_sidebar,
                     log_path=log_path, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
