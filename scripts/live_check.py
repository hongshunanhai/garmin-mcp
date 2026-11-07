#!/usr/bin/env python3
"""登录后逐工具自检：把 21 个工具挨个跑一遍，输出状态表。

  ./.venv/bin/python scripts/live_check.py                 # 全量
  ./.venv/bin/python scripts/live_check.py --json          # 机器可读
  ./.venv/bin/python scripts/live_check.py --only get_profile,get_gear
  ./.venv/bin/python scripts/live_check.py --activity 657618198

判定规则：
  ✅  返回了有效内容
  ⚠️  接口通了但没数据（账号里确实没有，不算失败）
  ❌  抛异常
  ⏭  跳过（需要活动 id 但账号没有记录，或本身是交互式工具）
"""
import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import garmin_mcp as G  # noqa: E402


def _note(out) -> str:
    """给结果生成一句摘要，方便一眼看出有没有内容。"""
    if isinstance(out, list):
        return f"{len(out)} 条"
    if not isinstance(out, dict):
        return str(out)[:40]
    if out.get("dry_run"):
        return "dry-run 正常"
    bits = []
    for k, v in out.items():
        if k in ("说明", "解读要点"):
            continue
        if isinstance(v, (list, tuple)):
            bits.append(f"{k} {len(v)} 项")
        elif isinstance(v, dict):
            bits.append(f"{k} {len([x for x in v if v[x] not in (None, [], {})])} 项")
        elif v not in (None, ""):
            bits.append(f"{k}={str(v)[:14]}")
        if len(bits) >= 2:
            break
    if not bits:
        return (out.get("说明") or out.get("解读要点") or "")[:40]
    return "；".join(bits)[:44]


def _has_content(obj) -> bool:
    if not isinstance(obj, dict):
        return bool(obj)
    meaningful = [k for k in obj if k not in ("说明", "解读要点")]
    return bool(meaningful)


def build_cases(activity_id: str | None):
    today = date.today().isoformat()
    cases = [
        ("get_profile", lambda: G.t_get_profile(S)),
        ("get_devices", lambda: G.t_get_devices(S)),
        ("list_activities", lambda: G.t_list_activities(S, limit=5)),
        ("get_period_stats", lambda: G.t_get_period_stats(S, "month", None, "run")),
        ("get_training_status", lambda: G.t_get_training_status(S, 14)),
        ("get_health", lambda: G.t_get_health(S, today)),
        ("get_personal_records", lambda: G.t_get_personal_records(S, None)),
        ("get_calendar", lambda: G.t_get_calendar(S, today[:7])),
        ("get_training", lambda: G.t_get_training(S, None, 5)),
        ("get_gear", lambda: G.t_get_gear(S)),
        ("get_achievements", lambda: G.t_get_achievements(S)),
        ("get_challenges", lambda: G.t_get_challenges(S)),
        ("get_social", lambda: G.t_get_social(S)),
        ("search_events", lambda: G.t_search_events(S, "马拉松", 120, 3)),
    ]
    if activity_id:
        cases += [
            ("get_activity", lambda: G.t_get_activity(S, activity_id, False, 30)),
            ("get_activity+streams", lambda: G.t_get_activity(S, activity_id, True, 60)),
            ("download_activity", lambda: G.t_download_activity(S, activity_id, "gpx")),
            ("render_chart", lambda: {"bytes": len(G._chart_activity(S, activity_id))}),
        ]
    cases += [
        ("create_workout(dry)", lambda: G.t_create_workout(
            None, "自检用课表", [{"type": "warmup", "duration_min": 5}], "run", None, False)),
        ("schedule_workout(dry)", lambda: G.t_schedule_workout(None, "0", today, False)),
        ("push_workout(dry)", lambda: G.t_push_to_device(None, False)),
        ("create_event(dry)", lambda: G.t_create_event(None, "自检赛事", today, "run", None, None, None, False)),
    ]
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--activity", default="")
    args = ap.parse_args()

    global S
    try:
        S = G.ensure_session(interactive=False)
    except G.AuthRequired as e:
        print(f"❌ {e}")
        print("   先在终端登录：./.venv/bin/python garmin_mcp.py login")
        return 2

    nickname = ""
    try:
        prof = G.api_opt(S, f"/gc-api/userprofile-service/socialProfile/{G._dn(S)}") or {}
        nickname = prof.get("fullName") or ""
    except Exception:
        pass
    session_info = {
        "site": G.SITES[S.site]["label"],
        "user": nickname or G._dn(S),
        "expires": (time.strftime("%Y-%m-%d %H:%M", time.localtime(S.expires))
                    if S.expires else None),
    }
    if not args.json:
        print(f"账号：{session_info['user']}（{session_info['site']}）")
        print(f"会话：有效至 {session_info['expires']}\n")

    # 自动挑一条活动
    activity_id = args.activity
    if not activity_id:
        try:
            rows = G.t_list_activities(S, limit=1).get("活动") or []
            activity_id = str(rows[0]["id"]) if rows else None
        except Exception:
            activity_id = None

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cases = [(n, f) for n, f in build_cases(activity_id) if not only or n in only]

    rows = []
    for name, fn in cases:
        if name.startswith(("get_activity", "download_activity", "render_chart")) and not activity_id:
            rows.append({"tool": name, "status": "⏭", "ms": 0, "note": "账号里没有活动可测"})
            continue
        t0 = time.time()
        try:
            out = fn()
            ms = int((time.time() - t0) * 1000)
            if isinstance(out, dict) and out.get("dry_run"):
                rows.append({"tool": name, "status": "✅", "ms": ms, "note": "dry-run 正常"})
            elif _has_content(out):
                rows.append({"tool": name, "status": "✅", "ms": ms, "note": _note(out)})
            else:
                rows.append({"tool": name, "status": "⚠️", "ms": ms,
                             "note": (out.get("说明") if isinstance(out, dict) else "") or "无数据"})
        except G.AuthRequired:
            rows.append({"tool": name, "status": "❌", "ms": 0, "note": "登录失效"})
            break
        except Exception as e:
            rows.append({"tool": name, "status": "❌", "ms": int((time.time() - t0) * 1000),
                         "note": f"{type(e).__name__}: {e}"[:70]})
            if not args.json:
                traceback.print_exc()

    if args.json:
        print(json.dumps({"session": session_info, "activity": activity_id, "rows": rows},
                         ensure_ascii=False, indent=1))
    else:
        print(f"{'工具':<24}{'状态':<6}{'耗时':>8}  说明")
        print("-" * 78)
        for r in rows:
            print(f"{r['tool']:<24}{r['status']:<6}{r['ms']:>7}ms  {r['note']}")
        print("-" * 78)
        ok = sum(1 for r in rows if r["status"] == "✅")
        warn = sum(1 for r in rows if r["status"] == "⚠️")
        bad = sum(1 for r in rows if r["status"] == "❌")
        skip = sum(1 for r in rows if r["status"] == "⏭")
        print(f"汇总：{ok} ✅ / {warn} ⚠️ / {bad} ❌ / {skip} ⏭")
        if bad:
            print("\n有 ❌，把上面的报错发我。")

    return 0 if not any(r["status"] == "❌" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
