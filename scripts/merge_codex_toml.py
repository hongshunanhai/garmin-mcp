#!/usr/bin/env python3
"""把 garmin 的 MCP 配置合并进 Codex 的 config.toml。

使用 tomlkit 做「保留格式」的编辑，因此：
  * 用户自己的 enabled / enabled_tools / disabled_tools / env_vars 原样保留
  * 注释、空行、其它 [table] 不受影响
  * 不会因为路径里带方括号（如 "/old/[local/python"）而误判数组边界

安全措施：
  * 解析失败（原文件不是合法 TOML）则拒绝改动
  * 写回前用标准库 tomllib 再校验一次，并对结果做键检查
  * 改动前留 .bak

用法： merge_codex_toml.py <config.toml> <python路径> <garmin_mcp.py路径>
"""
import os
import shutil
import sys
import tomllib

import tomlkit

MANAGED = ("command", "args", "startup_timeout_sec", "tool_timeout_sec")


def merge(path, python_path, script_path):
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if text.strip():
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"  ✗ {path} 不是合法 TOML（{e}），已跳过，未做任何修改")

    try:
        doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    except Exception as e:                       # tomlkit 解析异常同样拒绝
        raise SystemExit(f"  ✗ {path} 无法解析（{e}），已跳过，未做任何修改")

    servers = doc.get("mcp_servers")
    existed = False
    if servers is None:
        doc["mcp_servers"] = tomlkit.table()
        servers = doc["mcp_servers"]
    entry = servers.get("garmin")
    if entry is None:
        servers["garmin"] = tomlkit.table()
        entry = servers["garmin"]
    else:
        existed = True

    entry["command"] = python_path
    entry["args"] = [script_path]
    entry["startup_timeout_sec"] = 30.0
    entry["tool_timeout_sec"] = 600.0

    new_text = tomlkit.dumps(doc)

    # 写回前用标准库再验一次，并确认关键字段确实写对了
    try:
        parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"  ✗ 合并后 TOML 非法（{e}），已放弃写入")
    rq = (parsed.get("mcp_servers") or {}).get("garmin")
    if not rq or rq.get("tool_timeout_sec") != 600.0 or rq.get("args") != [script_path]:
        raise SystemExit("  ✗ 合并结果不符合预期，已放弃写入")

    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(new_text)
    print(f"  ✓ {path}（{'更新' if existed else '新增'}）")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("用法: merge_codex_toml.py <config.toml> <python> <script>")
    merge(sys.argv[1], sys.argv[2], sys.argv[3])
