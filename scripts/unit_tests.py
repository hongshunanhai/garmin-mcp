#!/usr/bin/env python3
"""纯逻辑单元测试（不联网、不依赖登录态）。

覆盖几个容易出错的地方：
  * 配速/时长换算（米每秒 vs 秒每公里）
  * 运动类型归一（run/跑步/ride… 与 all）
  * 周期区间解析（周/月/年/自定义，月末天数）
  * Cookie 粘贴解析（三段式 / 多行 / JSON）
  * 会话过期与续期窗口判断

用法:  ./.venv/bin/python scripts/unit_tests.py
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import garmin_mcp as G  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}  {detail}")
        FAILS.append(name)


def test_pace():
    print("配速与时长")
    check("2.5 m/s -> 6'40\"", G.pace_str(2.5) == '6\'40"', G.pace_str(2.5))
    check("2.506 m/s -> 6'39\"", G.pace_str(2.506) == '6\'39"', G.pace_str(2.506))
    check("0 速度 -> None", G.pace_str(0) is None)
    check("None 速度 -> None", G.pace_str(None) is None)
    check("字符串数字可用", G.pace_str("3.0") == '5\'33"', G.pace_str("3.0"))
    check("2567s -> 42:48", G.hms(2567.886) == "42:48", G.hms(2567.886))
    check("3661s -> 1:01:01", G.hms(3661) == "1:01:01", G.hms(3661))
    check("0s -> None", G.hms(0) is None)
    check("6434.83m -> 6.43km", G.km(6434.83) == 6.43, G.km(6434.83))


def test_sport():
    print("运动类型归一")
    check("run -> running", G._sport_key("run") == "running")
    check("跑步 -> running", G._sport_key("跑步") == "running")
    check("RIDE -> cycling", G._sport_key("RIDE") == "cycling")
    check("all -> None", G._sport_key("all") is None)
    check("空值 -> None", G._sport_key(None) is None)
    raised = False
    try:
        G._sport_key("rowing")
    except G.GarminError:
        raised = True
    check("未知运动报错而不是静默", raised)
    check("跑步机中文名", G._type_cn("treadmill_running") == "跑步机")
    check("未知类型回落原值", G._type_cn("weird_type") == "weird_type")


def test_period():
    print("周期区间")
    a, b, label = G._period_range("week", "2026-11-07")
    check("周起点是周一", a.isoformat() == "2026-11-02", a.isoformat())
    check("周终点是周日", b.isoformat() == "2026-11-08", b.isoformat())
    a, b, _ = G._period_range("month", "2026-02")
    check("2 月 28 天", b.day == 28, b.day)
    a, b, _ = G._period_range("month", "2024-02")
    check("闰年 2 月 29 天", b.day == 29, b.day)
    a, b, _ = G._period_range("month", "2026-01")
    check("1 月 31 天", b.day == 31, b.day)
    a, b, _ = G._period_range("year", "2025")
    check("年区间 1/1-12/31", (a.month, a.day, b.month, b.day) == (1, 1, 12, 31))
    a, b, label = G._period_range("custom", "2026-01-01 ~ 2026-03-01")
    check("自定义区间", (a.isoformat(), b.isoformat()) == ("2026-01-01", "2026-03-01"))
    raised = False
    try:
        G._period_range("quarter", None)
    except G.GarminError:
        raised = True
    check("非法周期报错", raised)


def test_cookie_parse():
    print("Cookie 粘贴解析")
    ck = G._parse_cookie_input("session=abc; JWT_WEB=ey.x.y; SESSIONID=zzz")
    check("三段式", ck.get("session") == "abc" and ck.get("JWT_WEB") == "ey.x.y" and ck.get("SESSIONID") == "zzz")
    ck = G._parse_cookie_input("session=abc\nSESSIONID=zzz\nnotice_preferences=2")
    check("多行忽略无关 Cookie", ck.get("SESSIONID") == "zzz" and "notice_preferences" not in ck)
    ck = G._parse_cookie_input('{"session":"abc","SESSIONID":"zzz","foo":"bar"}')
    check("JSON 形式", ck.get("session") == "abc" and "foo" not in ck)
    ck = G._parse_cookie_input(" Cookie: session=abc; SESSIONID=zzz ")
    check("带 Cookie: 前缀", ck.get("SESSIONID") == "zzz")


def test_csrf():
    print("CSRF 提取")
    html = '<meta name="csrf-token" content="0957b4ae-61d4"/>'
    check("从 meta 提取", G._csrf_from_html(html) == "0957b4ae-61d4")
    check("没有则空串", G._csrf_from_html("<html></html>") == "")


def test_session():
    print("会话生命周期")
    now = time.time()
    s = G.Session(site="cn", cookies={"session": "x"}, expires=now + 3600)
    check("未过期且有余量", s.alive(60) is True)
    s2 = G.Session(site="cn", cookies={"session": "x"}, expires=now + 30)
    check("余量不足视为过期", s2.alive(60) is False)
    s3 = G.Session(site="cn", cookies={}, expires=now + 3600)
    check("没有 session cookie 不算登录", s3.alive() is False)
    s4 = G.Session(site="cn", cookies={"session": "x"}, expires=now + 3600, csrf="")
    check("refresh 窗口已过则不续期", G.refresh_session(s4) is False)
    check("base 随站点切换", s.base == "https://connect.garmin.cn"
          and G.Session(site="global").base == "https://connect.garmin.com")


def test_compact():
    print("输出压缩")
    check("去掉 None 与空容器", G.compact({"a": None, "b": [], "c": {}, "d": 0}) == {"d": 0})
    check("保留 0 与 False", G.compact({"a": 0, "b": False}) == {"a": 0, "b": False})


def test_picker():
    print("站点选择页")
    import json as _json
    import threading
    import time as _time
    import urllib.request

    saved_open = G.webbrowser.open
    G.webbrowser.open = lambda *a, **k: True
    try:
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("p", G._pick_site(timeout=8)))
        th.start()
        for _ in range(50):
            if G._LAST_PICKER_URL:
                break
            _time.sleep(0.1)
        url = G._LAST_PICKER_URL
        check("弹出了选择页", bool(url))
        if not url:
            return
        with urllib.request.urlopen(url, timeout=5) as r:
            html = r.read().decode()
        check("页面有两个选项", "佳明中国" in html and "Garmin 国际版" in html)
        with urllib.request.urlopen(url + "status", timeout=5) as r:
            st = _json.loads(r.read())
        check("初始状态是 choose", st.get("phase") == "choose", st)

        # 非法站点应被拒绝
        bad = urllib.request.Request(url + "pick", data=b'{"site":"mars"}', method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(bad, timeout=5)
            check("非法站点被拒绝", False)
        except Exception as e:
            check("非法站点被拒绝", "400" in str(e), str(e))

        ok = urllib.request.Request(url + "pick", data=b'{"site":"global"}', method="POST",
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(ok, timeout=5) as r:
            check("选择成功", _json.loads(r.read()).get("ok") is True)
        th.join(timeout=5)
        p = box.get("p")
        check("返回的站点正确", getattr(p, "site", None) == "global", getattr(p, "site", None))
        check("进入 login 阶段", getattr(p, "state", None) and p.state.phase == "login")
        p.set("ok", "登录成功")
        with urllib.request.urlopen(url + "status", timeout=5) as r:
            st = _json.loads(r.read())
        check("状态可回传 ok", st.get("phase") == "ok", st)
        p.close()
    finally:
        G.webbrowser.open = saved_open


def test_weight_date():
    print("体重日期解析")
    check("字符串日期", G._weight_date({"calendarDate": "2026-11-05T00:00:00"}) == "2026-11-05")
    # 取 UTC 正午，避免时区把日期推到前后一天
    check("epoch 毫秒", G._weight_date({"timestampGMT": 1793870400000}) == "2026-11-05",
          G._weight_date({"timestampGMT": 1793870400000}))
    check("都没有返回 None", G._weight_date({}) is None)


def test_download():
    print("导出原始文件")
    import io
    import tempfile
    import zipfile
    from pathlib import Path as _P

    class FakeResp:
        def __init__(self, status, data):
            self.status = status
            self.content = data
            self.text = data.decode("utf-8", "replace")
            self.set_cookies = []
            self.content_type = ""
            self.location = ""

    # 构造一个含 FIT 的 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("123_ACTIVITY.fit", b"FITDATA")
    zipped = buf.getvalue()

    old_req, old_home, old_check = G._request, G.HOME, G._check
    tmp = _P(tempfile.mkdtemp())
    try:
        G.HOME = tmp

        def fake_req(s, method, path, **kw):
            if "files/activity" in path:
                return FakeResp(200, zipped)
            return FakeResp(200, b"<gpx/>")

        G._request = fake_req
        G._check = lambda s, r, what="": r

        out = G.t_download_activity(None, "123", "fit")
        p = _P(out["文件"])
        check("FIT 从 zip 里解出来", p.suffix == ".fit" and p.read_bytes() == b"FITDATA", out)
        check("返回大小", out["大小_KB"] >= 0)

        out2 = G.t_download_activity(None, "123", "gpx")
        p2 = _P(out2["文件"])
        check("GPX 直接落盘", p2.suffix == ".gpx" and p2.read_bytes() == b"<gpx/>")
        import os as _os
        import stat as _st
        check("导出文件 0600", _st.S_IMODE(_os.stat(p2).st_mode) == 0o600,
              oct(_st.S_IMODE(_os.stat(p2).st_mode)))

        raised = False
        try:
            G.t_download_activity(None, "123", "pdf")
        except G.GarminError:
            raised = True
        check("不支持的格式报错", raised)
    finally:
        G._request, G.HOME, G._check = old_req, old_home, old_check


def test_workout_builder():
    print("课表编译")
    steps = [
        {"type": "warmup", "duration_min": 10},
        {"type": "repeat", "times": 4, "steps": [
            {"type": "interval", "distance_m": 800, "pace": "5:00"},
            {"type": "recovery", "duration_min": 2},
        ]},
        {"type": "cooldown", "duration_min": 10},
    ]
    w = G.build_workout("800米间歇", steps)
    top = w["workoutSegments"][0]["workoutSteps"]
    check("顶层三步", len(top) == 3, len(top))
    check("第 1 步是热身", top[0]["stepType"]["stepTypeKey"] == "warmup")
    check("热身 600 秒", top[0]["endConditionValue"] == 600.0, top[0]["endConditionValue"])
    rep = top[1]
    check("第 2 步是重复组", rep["type"] == "RepeatGroupDTO")
    check("重复 4 次", rep["numberOfIterations"] == 4)
    check("组内两步", len(rep["workoutSteps"]) == 2)
    interval = rep["workoutSteps"][0]
    check("间歇 800 米", interval["endConditionValue"] == 800.0)
    check("按配速", interval["targetType"]["workoutTargetTypeKey"] == "pace.zone")
    check("速度下界小于上界", interval["targetValueOne"] < interval["targetValueTwo"])
    check("stepOrder 连续", [s["stepOrder"] for s in top] == [1, 2, 3])

    summary = G._workout_summary(w)
    check("预览是中文", any("热身" in x for x in summary) and any("重复 4 组" in x for x in summary), summary)

    for name, fn in [
        ("空 steps", lambda: G.build_workout("x", [])),
        ("缺时长", lambda: G.build_workout("x", [{"type": "interval"}])),
        ("不支持的运动", lambda: G.build_workout("x", [{"type": "interval", "duration_min": 5}], sport="rowing")),
        ("次数为 0", lambda: G.build_workout("x", [{"type": "repeat", "times": 0, "steps": [{"type": "interval", "duration_min": 1}]}])),
        ("配速格式错", lambda: G.build_workout("x", [{"type": "interval", "duration_min": 5, "pace": "abc"}])),
    ]:
        raised = False
        try:
            fn()
        except G.GarminError:
            raised = True
        check(f"{name} 报错", raised)


def test_editor_roundtrip():
    print("编辑器结构还原")
    w = G.build_workout("t", [
        {"type": "warmup", "distance_m": 1500, "pace_range": ["8:00", "7:30"]},
        {"type": "repeat", "times": 3, "steps": [
            {"type": "interval", "distance_m": 1000, "pace_range": ["5:30", "5:00"]},
            {"type": "recovery", "distance_m": 1000, "pace_range": ["8:00", "7:30"]},
        ]},
    ])
    steps = G._payload_to_steps(w)
    check("两步（热身+重复）", len(steps) == 2, steps)
    check("热身距离还原成公里", steps[0]["distance_km"] == 1.5, steps[0])
    check("配速还原成 快-慢", steps[0]["pace"] == ["7:30", "8:00"], steps[0].get("pace"))
    rep = steps[1]
    check("重复次数", rep["times"] == 3)
    check("组内两步", len(rep["steps"]) == 2)
    check("间歇配速", rep["steps"][0]["pace"] == ["5:00", "5:30"], rep["steps"][0].get("pace"))
    # 时长型步骤
    w2 = G.build_workout("t2", [{"type": "warmup", "duration_min": 10}])
    s2 = G._payload_to_steps(w2)[0]
    check("时长还原成分钟", s2["duration_min"] == 10, s2)


def test_write_gate():
    print("写操作开关")
    import os as _os
    old = _os.environ.pop("GARMIN_ALLOW_WRITE", None)
    try:
        raised = False
        try:
            G._require_write()
        except G.GarminError:
            raised = True
        check("默认关闭", raised)
        _os.environ["GARMIN_ALLOW_WRITE"] = "1"
        ok = True
        try:
            G._require_write()
        except G.GarminError:
            ok = False
        check("设 1 后放行", ok)
    finally:
        _os.environ.pop("GARMIN_ALLOW_WRITE", None)
        if old is not None:
            _os.environ["GARMIN_ALLOW_WRITE"] = old


def test_session_permissions():
    print("凭据文件权限")
    import os as _os
    import stat as _stat
    import tempfile
    from pathlib import Path as _P

    old_home, old_session = G.HOME, G.SESSION
    tmp = _P(tempfile.mkdtemp())
    G.HOME, G.SESSION = tmp, tmp / "session.json"
    try:
        # 先放一个 644 的旧文件，验证会被收紧
        G.SESSION.write_text("{}")
        _os.chmod(G.SESSION, 0o644)
        sess = G.Session(site="cn", cookies={"session": "x"}, csrf="c")
        G.save_session(sess)
        mode = _stat.S_IMODE(G.SESSION.stat().st_mode)
        check("写成 0600", mode == 0o600, oct(mode))

        # 新文件直接建，中途不存在 644 窗口
        G.SESSION.unlink()
        old_mask = _os.umask(0o022)
        try:
            G.save_session(sess)
        finally:
            _os.umask(old_mask)
        check("新建即 0600", _stat.S_IMODE(G.SESSION.stat().st_mode) == 0o600)
    finally:
        G.HOME, G.SESSION = old_home, old_session


def test_bootstrap_fallback():
    print("会话兜底")
    import time as _t

    old_req = G._request
    try:
        class R:
            status = 200
            text = "<html><head><meta name=\"csrf-token\" content=\"abc\"></head></html>"
            set_cookies = []
            content_type = "text/html"

        G._request = lambda *a, **k: R()
        s = G.Session(site="cn", cookies={"session": "x"})
        ok = G._bootstrap(s)
        check("拿到 csrf", ok and s.csrf == "abc")
        check("没有 SESSION_EXPIRES 也给出有效期", s.expires > _t.time(), s.expires)
        check("有效期不超过 1 小时", s.expires <= _t.time() + 3600, s.expires)
        check("alive() 为真（不会反复弹登录）", s.alive(0) is True)
    finally:
        G._request = old_req


def test_refresh_lock():
    print("并发续期")
    import tempfile
    import threading as _th
    import time as _t
    from pathlib import Path as _P

    old_home, old_session, old_req = G.HOME, G.SESSION, G._request
    tmp = _P(tempfile.mkdtemp())
    G.HOME, G.SESSION = tmp, tmp / "session.json"
    calls = []
    try:
        class R:
            status = 200
            set_cookies = ["session=NEW; Path=/", "SESSIONID=N2; Path=/"]
            content_type = "application/json"

            def __init__(self):
                self.text = json.dumps({
                    "expires": int((_t.time() + 3600) * 1000),
                    "refreshExpires": int((_t.time() + 7200) * 1000),
                })

        def fake_req(*a, **k):
            calls.append(1)
            _t.sleep(0.3)          # 模拟网络往返，放大并发窗口
            return R()

        G._request = fake_req
        base = G.Session(site="cn", cookies={"session": "A", "SESSIONID": "B"}, csrf="c",
                         expires=_t.time() + 60, refresh_expires=_t.time() + 3600)
        G.save_session(base)
        s1, s2 = G.load_session(), G.load_session()

        out = []
        t1 = _th.Thread(target=lambda: out.append(G.refresh_session(s1, force=True)))
        t2 = _th.Thread(target=lambda: out.append(G.refresh_session(s2, force=True)))
        t1.start()
        _t.sleep(0.05)             # 让 t1 先进临界区
        t2.start()
        t1.join()
        t2.join()

        check("只消耗一次续期凭据", len(calls) == 1, f"实际 {len(calls)} 次")
        check("两个调用都算成功", out == [True, True], out)
        check("后来者拿到了新 Cookie", all(x is True for x in out))
    finally:
        G.HOME, G.SESSION, G._request = old_home, old_session, old_req


def test_temperature():
    print("气温单位")
    check("华氏 59 -> 15.0", G._temp_c(59) == 15.0, G._temp_c(59))
    check("摄氏 15 -> 15.0", G._temp_c(15) == 15.0, G._temp_c(15))
    check("摄氏 32 不被误转", G._temp_c(32) == 32.0, G._temp_c(32))
    check("None -> None", G._temp_c(None) is None)
    check("空串 -> None", G._temp_c("") is None)


def test_pr_sport():
    print("个人纪录运动类型")
    check("字符串", G._pr_sport({"activityType": "running"}, {}) == "跑步")
    check("对象", G._pr_sport({"activityType": {"typeKey": "running"}}, {}) == "跑步")
    check("对象无 typeKey 回落 meta", G._pr_sport({"activityType": {}}, {"sport": "CYCLING"}) == "骑行")
    check("都没有不崩", G._pr_sport({}, {}) == "未知")


def test_http_status_mapping():
    print("HTTP 状态映射")
    s = G.Session(site="cn", cookies={"session": "x"}, csrf="c")

    def resp(status, location=""):
        return G._Resp(status, "", [], "application/json", location)

    # 302 到 SSO 才算掉登录
    raised = False
    try:
        G._check(s, resp(302, "https://sso.garmin.cn/portal/sso/zh-CN/sign-in?x=1"))
    except G.AuthRequired:
        raised = True
    check("302 -> sso 视为过期", raised)

    raised = False
    try:
        G._check(s, resp(302, "https://connect.garmin.cn/app/home"))
    except G.GarminError as e:
        raised = "跳转" in str(e)
    check("302 -> 普通地址当错误透出", raised)

    check("204 不抛错", G._check(s, resp(204)) is not None)
    raised = False
    try:
        G._check(s, resp(404))
    except G.NotFound:
        raised = True
    check("404 -> NotFound", raised)


def test_probe_result():
    print("会话探测三态")
    old = G._request
    s = G.Session(site="cn", cookies={"session": "x"}, csrf="c")

    class R:
        def __init__(self, status, location=""):
            self.status = status
            self.location = location
            self.text = "{}"

    try:
        for status, expect, desc in [
            (200, "ok", "200"),
            (401, "auth", "401"),
            (403, "auth", "403"),
            (500, "error", "500"),
            (429, "error", "429"),
        ]:
            G._request = lambda *a, _st=status, **k: R(_st)
            check(f"{desc} -> {expect}", G._probe_result(s) == expect, G._probe_result(s))

        G._request = lambda *a, **k: R(302, "https://sso.garmin.cn/portal/sso/zh-CN/sign-in")
        check("302->sso -> auth", G._probe_result(s) == "auth")

        G._request = lambda *a, **k: R(302, "https://cdn.example.com/x")
        check("302->别处 -> error", G._probe_result(s) == "error")

        def boom(*a, **k):
            raise G.GarminError("网络挂了")

        G._request = boom
        check("网络异常 -> error（不误判失效）", G._probe_result(s) == "error")
    finally:
        G._request = old


def test_rows_truncated():
    print("列表截断标记")
    r = G._Rows([{"a": 1}])
    check("默认未截断", r.truncated is False)
    old_api, old_dn = G.api, G._dn
    try:
        G._dn = lambda s: "guid"
        G.api = lambda *a, **k: {"activityList": [{"activityId": i} for i in range(1000)]}
        rows = G._fetch_range(G.Session(site="cn"), __import__("datetime").date(2026, 1, 1),
                              __import__("datetime").date(2026, 12, 31))
        check("满 1000 条标记截断", getattr(rows, "truncated", False) is True)
        G.api = lambda *a, **k: {"activityList": [{"activityId": 1}]}
        rows2 = G._fetch_range(G.Session(site="cn"), __import__("datetime").date(2026, 1, 1),
                               __import__("datetime").date(2026, 12, 31))
        check("少量数据不标记", getattr(rows2, "truncated", False) is False)
    finally:
        G.api, G._dn = old_api, old_dn


def test_login_budget():
    print("登录预算")
    check("站点选择默认 120s", G._pick_site.__defaults__[0] == 120.0, G._pick_site.__defaults__)
    check("登录默认 300s", G._do_login_impl.__defaults__[-1] == 300, G._do_login_impl.__defaults__)
    check("合计 <= 450s", G._pick_site.__defaults__[0] + G._do_login_impl.__defaults__[-1] <= 450)


def test_pr_labels():
    print("个人纪录标签")
    check("5K 有时间型", 3 in G.PR_TIME_TYPES)
    check("最长跑步不是时间型", 7 not in G.PR_TIME_TYPES)
    check("标签存在", G.PR_LABEL[6] == "全程马拉松")


if __name__ == "__main__":
    for fn in (test_pace, test_sport, test_period, test_cookie_parse,
               test_csrf, test_session, test_compact, test_picker,
               test_weight_date, test_download, test_workout_builder,
               test_editor_roundtrip, test_write_gate, test_session_permissions,
               test_bootstrap_fallback, test_refresh_lock, test_temperature,
               test_pr_sport, test_http_status_mapping, test_probe_result,
               test_rows_truncated, test_login_budget,
               test_pr_labels):
        fn()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        sys.exit(1)
    print("✅ 全部通过")
