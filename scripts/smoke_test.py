#!/usr/bin/env python3
"""CI 冒烟测试：启动服务端，走一遍 MCP 握手，确认工具都注册上了。

不联网、不需要登录。
用法:  python scripts/smoke_test.py [--python /path/to/venv/python]
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPECTED = {
    "get_profile", "list_activities", "get_activity", "get_period_stats",
    "get_training_status", "get_health", "get_personal_records",
    "login", "render_chart",
}


def main() -> int:
    py = sys.executable
    if "--python" in sys.argv:
        py = sys.argv[sys.argv.index("--python") + 1]

    proc = subprocess.Popen(
        [py, str(ROOT / "garmin_mcp.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv(note=""):
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()[:800]
            raise SystemExit(f"服务端没有响应 {note}\nstderr: {err}")
        return json.loads(line)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}})
        init = recv("(initialize)")
        info = init["result"]["serverInfo"]
        assert info["name"] == "garmin", info
        print(f"  ✓ initialize -> {info['name']} {info['version']}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"] for t in recv("(tools/list)")["result"]["tools"]}
        print(f"  ✓ tools/list -> {len(tools)} 个工具")

        missing = EXPECTED - tools
        assert not missing, f"缺少工具: {sorted(missing)}"
        print("  ✓ 工具清单与预期一致")

        # 每个工具都要有非空 description，否则模型不知道何时调用
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        for t in recv("(tools/list)")["result"]["tools"]:
            assert t.get("description"), f"工具 {t['name']} 没有 description"
        print("  ✓ 所有工具都有 description")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
