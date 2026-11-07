#!/usr/bin/env python3
"""把 mcp-garmin 条目合并进 DSH profile 的 cordis.patch.yml。

用 ruamel.yaml 做 **round-trip 解析/写回**，因此：
  * 文件内容是 `[]`（合法空列表）或只剩注释时，都能正确追加
  * 注释、缩进、引号风格、其它条目都原样保留
  * 判断"是否已安装"依据**解析后的条目 id**，不会被注释里的同名文本骗到

写入前会重新解析一遍结果并断言条目确实存在，绝不在未生效的情况下报告成功。

依赖：ruamel.yaml（已列入 requirements.txt）

用法： append_dsh_patch.py <cordis.patch.yml> <python路径> <garmin_mcp.py路径>
"""
import io
import os
import shutil
import sys

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

ENTRY_ID = "mcp-garmin"


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def build_entry(python_path: str, script_path: str) -> CommentedMap:
    entry = CommentedMap()
    entry["id"] = ENTRY_ID
    entry["name"] = "@deepseek-ai/dsh-mcp-client"
    cfg = CommentedMap()
    cfg["serverName"] = "garmin"
    cfg["transport"] = "stdio"
    cfg["command"] = python_path
    args = CommentedSeq()
    args.append(script_path)
    cfg["args"] = args
    cfg["failOnStartupError"] = False
    cfg["toolCallTimeoutMs"] = 600000
    entry["config"] = cfg
    return entry


def _load(text: str):
    """返回 (数据, 原文是否没有任何 YAML 结构)。

    纯注释文件解析结果是 None —— 注释挂在 token 流上、没有节点承载，
    这种情况需要保留原文，不能直接丢掉。
    """
    y = _yaml()
    data = y.load(text) if text.strip() else None
    if data is None:
        return CommentedSeq(), True
    if not isinstance(data, CommentedSeq):
        raise SystemExit(f"  ✗ 顶层不是 YAML 列表（实际是 {type(data).__name__}），已跳过")
    return data, False


def _has_entry(data) -> bool:
    return any(isinstance(i, dict) and i.get("id") == ENTRY_ID for i in data)


def append(path: str, python_path: str, script_path: str) -> str:
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    data, structureless = _load(text)

    if _has_entry(data):
        return "already"

    if structureless and text.strip():
        # 原文只有注释/空行：把原文整体作为前缀保留，新条目接在后面。
        # 此时没有任何 YAML 结构可破坏，不存在粘行或覆盖问题。
        entry_seq = CommentedSeq()
        entry_seq.append(build_entry(python_path, script_path))
        buf = io.StringIO()
        _yaml().dump(entry_seq, buf)
        prefix = text if text.endswith("\n") else text + "\n"
        if not prefix.endswith("\n\n"):
            prefix += "\n"
        new_text = prefix + buf.getvalue()
    else:
        data.append(build_entry(python_path, script_path))
        buf = io.StringIO()
        _yaml().dump(data, buf)
        new_text = buf.getvalue()

    # 回验：重新解析，确认条目真的在列表里
    check, _ = _load(new_text)
    if not _has_entry(check):
        raise SystemExit("  ✗ 写入结果中找不到 mcp-garmin，已放弃写入")

    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(new_text)
    return "added"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("用法: append_dsh_patch.py <patch.yml> <python> <script>")
    status = append(sys.argv[1], sys.argv[2], sys.argv[3])
    if status == "already":
        print("  - 已存在 mcp-garmin（按解析结果判断），未改动")
    else:
        print(f"  ✓ {sys.argv[1]}（已追加）")
