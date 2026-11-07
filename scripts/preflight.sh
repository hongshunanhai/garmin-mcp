#!/usr/bin/env bash
# 发布前自检：语法 / 未提交改动 / 敏感信息 / 测试
# 用法： ./scripts/preflight.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FAIL=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }
note() { printf "      %s\n" "$1"; }

echo "[1/6] shell 语法"
if bash -n install.sh; then ok "install.sh"; else bad "install.sh 语法错误"; fi
if bash -n scripts/preflight.sh; then ok "preflight.sh"; else bad "preflight.sh 语法错误"; fi

echo "[2/6] 未提交改动（没提交的改动 push 时不会上传）"
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
  ok "工作区干净"
else
  bad "存在未提交改动："
  git status --short | sed 's/^/      /'
fi

echo "[3/6] 敏感信息"
# KEEP_COOKIES 里每一种凭据形态都要有对应检测，别只盯 session
LEAK=0
if git grep -qE "session=Fe26\.2\*" -- . 2>/dev/null; then
  bad "跟踪文件中出现真实会话 Cookie（session=Fe26.2*）"; LEAK=1
fi
if git grep -qE "JWT_WEB=eyJ[A-Za-z0-9_-]{20,}" -- . 2>/dev/null; then
  bad "跟踪文件中出现 JWT_WEB（eyJ…）"; LEAK=1
fi
if git grep -qE "Connect-Csrf-Token:[[:space:]]*[0-9a-f]{8}-[0-9a-f]{4}" -- . 2>/dev/null; then
  bad "跟踪文件中出现真实 CSRF Token"; LEAK=1
fi
if git grep -qE "GARMIN-SSO-CUST-GUID=[0-9a-f]{8}-" -- . 2>/dev/null; then
  bad "跟踪文件中出现 GARMIN-SSO 凭据"; LEAK=1
fi
[ "$LEAK" -eq 0 ] && ok "无会话 Cookie / JWT / CSRF / SSO 凭据"
if [ -n "${USER:-}" ] && git grep -q "/Users/${USER}/" -- . 2>/dev/null; then
  bad "跟踪文件中出现个人绝对路径："
  git grep -l "/Users/${USER}/" -- . | head -5 | sed 's/^/      /'
else
  ok "无个人绝对路径"
fi
BIG=$(git ls-files -z | xargs -0 -I{} sh -c '
  f="{}"
  sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt 1048576 ] && echo "$f"
' 2>/dev/null)
if [ -n "$BIG" ]; then
  bad "存在大于 1MB 的跟踪文件："
  echo "$BIG" | head -5 | sed 's/^/      /'
else
  ok "无超大文件"
fi

echo "[4/6] 单元测试"
if [ -x ./.venv/bin/python ]; then
  if ./.venv/bin/python scripts/unit_tests.py >/tmp/preflight-unit.log 2>&1; then
    ok "通过（$(grep -c '✓' /tmp/preflight-unit.log) 项）"
  else
    bad "单元测试失败："; tail -6 /tmp/preflight-unit.log | sed 's/^/      /'
  fi
else
  bad "未找到 .venv，单元测试没有执行（先跑 ./install.sh）"
fi

echo "[5/6] MCP 协议握手"
if [ -x ./.venv/bin/python ]; then
  if ./.venv/bin/python scripts/smoke_test.py >/tmp/preflight-smoke.log 2>&1; then
    ok "通过"; else bad "握手失败："; tail -4 /tmp/preflight-smoke.log | sed 's/^/      /'
  fi
else
  bad "未找到 .venv，MCP 握手没有执行（先跑 ./install.sh）"
fi

echo "[6/6] 静态检查（ruff）"
if [ -x ./.venv/bin/ruff ]; then
  if ./.venv/bin/ruff check . >/tmp/preflight-ruff.log 2>&1; then
    ok "ruff 通过"
  else
    bad "ruff 有问题："; tail -8 /tmp/preflight-ruff.log | sed 's/^/      /'
  fi
elif command -v ruff >/dev/null 2>&1; then
  if ruff check . >/tmp/preflight-ruff.log 2>&1; then ok "ruff 通过"; else bad "ruff 有问题："; tail -8 /tmp/preflight-ruff.log | sed 's/^/      /'; fi
else
  bad "未安装 ruff（pip install -r requirements-dev.txt）"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ 自检通过，可以 commit / push"
  echo "   提醒：确认 README 里的 hongshunanhai 已换成真实地址"
else
  echo "❌ 有问题，先修再发"
fi
exit "$FAIL"
