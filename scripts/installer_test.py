#!/usr/bin/env python3
"""安装器深测：在临时 HOME 里跑 install.sh，验证各客户端配置的正确性/幂等/不破坏原文件。

  ./.venv/bin/python scripts/installer_test.py          # 全部
  ./.venv/bin/python scripts/installer_test.py --keep   # 保留临时目录，便于排查

覆盖：
  * Codex：新建 / 保留已有内容 / 幂等（不重复写 section）
  * ZCode：新建 / 已有 native 配置时合并而不是覆盖
  * 通用兜底：只有 ~/.agents/mcp.json 时写那里，不新建 ~/.zcode
  * DSH：`[]` / 纯注释 / 无结尾换行 三种形态；多 profile 时必须拒绝写入
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}  {detail}")
        FAILS.append(name)


def run_install(home: Path, client: str, extra_env: dict | None = None):
    env = {k: v for k, v in os.environ.items() if k != "DSH_PROFILE"}
    env["HOME"] = str(home)
    env.pop("GARMIN_ALLOW_WRITE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(INSTALL), "--no-login", f"--client={client}"],
                          capture_output=True, text=True, env=env, timeout=180)


def fresh_home() -> Path:
    h = Path(tempfile.mkdtemp(prefix="gmcp-inst-"))
    (h / ".codex").mkdir()
    (h / ".claude").mkdir()
    return h


def test_codex():
    print("Codex")
    h = fresh_home()
    cfg = h / ".codex" / "config.toml"
    cfg.write_text('# 我的配置\nmodel = "o3"\n\n[profiles.work]\napproval = "never"\n')
    r = run_install(h, "codex")
    check("退出码 0", r.returncode == 0, r.stderr[-300:])
    data = tomllib.loads(cfg.read_text())
    srv = (data.get("mcp_servers") or {}).get("garmin") or {}
    check("写入 garmin 段", srv.get("command", "").endswith("python"), srv)
    check("保留其它配置", data.get("model") == "o3" and "work" in (data.get("profiles") or {}))
    check("保留注释", "# 我的配置" in cfg.read_text())

    r2 = run_install(h, "codex")
    text2 = cfg.read_text()
    check("幂等（只一个 garmin 段）", text2.count("[mcp_servers.garmin]") == 1,
          text2.count("[mcp_servers.garmin]"))
    check("二次安装仍退出 0", r2.returncode == 0, r2.stderr[-200:])
    shutil.rmtree(h, ignore_errors=True)


def test_zcode():
    print("ZCode")
    h = fresh_home()
    zc = h / ".zcode" / "cli" / "config.json"
    zc.parent.mkdir(parents=True)
    zc.write_text(json.dumps({"mcp": {"servers": {"other": {"command": "x"}}},
                              "theme": "dark"}, ensure_ascii=False))
    r = run_install(h, "zcode")
    check("退出码 0", r.returncode == 0, r.stderr[-300:])
    data = json.loads(zc.read_text())
    servers = data["mcp"]["servers"]
    check("新增 garmin", "garmin" in servers)
    check("保留原有 server", "other" in servers)
    check("保留其它字段", data.get("theme") == "dark")
    shutil.rmtree(h, ignore_errors=True)


def test_agents_fallback():
    print("ZCode 兜底到 ~/.agents")
    h = fresh_home()
    ag = h / ".agents" / "mcp.json"
    ag.parent.mkdir(parents=True)
    ag.write_text(json.dumps({"mcpServers": {"legacy": {"command": "y"}}}))
    r = run_install(h, "zcode")
    check("退出码 0", r.returncode == 0, r.stderr[-200:])
    data = json.loads(ag.read_text())
    check("写到 .agents", "garmin" in data["mcpServers"])
    check("保留原有条目", "legacy" in data["mcpServers"])
    check("没有新建 ~/.zcode", not (h / ".zcode").exists())
    shutil.rmtree(h, ignore_errors=True)


def dsh_home(content: str, profiles=("p1",)) -> Path:
    h = fresh_home()
    for p in profiles:
        d = h / ".dsh" / "profiles" / p
        d.mkdir(parents=True)
        (d / "cordis.patch.yml").write_text(content)
    return h


def test_dsh():
    print("DSH")
    # 1) 空列表 []
    h = dsh_home("[]\n")
    r = run_install(h, "dsh")
    patch = h / ".dsh" / "profiles" / "p1" / "cordis.patch.yml"
    text = patch.read_text()
    check("[] 形态：退出码 0", r.returncode == 0, r.stderr[-300:])
    check("[] 形态：写入 mcp-garmin", "mcp-garmin" in text, text[:120])
    check("[] 形态：仍是 YAML 列表", text.lstrip().startswith("- "), text[:60])
    shutil.rmtree(h, ignore_errors=True)

    # 2) 纯注释
    h = dsh_home("# 我的 profile\n# 不要删这段\n")
    r = run_install(h, "dsh")
    text = (h / ".dsh" / "profiles" / "p1" / "cordis.patch.yml").read_text()
    check("纯注释：保留注释", "# 我的 profile" in text and "# 不要删这段" in text, text[:120])
    check("纯注释：写入条目", "mcp-garmin" in text)
    shutil.rmtree(h, ignore_errors=True)

    # 3) 无结尾换行
    h = dsh_home("- id: existing\n  name: x")
    r = run_install(h, "dsh")
    text = (h / ".dsh" / "profiles" / "p1" / "cordis.patch.yml").read_text()
    check("无换行：保留原条目", "id: existing" in text, text[:120])
    check("无换行：写入新条目", "mcp-garmin" in text)
    check("无换行：没有粘行", "name: x- id:" not in text, text[:160])
    shutil.rmtree(h, ignore_errors=True)

    # 4) 多 profile：必须拒绝
    h = dsh_home("[]\n", profiles=("p1", "p2"))
    r = run_install(h, "dsh")
    t1 = (h / ".dsh" / "profiles" / "p1" / "cordis.patch.yml").read_text()
    t2 = (h / ".dsh" / "profiles" / "p2" / "cordis.patch.yml").read_text()
    check("多 profile：都不写入", "mcp-garmin" not in t1 and "mcp-garmin" not in t2, (t1, t2))
    check("多 profile：给出提示", "DSH_PROFILE" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    shutil.rmtree(h, ignore_errors=True)

    # 5) 显式指定 profile
    h = dsh_home("[]\n", profiles=("p1", "p2"))
    r = run_install(h, "dsh", extra_env={"DSH_PROFILE": "p2"})
    t2 = (h / ".dsh" / "profiles" / "p2" / "cordis.patch.yml").read_text()
    check("显式 DSH_PROFILE 生效", "mcp-garmin" in t2, t2[:80])
    shutil.rmtree(h, ignore_errors=True)


def main() -> int:
    if not INSTALL.exists():
        print("找不到 install.sh")
        return 2
    for fn in (test_codex, test_zcode, test_agents_fallback, test_dsh):
        fn()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 安装器测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
