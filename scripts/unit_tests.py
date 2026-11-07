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
    import zipfile
    import tempfile
    from pathlib import Path as _P

    class FakeResp:
        def __init__(self, status, data):
            self.status = status
            self.text = data
            self.set_cookies = []
            self.content_type = ""

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

        raised = False
        try:
            G.t_download_activity(None, "123", "pdf")
        except G.GarminError:
            raised = True
        check("不支持的格式报错", raised)
    finally:
        G._request, G.HOME, G._check = old_req, old_home, old_check


def test_pr_labels():
    print("个人纪录标签")
    check("5K 有时间型", 3 in G.PR_TIME_TYPES)
    check("最长跑步不是时间型", 7 not in G.PR_TIME_TYPES)
    check("标签存在", G.PR_LABEL[6] == "全程马拉松")


if __name__ == "__main__":
    for fn in (test_pace, test_sport, test_period, test_cookie_parse,
               test_csrf, test_session, test_compact, test_picker,
               test_weight_date, test_download, test_pr_labels):
        fn()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        sys.exit(1)
    print("✅ 全部通过")
