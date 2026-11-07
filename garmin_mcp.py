#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Garmin Connect MCP Server —— 让 AI 直接读取你的佳明训练数据。

用法:
    python3 garmin_mcp.py login                 # 弹出浏览器窗口，登录一次
    python3 garmin_mcp.py login --site=global   # 登录国际版（connect.garmin.com）
    python3 garmin_mcp.py login --manual        # 手动粘贴 Cookie（无 Chrome 时兜底）
    python3 garmin_mcp.py status                # 查看登录状态
    python3 garmin_mcp.py logout                # 退出并清除本地凭据
    python3 garmin_mcp.py call <tool> [--args '{"k":"v"}']
    python3 garmin_mcp.py                       # 以 MCP stdio server 模式运行（默认）

设计要点:
  * 站点：国区 connect.garmin.cn / 国际版 connect.garmin.com，**接口路径完全相同**
    （两站部署同一份前端，/gc-api/<service>/<path>），只有域名与 SSO 不同
  * 认证：三个 HttpOnly Cookie（session / JWT_WEB / SESSIONID）
    + 请求头 Connect-Csrf-Token（页面 <meta name="csrf-token">）
    + Sec-Fetch-Site: same-origin（缺这个 WAF 直接 403）
  * Cookie 是 HttpOnly，document.cookie 读不到 —— 所以登录窗口用 CDP 的
    Storage.getCookies 取，这是与 RQrun/悦跑圈 最大的实现差异
  * 会话约 2 小时，但可用 POST /modern/di-oauth/refreshToken 静默续期
    （续期会轮换 session/SESSIONID，且**只能用一次**，必须立刻落盘）
  * 只封装只读接口；上传 FIT、删除活动、发评论等写操作一律不暴露
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
SITES = {
    "cn": {
        "base": "https://connect.garmin.cn",
        "sso": "https://sso.garmin.cn",
        "locale": "zh-CN",
        "label": "佳明中国",
        "cookie_domains": ("connect.garmin.cn", ".garmin.cn"),
    },
    "global": {
        "base": "https://connect.garmin.com",
        "sso": "https://sso.garmin.com",
        "locale": "en-US",
        "label": "Garmin 国际版",
        "cookie_domains": ("connect.garmin.com", ".garmin.com"),
    },
}
DEFAULT_SITE = os.environ.get("GARMIN_SITE", "cn").strip().lower()

HOME = Path(os.environ.get("GARMIN_MCP_HOME", Path.home() / ".garmin-mcp"))
SESSION = HOME / "session.json"
PROFILE_PREFIX = "chrome-profile"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 需要持久化的 Cookie（其余如 notice_* 是 Cookie 同意横幅，没用）
KEEP_COOKIES = ("session", "JWT_WEB", "SESSIONID", "GARMIN-SSO",
                "GARMIN-SSO-CUST-GUID", "TAsessionID")

SPORT_PARENT = {
    "running": 1, "cycling": 2, "hiking": 3, "walking": 9,
    "swimming": 26, "strength_training": 13, "multi_sport": 89,
}
SPORT_ALIAS = {
    "run": "running", "跑步": "running",
    "ride": "cycling", "bike": "cycling", "骑行": "cycling",
    "swim": "swimming", "游泳": "swimming",
    "walk": "walking", "走": "walking", "徒步": "hiking", "hike": "hiking",
    "strength": "strength_training", "力量": "strength_training",
    "all": "all", "全部": "all", "": "all", "其它": "all", "other": "all",
}
ACTIVITY_TYPE_CN = {
    "running": "跑步", "trail_running": "越野跑", "street_running": "路跑",
    "track_running": "操场跑", "treadmill_running": "跑步机", "indoor_running": "室内跑",
    "virtual_run": "虚拟跑", "obstacle_run": "障碍赛", "ultra_run": "超马",
    "cycling": "骑行", "indoor_cycling": "室内骑行", "road_biking": "公路骑行",
    "mountain_biking": "山地骑行", "walking": "步行", "casual_walking": "散步",
    "speed_walking": "快走", "hiking": "徒步", "swimming": "游泳",
    "lap_swimming": "泳池游泳", "open_water_swimming": "公开水域游泳",
    "strength_training": "力量训练", "yoga": "瑜伽", "pilates": "普拉提",
    "elliptical": "椭圆机", "stair_climbing": "爬楼", "breathwork": "呼吸训练",
    "multi_sport": "多项运动", "other": "其它",
}


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------
class AuthRequired(RuntimeError):
    def __init__(self, msg="尚未登录佳明，或登录已过期"):
        super().__init__(
            f"{msg}。请在终端运行:  python3 garmin_mcp.py login"
            "（或直接对我说「登录佳明」，我会打开登录窗口）")


class GarminError(RuntimeError):
    pass


class NotFound(Exception):
    """接口不存在 / 该功能在所在站点未开放（404/405/503）。"""


# --------------------------------------------------------------------------
# 会话存储
# --------------------------------------------------------------------------
@dataclass
class Session:
    site: str
    cookies: dict = field(default_factory=dict)
    csrf: str = ""
    saved_at: str = ""
    expires: float = 0.0          # 会话硬过期（epoch 秒）
    refresh_expires: float = 0.0  # 续期窗口截止（epoch 秒）
    display_name: str = ""        # 用户 GUID（路径参数）

    @property
    def base(self) -> str:
        return SITES[self.site]["base"]

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v)

    def alive(self, skew: float = 60) -> bool:
        return bool(self.cookies.get("session")) and self.expires > time.time() + skew


def load_session() -> Session | None:
    try:
        d = json.loads(SESSION.read_text(encoding="utf-8"))
        site = d.get("site") if d.get("site") in SITES else DEFAULT_SITE
        s = Session(
            site=site,
            cookies=d.get("cookies") or {},
            csrf=d.get("csrf") or "",
            saved_at=d.get("saved_at") or "",
            expires=float(d.get("expires") or 0),
            refresh_expires=float(d.get("refresh_expires") or 0),
            display_name=d.get("display_name") or "",
        )
        return s if s.cookies.get("session") else None
    except Exception:
        return None


def save_session(s: Session) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    s.saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SESSION.write_text(json.dumps({
        "site": s.site,
        "cookies": {k: v for k, v in s.cookies.items() if v},
        "csrf": s.csrf,
        "saved_at": s.saved_at,
        "expires": s.expires,
        "refresh_expires": s.refresh_expires,
        "display_name": s.display_name,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(SESSION, 0o600)
    except OSError:
        pass


def clear_session() -> None:
    try:
        SESSION.unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _headers(s: Session, extra: dict | None = None) -> dict:
    h = {
        "Cookie": s.cookie_header(),
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        # WAF 硬条件：缺了它，即使 Cookie+CSRF 齐全也 403
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if s.csrf:
        h["Connect-Csrf-Token"] = s.csrf
    if extra:
        h.update(extra)
    return h


class _Resp:
    __slots__ = ("status", "text", "set_cookies", "content_type")

    def __init__(self, status: int, text: str, set_cookies: list[str], content_type: str):
        self.status = status
        self.text = text
        self.set_cookies = set_cookies
        self.content_type = content_type

    def json(self):
        try:
            return json.loads(self.text)
        except Exception:
            raise GarminError(f"返回不是 JSON（HTTP {self.status}）：{self.text[:160]}")


def _request(s: Session, method: str, path: str, *, params: dict | None = None,
             body: bytes | None = None, headers: dict | None = None,
             timeout: int = 30, raw: bool = False) -> _Resp:
    url = path if path.startswith("http") else s.base + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    req = urllib.request.Request(url, data=body, method=method, headers=_headers(s, headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            text = data.decode("utf-8", "replace") if not raw else data
            return _Resp(r.status, text if not raw else data,
                         r.headers.get_all("Set-Cookie") or [],
                         r.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as e:
        data = e.read()
        text = data.decode("utf-8", "replace") if not raw else data
        return _Resp(e.code, text if not raw else data,
                     e.headers.get_all("Set-Cookie") or [],
                     e.headers.get("Content-Type", ""))
    except urllib.error.URLError as e:
        raise GarminError(f"网络请求失败：{e.reason}") from e


def _check(s: Session, r: _Resp, what: str = "") -> _Resp:
    """统一的状态码 → 语义映射（204/404/503 视为「无数据」，不抛错）。"""
    if r.status in (200, 201):
        return r
    if r.status == 204:
        return r
    if r.status in (401, 302):
        raise AuthRequired("登录已过期")
    if r.status == 403:
        raise GarminError(
            "被拒绝（403）。多半是会话过期或 CSRF 失效，请重新运行 login"
            + (f"（{what}）" if what else ""))
    if r.status in (404, 405, 503):
        raise NotFound(f"{what or '接口'} 在 {s.site} 不可用（HTTP {r.status}）")
    if r.status == 429:
        raise GarminError("请求过于频繁（429），稍后再试")
    raise GarminError(f"{what or '接口'} 返回 HTTP {r.status}：{r.text[:160]}")


def api(s: Session, path: str, **params):
    """GET 一个 /gc-api 接口并返回解析后的 JSON；无数据返回 None。"""
    r = _check(s, _request(s, "GET", path, params=params), path)
    if r.status == 204 or not r.text.strip():
        return None
    return r.json()


def api_soft(s: Session, path: str, **params):
    """同 api()，但接口不存在/未开放时返回 None 而不是抛错（用于可选数据）。"""
    try:
        return api(s, path, **params)
    except NotFound:
        return None


# --------------------------------------------------------------------------
# 登录：独立 Chrome 窗口 + CDP 读 HttpOnly Cookie
# --------------------------------------------------------------------------
def _chrome_path() -> str:
    cands = []
    if sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif sys.platform.startswith("win"):
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                cands += [str(Path(root) / "Google/Chrome/Application/chrome.exe"),
                          str(Path(root) / "Chromium/Application/chrome.exe")]
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    else:
        cands += ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                  "/usr/bin/chromium", "/usr/bin/chromium-browser",
                  "/snap/bin/chromium", "/opt/google/chrome/chrome"]
    for c in cands:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "chromium", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise GarminError(
        "未找到 Google Chrome。请先安装 Chrome（https://www.google.com/chrome/），"
        "或改用：python3 garmin_mcp.py login --manual")


def _profile(site: str) -> Path:
    return HOME / f"{PROFILE_PREFIX}-{site}"


def _read_devtools_port(profile: Path, timeout: float = 25.0) -> tuple[int, str]:
    f = profile / "DevToolsActivePort"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            lines = f.read_text().splitlines()
            if len(lines) >= 2:
                return int(lines[0]), lines[1].strip()
        except Exception:
            pass
        time.sleep(0.2)
    raise GarminError("Chrome 调试端口未就绪（可能已有同名用户目录的 Chrome 在运行）")


class CDP:
    """极简 CDP 客户端：只用 websockets 同步客户端，够登录场景使用。"""

    def __init__(self, ws_url: str):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=20)
        self._id = 0
        self.session_id = None
        self._target_id = None

    def send(self, method: str, params: dict | None = None, sid: str | None = None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if sid or self.session_id:
            msg["sessionId"] = sid or self.session_id
        self.ws.send(json.dumps(msg))
        while True:
            raw = self.ws.recv(timeout=25)
            data = json.loads(raw)
            if data.get("id") == mid:
                if "error" in data:
                    raise GarminError(f"CDP {method} 失败: {data['error']}")
                return data.get("result", {})

    def browser_send(self, method: str, params: dict | None = None):
        """不带 sessionId 的浏览器级调用（Storage.getCookies 属于这一类）。"""
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self.ws.recv(timeout=25))
            if data.get("id") == mid:
                if "error" in data:
                    raise GarminError(f"CDP {method} 失败: {data['error']}")
                return data.get("result", {})

    def attach_first_page(self):
        targets = self.send("Target.getTargets")["targetInfos"]
        pages = [t for t in targets if t["type"] == "page"]
        self._target_id = (pages[0]["targetId"] if pages
                           else self.send("Target.createTarget", {"url": "about:blank"})["targetId"])
        self.session_id = self.send("Target.attachToTarget",
                                    {"targetId": self._target_id, "flatten": True})["sessionId"]
        return self.session_id

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    def cookies(self) -> dict:
        raw = self.browser_send("Storage.getCookies").get("cookies") or []
        out = {}
        for c in raw:
            if c.get("name") in KEEP_COOKIES:
                # 同名 Cookie 可能在多个域下出现，connect.* 的优先（覆盖 .garmin.cn）
                out[c["name"]] = c.get("value", "")
        return out

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _csrf_from_html(html: str) -> str:
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return m.group(1) if m else ""


def _probe(s: Session) -> bool:
    """用当前 Cookie 打一个轻量接口，判断会话是否真的可用。"""
    try:
        r = _request(s, "GET", "/gc-api/userprofile-service/userprofile/user-settings/")
        return r.status == 200
    except Exception:
        return False


def _bootstrap(s: Session) -> bool:
    """补齐 csrf + 过期时间：GET /app/home 拿 meta 与 SESSION_EXPIRES。"""
    try:
        r = _request(s, "GET", "/app/home")
    except Exception:
        return False
    if r.status != 200:
        return False
    s.csrf = _csrf_from_html(r.text) or s.csrf
    m = re.search(r'window\.SESSION_EXPIRES\s*=\s*(\{[^}]+\})', r.text)
    if m:
        try:
            d = json.loads(m.group(1))
            s.expires = float(d.get("expires") or 0) / 1000.0
            s.refresh_expires = float(d.get("refreshExpires") or 0) / 1000.0
        except Exception:
            pass
    return bool(s.csrf)


def refresh_session(s: Session, force: bool = False) -> bool:
    """静默续期。返回 True=续期成功。

    端点：POST /modern/di-oauth/refreshToken（无 body，必须带 CSRF 头）
    返回：{"expires":…,"refreshExpires":…} + Set-Cookie 轮换 session/SESSIONID
    ⚠️ 续期凭据一次性：成功后必须立刻 save_session()，旧的不能再用。
    """
    if not s.cookies.get("session") or not s.csrf:
        return False
    if not force and s.expires - time.time() > 45 * 60:
        return False
    if s.refresh_expires and s.refresh_expires < time.time():
        return False  # 宽限期已过，只能重新登录
    try:
        r = _request(s, "POST", "/modern/di-oauth/refreshToken",
                     headers={"Content-Type": "application/json"})
    except Exception:
        return False
    if r.status != 200:
        return False
    got = False
    for sc in r.set_cookies:
        name, _, rest = sc.partition("=")
        if name in ("session", "SESSIONID") and rest:
            s.cookies[name] = rest.split(";")[0]
            got = True
    try:
        d = json.loads(r.text)
        s.expires = float(d.get("expires") or 0) / 1000.0
        s.refresh_expires = float(d.get("refreshExpires") or 0) / 1000.0
    except Exception:
        pass
    if got:
        save_session(s)
    return got



# --------------------------------------------------------------------------
# 站点选择（弹一个本地小页面，让用户选国区 / 国际版）
# --------------------------------------------------------------------------
PICKER_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>选择你的佳明站点</title>
<style>
:root{--bg:#fbfbfd;--fg:#1d1d1f;--mute:#6e6e73;--line:#e3e3e8;--accent:#007cc3}
@media (prefers-color-scheme:dark){:root{--bg:#111114;--fg:#e8e8ed;--mute:#9a9aa2;--line:#2a2a30;--accent:#4aa3d8}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",system-ui,sans-serif}
.card{width:min(680px,92vw);padding:38px 40px 34px;border:1px solid var(--line);border-radius:18px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.01em}
p.sub{margin:0 0 26px;color:var(--mute);font-size:14px}
.opts{display:grid;grid-template-columns:1fr 1fr;gap:16px}
button{display:block;width:100%;text-align:left;padding:20px 22px;border-radius:14px;cursor:pointer;
 border:1.5px solid var(--line);background:transparent;color:var(--fg);font:inherit;transition:.15s}
button:hover{border-color:var(--accent);transform:translateY(-1px)}
button b{display:block;font-size:17px;margin-bottom:4px}
button span{color:var(--mute);font-size:13px}
.status{margin-top:22px;font-size:14px;color:var(--mute);min-height:22px}
.status.ok{color:#1f9d3d}.status.err{color:#d03e3e}
.hide{display:none}
</style></head><body>
<div class="card">
  <h1>你的佳明账号在哪个站点？</h1>
  <p class="sub">两个站点的数据互不相通，选错了会看不到数据。登录后会记住这个选择。</p>
  <div class="opts" id="opts">
    <button onclick="pick('cn')"><b>佳明中国</b><span>connect.garmin.cn<br>国区账号（+86 手机号）</span></button>
    <button onclick="pick('global')"><b>Garmin 国际版</b><span>connect.garmin.com<br>国际账号（邮箱 / 非 +86）</span></button>
  </div>
  <div class="status" id="st">点上面任意一个，随后会弹出一个登录窗口</div>
</div>
<script>
let site = null;
async function pick(s){
  site = s;
  document.getElementById('opts').classList.add('hide');
  const st = document.getElementById('st');
  st.className = 'status';
  st.textContent = '正在打开登录窗口…';
  try { await fetch('/pick', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site:s})}); }
  catch(e) { st.className='status err'; st.textContent='本页面与工具断开了，请回终端重试'; return; }
  poll();
}
async function poll(){
  const st = document.getElementById('st');
  try {
    const r = await fetch('/status'); const d = await r.json();
    if (d.phase === 'login') { st.textContent = '已打开登录窗口，请在新窗口里完成登录（勾上 Remember Me 更稳）…'; }
    else if (d.phase === 'ok') { st.className='status ok'; st.textContent = '✅ 登录成功，可以关闭这个页面了'; return; }
    else if (d.phase === 'error') { st.className='status err'; st.textContent = '❌ ' + (d.message||'登录未完成，请回终端重试'); return; }
  } catch(e) {}
  setTimeout(poll, 1200);
}
</script></body></html>"""


_LAST_PICKER_URL = ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _PickState:
    def __init__(self):
        self.site = None
        self.phase = "choose"      # choose -> login -> ok / error
        self.message = ""
        self.done = threading.Event()


@dataclass
class _Picker:
    site: str
    state: _PickState
    httpd: ThreadingHTTPServer

    def set(self, phase: str, message: str = ""):
        self.state.phase = phase
        self.state.message = message

    def close(self, delay: float = 0.0):
        def _stop():
            try:
                if delay:
                    time.sleep(delay)
                self.httpd.shutdown()
            except Exception:
                pass
        threading.Thread(target=_stop, daemon=True).start()


def _make_picker_handler(state: _PickState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # 静默访问日志，别污染 stdio
            pass

        def _send(self, code: int, ctype: str, body: str):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass

        def do_GET(self):
            if self.path.startswith("/status"):
                self._send(200, "application/json", json.dumps(
                    {"phase": state.phase, "site": state.site, "message": state.message},
                    ensure_ascii=False))
            else:
                self._send(200, "text/html; charset=utf-8", PICKER_HTML)

        def do_POST(self):
            if not self.path.startswith("/pick"):
                self._send(404, "text/plain", "not found")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                payload = {}
            site = str(payload.get("site") or "").lower()
            if site not in SITES:
                self._send(400, "application/json", '{"ok":false}')
                return
            state.site = site
            state.phase = "login"
            self._send(200, "application/json", '{"ok":true}')
            state.done.set()
    return Handler


def _pick_site(timeout: float = 180.0) -> _Picker | None:
    """弹本地页面让用户选国区 / 国际版；超时或起不来返回 None。"""
    state = _PickState()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", _free_port()), _make_picker_handler(state))
    except OSError:
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}/"
    global _LAST_PICKER_URL
    _LAST_PICKER_URL = url
    print(f"🌐 已打开「选择站点」页面：{url}", file=sys.stderr)
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception:
        print(f"   浏览器没有自动打开，请手动访问上面的地址", file=sys.stderr)
    if not state.done.wait(timeout) or not state.site:
        httpd.shutdown()
        return None
    return _Picker(site=state.site, state=state, httpd=httpd)


def _prompt_site_cli() -> str:
    """没有图形界面时的兜底：终端里问一下。"""
    print("\n请选择你的佳明站点：", file=sys.stderr)
    print("  1) 佳明中国   connect.garmin.cn（默认）", file=sys.stderr)
    print("  2) Garmin 国际版  connect.garmin.com", file=sys.stderr)
    try:
        ans = input("输入 1 或 2（直接回车 = 1）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return DEFAULT_SITE
    return "global" if ans == "2" else DEFAULT_SITE


def do_login(site: str | None = None, timeout_sec: int = 300) -> int:
    """登录入口。site=None 时先弹「选择站点」页面（国区 / 国际版），再打开对应登录页。"""
    picker = None
    if not site:
        picker = _pick_site()
        if picker is None:
            site = _prompt_site_cli()
        else:
            site = picker.site
    site = site if site in SITES else DEFAULT_SITE
    if picker:
        picker.set("login", f"已选择 {SITES[site]['label']}")
    try:
        rc = _do_login_impl(site, timeout_sec)
        if picker:
            picker.set("ok" if rc == 0 else "error",
                       "登录成功" if rc == 0 else "登录未完成")
            picker.close(delay=8)
        return rc
    except Exception as e:
        if picker:
            picker.set("error", str(e)[:140])
            picker.close(delay=8)
        raise


def _do_login_impl(site: str, timeout_sec: int = 300) -> int:
    """拉起独立 Chrome 窗口，等用户登录完成后自动保存 Cookie。"""
    site = site if site in SITES else DEFAULT_SITE
    cfg = SITES[site]
    chrome = _chrome_path()
    HOME.mkdir(parents=True, exist_ok=True)
    profile = _profile(site)
    profile.mkdir(parents=True, exist_ok=True)

    # 已有有效会话则直接返回（同站点）
    old = load_session()
    if old and old.site == site and _probe(old):
        print(f"✅ 已登录 {cfg['label']}（本地凭据有效，保存于 {old.saved_at}）")
        print("   如需切换账号: python3 garmin_mcp.py logout 后再 login")
        return 0

    try:
        (profile / "DevToolsActivePort").unlink()
    except FileNotFoundError:
        pass

    args = [
        chrome,
        f"--user-data-dir={profile}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate,MediaRouter",
        f"--lang={cfg['locale']}",
        "--window-size=1020,820",
    ]
    # 高级用法：某些环境（CI / 容器 / root）需要额外参数，如 --no-sandbox
    extra = os.environ.get("GARMIN_CHROME_FLAGS", "").split()
    args += extra + ["about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        port, path = _read_devtools_port(profile)
        cdp = CDP(f"ws://127.0.0.1:{port}{path}")
        cdp.attach_first_page()
        # 直接落到对应语言的新版 SSO 门户（否则会按浏览器语言猜，中文用户会看到英文页）
        signin = (f"{cfg['sso']}/portal/sso/{cfg['locale']}/sign-in?clientId=GarminConnect"
                  f"&service={urllib.parse.quote(cfg['base'] + '/app/home', safe='')}")
        try:
            cdp.send("Page.enable")
            cdp.send("Page.navigate", {"url": signin})
        except Exception as e:
            raise GarminError(f"打开登录页失败：{e}")

        print(f"🌐 已打开 {cfg['label']} 登录窗口，请在窗口里完成登录（邮箱/手机号 + 密码 + 验证码）")
        print("   登录成功后窗口会自动关闭，无需手动操作…")

        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            if proc.poll() is not None:
                raise GarminError("登录窗口被关闭了，请重新运行 login")
            try:
                ck = cdp.cookies()
            except Exception:
                ck = {}
            if ck.get("session") and ck.get("SESSIONID"):
                s = Session(site=site, cookies=ck)
                if _bootstrap(s) and _probe(s):
                    refresh_session(s, force=True)  # 拿一份最长的有效期
                    save_session(s)
                    exp = datetime.fromtimestamp(s.expires).strftime("%Y-%m-%d %H:%M") if s.expires else "未知"
                    print(f"✅ 登录成功，凭据已保存（仅存本机，权限 600，本次有效至 {exp}，会自动续期）")
                    return 0
            time.sleep(1.0)
        raise GarminError(f"等待登录超时（{timeout_sec} 秒）")
    finally:
        if cdp:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _parse_cookie_input(raw: str) -> dict:
    """把用户粘贴的内容解析成 Cookie 字典。

    支持三种粘贴形式：
      1. `session=…; JWT_WEB=…; SESSIONID=…`
      2. 从 DevTools 复制的多行（每行 `name<TAB>value` 或 `name=value`）
      3. 浏览器插件导出的 JSON（{"session": "…", …}）
    """
    raw = raw.strip()
    out: dict = {}
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                for k, v in d.items():
                    if k in KEEP_COOKIES and isinstance(v, str):
                        out[k] = v
                return out
        except Exception:
            pass
    for part in re.split(r"[;\n]+", raw):
        part = part.strip().strip(",").strip('"')
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k in KEEP_COOKIES and v.strip():
            out[k] = v.strip()
    return out


def do_login_manual(site: str = DEFAULT_SITE) -> int:
    """无 Chrome / 自动登录失败时的兜底：手动粘贴 Cookie。"""
    site = site if site in SITES else DEFAULT_SITE
    cfg = SITES[site]
    print(f"手动登录（{cfg['label']}）- 需要 3 个 Cookie：session / JWT_WEB / SESSIONID")
    print(f"  1. 用浏览器打开 {cfg['base']} 并登录")
    print("  2. 打开开发者工具 → Application(应用) → Cookies → 选中该站点")
    print("  3. 把这 3 个 Cookie 逐行复制粘贴（name 和 value 用 = 或空格隔开都行）")
    print("     （Chrome 里 Cookie 有 HttpOnly 标记，document.cookie 是读不到的，必须在这里复制）")
    print("")
    try:
        raw = input("粘贴 Cookie（直接回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return 1
    if not raw:
        print("已取消")
        return 1
    ck = _parse_cookie_input(raw)
    if not ck.get("session"):
        print("❌ 没能解析出 session，请确认复制完整")
        return 2
    s = Session(site=site, cookies=ck)
    if not _bootstrap(s) or not _probe(s):
        print("❌ 这组 Cookie 校验没通过，可能复制不完整或已过期")
        return 2
    refresh_session(s, force=True)
    save_session(s)
    exp = datetime.fromtimestamp(s.expires).strftime("%Y-%m-%d %H:%M") if s.expires else "未知"
    print(f"✅ 登录成功，凭据已保存（本次有效至 {exp}，会自动续期）")
    return 0


# --------------------------------------------------------------------------
# 会话入口：每次工具调用前调用它（含自动续期 / 自动弹窗登录）
# --------------------------------------------------------------------------
def ensure_session(interactive: bool = True) -> Session:
    """每次工具调用前调用：确保有可用会话。

    顺序：读本地会话 → 剩余不足 45 分钟就先静默续期 → 仍不可用则（可选）弹窗登录。
    """
    s = load_session()
    if s is None:
        if not interactive:
            raise AuthRequired()
        print("未检测到登录凭据，正在打开站点选择…", file=sys.stderr)
        do_login(None)
        s = load_session()
        if s is None:
            raise AuthRequired()

    now = time.time()
    # 快到期时先续期；失败不抛（此时会话本身可能还没过期，继续用）
    if (s.cookies.get("session") and s.expires < now + 45 * 60
            and (not s.refresh_expires or s.refresh_expires > now)):
        refresh_session(s, force=False)

    if s.alive(5) and s.csrf:
        return s

    # 会话还有效但缺 csrf（例如从别的工具导入的凭据）
    if s.alive(0):
        _bootstrap(s)
        if s.csrf and _probe(s):
            save_session(s)
            return s

    # 真的过期了：重新走一次登录（专用 profile 里 SSO Cookie 通常还在，点一下即可）
    if interactive:
        print("登录已过期，正在重新打开登录窗口…", file=sys.stderr)
        do_login(s.site)
        s2 = load_session()
        if s2:
            return s2
    raise AuthRequired()


# ==========================================================================
# 通用小工具
# ==========================================================================
def _today() -> date:
    return date.today()


def _iso(d: date | str) -> str:
    return d if isinstance(d, str) else d.isoformat()


def pace_str(speed_mps, nd: int = 0) -> str | None:
    """米/秒 → 配速字符串 `6'39"`。"""
    try:
        v = float(speed_mps)
    except (TypeError, ValueError):
        return None
    if v <= 0.01:
        return None
    sec = 1000.0 / v
    m, s = divmod(int(round(sec)), 60)
    return f"{m}'{s:02d}\"" if nd == 0 else f"{m}'{s:02d}\""


def hms(seconds) -> str | None:
    try:
        v = float(seconds)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    h, rem = divmod(int(round(v)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def km(meters) -> float | None:
    try:
        return round(float(meters) / 1000.0, 2)
    except (TypeError, ValueError):
        return None


def num(v, nd: int = 1):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, nd) if nd else int(round(f))


def compact(obj):
    """去掉 None / 空容器，让输出更省 token（但不删 0）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            c = compact(v)
            if c is None or c == {} or c == []:
                continue
            out[k] = c
        return out
    if isinstance(obj, list):
        return [compact(x) for x in obj]
    return obj


def _dn(s: Session) -> str:
    """用户 GUID（displayName），几乎所有路径都要它。"""
    if s.display_name:
        return s.display_name
    r = _check(s, _request(s, "GET", "/gc-api/userprofile-service/userprofile/settings"),
               "读取用户信息")
    d = r.json() if r.text.strip() else {}
    dn = (d or {}).get("displayName") or ""
    if not dn:
        raise GarminError("拿不到用户 ID（displayName），请重新登录")
    s.display_name = dn
    save_session(s)
    return dn


def gql(s: Session, query: str):
    """GraphQL 网关（首页/日历/健康卡片的聚合查询）。"""
    body = json.dumps({"query": query}).encode()
    r = _check(s, _request(s, "POST", "/gc-api/graphql-gateway/graphql",
                           body=body, headers={"Content-Type": "application/json"}), "GraphQL")
    d = r.json()
    if d.get("errors"):
        raise GarminError("GraphQL 错误：" + str(d["errors"][0].get("message"))[:160])
    return d.get("data") or {}


def _sport_key(sport: str | None) -> str | None:
    """把 run/跑步/ride… 归一成佳明的 activityType key；all 返回 None。"""
    if not sport:
        return None
    k = SPORT_ALIAS.get(str(sport).strip().lower(), None)
    if k is None:
        k = SPORT_ALIAS.get(str(sport).strip(), None)
    if k is None:
        raise GarminError(f"不认识的运动类型：{sport}（可用：run/ride/swim/walk/hike/strength/all）")
    return None if k == "all" else k


def _type_cn(key: str | None) -> str:
    return ACTIVITY_TYPE_CN.get(key or "", key or "未知")


def _row(a: dict) -> dict:
    """活动列表单条的紧凑视图。"""
    t = (a.get("activityType") or {}).get("typeKey")
    return compact({
        "id": a.get("activityId"),
        "日期": (a.get("startTimeLocal") or "")[:10],
        "开始": (a.get("startTimeLocal") or "")[11:16],
        "名称": a.get("activityName"),
        "类型": _type_cn(t),
        "距离_km": km(a.get("distance")),
        "时长": hms(a.get("duration")),
        "配速": pace_str(a.get("averageSpeed")),
        "均心率": num(a.get("averageHR"), 0),
        "最大心率": num(a.get("maxHR"), 0),
        "爬升_m": num(a.get("elevationGain"), 0),
        "卡路里": num(a.get("calories"), 0),
        "步频": num(a.get("averageRunningCadenceInStepsPerMinute"), 0),
        "训练负荷": num(a.get("activityTrainingLoad"), 1),
        "有氧效果": num(a.get("aerobicTrainingEffect"), 1),
    })


# ==========================================================================
# 工具实现（只读）
# ==========================================================================
def t_get_profile(s: Session) -> dict:
    dn = _dn(s)
    info = api_soft(s, f"/gc-api/userprofile-service/userprofile/personal-information/{dn}") or {}
    soc = api_soft(s, f"/gc-api/userprofile-service/socialProfile/{dn}") or {}
    life = api_soft(s, f"/gc-api/usersummary-service/stats/connectLifetimeTotals/{dn}") or {}
    stats = api_soft(s, f"/gc-api/userstats-service/statistics/{dn}") or {}
    zones = api_soft(s, "/gc-api/biometric-service/heartRateZones/v2?generateDefaults=true") or []
    devs = api_soft(s, "/gc-api/device-service/deviceregistration/devices") or []
    prefs = api_soft(s, "/gc-api/userprofile-service/userprofile/settings") or {}

    bio = info.get("biometricProfile") or {}
    z = zones[0] if isinstance(zones, list) and zones else {}
    by_type = {}
    for m in (stats.get("userMetrics") or []):
        key = ((m.get("parentActivityType") or {}).get("typeKey")
               or (m.get("activityType") or {}).get("typeKey"))
        if not key:
            continue
        by_type["全部" if key == "all" else _type_cn(key)] = compact({
            "次数": num(m.get("totalActivities"), 0),
            "距离_km": km(m.get("totalDistance")),
            "时长": hms(m.get("totalDuration")),
        })

    devices = []
    for d in devs[:6]:
        devices.append(compact({
            "名称": d.get("productDisplayName"),
            "型号": d.get("productSku"),
            "固件": d.get("currentFirmwareVersion"),
            "序列号": d.get("serialNumber"),
            "主设备": bool(d.get("primary")),
        }))

    return compact({
        "昵称": soc.get("fullName") or prefs.get("displayName"),
        "用户ID": dn,
        "性别": {"MALE": "男", "FEMALE": "女"}.get(info.get("gender"), info.get("gender")),
        "年龄": info.get("age"),
        "身高_cm": num(bio.get("height"), 0),
        "体重_kg": num((bio.get("weight") or 0) / 1000.0, 1) if bio.get("weight") else None,
        "最大摄氧量": num(bio.get("vo2Max"), 1),
        "乳酸阈心率": num(bio.get("lactateThresholdHeartRate"), 0),
        "心率区间": compact({
            "方法": z.get("trainingMethod"),
            "最大心率": z.get("maxHeartRateUsed"),
            "阈值": [z.get(f"zone{i}Floor") for i in range(1, 6)],
            "说明": "阈值是 5 个区的下界（bpm）；静息心率要看 get_health",
        }),
        "生涯总计": compact({
            "总距离_km": km(life.get("totalDistance")),
            "总步数": life.get("totalSteps"),
            "总卡路里": life.get("totalCalories"),
            "活动天数": life.get("totalActiveDays"),
        }),
        "分项统计": by_type,
        "设备": devices,
        "站点": SITES[s.site]["label"],
    })


def t_list_activities(s: Session, start_date: str | None = None, end_date: str | None = None,
                      sport: str | None = None, keyword: str | None = None,
                      limit: int = 10) -> dict:
    t = _sport_key(sport)
    rows = api(s, "/gc-api/activitylist-service/activities/search/activities",
               start=0, limit=max(1, min(int(limit), 50)),
               startDate=start_date, endDate=end_date, activityType=t, search=keyword) or []
    return {
        "数量": len(rows),
        "活动": [_row(a) for a in rows],
        "提示": "用 get_activity 看单次详情（分段/心率区间/序列）",
    }


def _streams(s: Session, activity_id: str, resolution_sec: int = 30) -> dict | None:
    """把 details 的「列式」时序流转成按 key 的数组，并按 resolution_sec 降采样。"""
    d = api(s, f"/gc-api/activity-service/activity/{activity_id}/details",
            enabled="true", maxChartSize=10000, maxPolylineSize=4000)
    if not d:
        return None
    desc = {x["key"]: x for x in (d.get("metricDescriptors") or [])}
    pts = d.get("activityDetailMetrics") or []

    def idx(key):
        return (desc.get(key) or {}).get("metricsIndex")

    want = {
        "t": "directTimestamp",
        "dist": "sumDistance",
        "hr": "directHeartRate",
        "speed": "directSpeed",
        "cad": "directDoubleCadence",
        "alt": "directElevation",
        "power": "directPower",
        "gct": "directGroundContactTime",
        "vo": "directVerticalOscillation",
    }
    ix = {k: idx(v) for k, v in want.items()}
    step = max(1, int(resolution_sec))
    out = {k: [] for k in want}
    for i, p in enumerate(pts):
        if i % step:
            continue
        m = p.get("metrics") or []
        for k, j in ix.items():
            if j is None or j >= len(m):
                continue
            v = m[j]
            if v is None:
                continue
            out[k].append(v)
    t0 = (pts[0].get("metrics") or [None])[ix["t"] or 0] if pts else None
    return compact({
        "采样秒": step,
        "起点": datetime.fromtimestamp(t0 / 1000).strftime("%Y-%m-%d %H:%M:%S") if t0 else None,
        "点": out,
        "字段说明": {"t": "毫秒时间戳", "dist": "累计米", "hr": "bpm", "speed": "m/s",
                     "cad": "步/分", "alt": "米", "power": "瓦", "gct": "毫秒", "vo": "厘米"},
    })


def t_get_activity(s: Session, activity_id: str, include_streams: bool = False,
                   resolution_sec: int = 30) -> dict:
    a = api(s, f"/gc-api/activity-service/activity/{activity_id}")
    if not a:
        raise GarminError(f"找不到活动 {activity_id}")
    sm = a.get("summaryDTO") or {}
    md = a.get("metadataDTO") or {}
    splits = api_soft(s, f"/gc-api/activity-service/activity/{activity_id}/splits") or {}
    zones = api_soft(s, f"/gc-api/activity-service/activity/{activity_id}/hrTimeInZones") or []
    weather = api_soft(s, f"/gc-api/activity-service/activity/{activity_id}/weather?enabled=true") or {}

    laps = []
    for i, l in enumerate(splits.get("lapDTOs") or [], 1):
        laps.append(compact({
            "圈": l.get("lapIndex") or i,
            "距离_km": km(l.get("distance")),
            "用时": hms(l.get("duration")),
            "配速": pace_str(l.get("averageSpeed")),
            "均心率": num(l.get("averageHR"), 0),
            "最大心率": num(l.get("maxHR"), 0),
            "步频": num(l.get("averageRunCadence"), 0),
            "爬升_m": num(l.get("elevationGain"), 0),
            "功率": num(l.get("averagePower"), 0),
        }))

    zs = [{"区": z.get("zoneNumber"), "秒": num(z.get("secsInZone"), 0),
           "下限": num(z.get("zoneLowBoundary"), 0)} for z in zones]

    out = {
        "id": a.get("activityId"),
        "名称": a.get("activityName"),
        "类型": _type_cn((a.get("activityTypeDTO") or {}).get("typeKey")),
        "开始": (sm.get("startTimeLocal") or "").replace("T", " ")[:16],
        "距离_km": km(sm.get("distance")),
        "用时": hms(sm.get("duration")),
        "移动用时": hms(sm.get("movingDuration")),
        "配速": pace_str(sm.get("averageSpeed")),
        "平均配速_含暂停": pace_str((sm.get("distance") or 0) / (sm.get("elapsedDuration") or 1)),
        "心率": compact({"平均": num(sm.get("averageHR"), 0), "最大": num(sm.get("maxHR"), 0)}),
        "步频": num(sm.get("averageRunCadence"), 0),
        "步幅_cm": num(sm.get("strideLength"), 1),
        "触地时间_ms": num(sm.get("groundContactTime"), 0),
        "垂直振幅_cm": num(sm.get("verticalOscillation"), 1),
        "功率": compact({"平均": num(sm.get("averagePower"), 0),
                         "标准化": num(sm.get("normalizedPower"), 0)}),
        "卡路里": num(sm.get("calories"), 0),
        "爬升_m": num(sm.get("elevationGain"), 0),
        "下降_m": num(sm.get("elevationLoss"), 0),
        "训练效果": compact({"有氧": num(sm.get("trainingEffect"), 1),
                             "无氧": num(sm.get("anaerobicTrainingEffect"), 1),
                             "标签": sm.get("trainingEffectLabel")}),
        "训练负荷": num(sm.get("activityTrainingLoad"), 1),
        "强度分钟": compact({"中等": num(sm.get("moderateIntensityMinutes"), 0),
                             "高强度": num(sm.get("vigorousIntensityMinutes"), 0)}),
        "设备": md.get("manufacturer") and {
            "厂商": md.get("manufacturer"),
            "设备ID": (md.get("deviceMetaDataDTO") or {}).get("deviceId"),
        },
        "分段": laps,
        "心率区间": zs,
    }
    if weather:
        out["天气"] = compact({
            "气温_C": num((weather.get("temp") - 32) * 5 / 9, 1) if weather.get("temp") is not None else None,
            "体感_C": num((weather.get("apparentTemp") - 32) * 5 / 9, 1) if weather.get("apparentTemp") is not None else None,
            "湿度_%": num(weather.get("relativeHumidity"), 0),
            "风速": num(weather.get("windSpeed"), 0),
            "天气": (weather.get("weatherTypeDTO") or {}).get("desc"),
        })
    if include_streams:
        out["序列"] = _streams(s, activity_id, resolution_sec)
    return compact(out)


def _period_range(period: str, date_str: str | None) -> tuple[date, date, str]:
    """把 period/date_str 解析成闭区间 [start, end] 与展示名。"""
    today = _today()
    p = (period or "month").strip().lower()
    if p in ("custom", "区间") and date_str and "~" in date_str:
        a, b = [x.strip() for x in date_str.split("~", 1)]
        return date.fromisoformat(a), date.fromisoformat(b), f"{a} ~ {b}"
    if p in ("week", "周"):
        anchor = date.fromisoformat(date_str) if date_str else today
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6), f"{start} 当周"
    if p in ("month", "月"):
        anchor = date.fromisoformat(date_str + "-01") if date_str and len(date_str) == 7 else today
        start = anchor.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start, end, f"{start.year}-{start.month:02d}"
    if p in ("year", "年"):
        y = int(date_str) if date_str and len(date_str) == 4 else today.year
        return date(y, 1, 1), date(y, 12, 31), str(y)
    if p in ("all", "全部", "生涯"):
        return date(2000, 1, 1), today, "全部"
    raise GarminError("period 只支持 week/month/year/all/custom（custom 需要 date_str='2026-01-01 ~ 2026-03-01'）")


def _fetch_range(s: Session, start: date, end: date, sport: str | None = None) -> list[dict]:
    dn = _dn(s)
    d = api(s, f"/gc-api/activitylist-service/activities/list/{dn}",
            startTimestampLocal=f"{start.isoformat()}T00:00:00.00",
            endTimestampLocal=f"{end.isoformat()}T23:59:59.999",
            start=1, limit=1000, activityTypeId=None)
    rows = (d or {}).get("activityList") or []
    t = _sport_key(sport)
    if t:
        parent = SPORT_PARENT.get(t, 0)
        rows = [r for r in rows
                if (r.get("activityType") or {}).get("typeKey") == t
                or (r.get("activityType") or {}).get("parentTypeId") == parent]
    return rows


def t_get_period_stats(s: Session, period: str = "month", date_str: str | None = None,
                       sport: str | None = "run") -> dict:
    start, end, label = _period_range(period, date_str)
    rows = _fetch_range(s, start, end, sport)

    total_dist = sum(float(r.get("distance") or 0) for r in rows)
    total_time = sum(float(r.get("duration") or 0) for r in rows)
    total_up = sum(float(r.get("elevationGain") or 0) for r in rows)
    hrs = [r.get("averageHR") for r in rows if r.get("averageHR")]

    # 分桶：周→按天，月→按周，年→按月，全部/自定义→按月或按年
    buckets: dict[str, dict] = {}
    span_days = (end - start).days

    def bucket_key(d: date) -> str:
        if period in ("week", "周"):
            return d.isoformat()
        if period in ("month", "月"):
            monday = d - timedelta(days=d.weekday())
            return f"{monday.isoformat()} 周"
        if period in ("year", "年") or span_days <= 400:
            return f"{d.year}-{d.month:02d}"
        return str(d.year)

    for r in rows:
        try:
            d = date.fromisoformat((r.get("startTimeLocal") or "")[:10])
        except Exception:
            continue
        k = bucket_key(d)
        b = buckets.setdefault(k, {"次数": 0, "距离_km": 0.0, "时长_秒": 0.0, "爬升_m": 0.0})
        b["次数"] += 1
        b["距离_km"] += float(r.get("distance") or 0) / 1000.0
        b["时长_秒"] += float(r.get("duration") or 0)
        b["爬升_m"] += float(r.get("elevationGain") or 0)

    breakdown = []
    for k in sorted(buckets):
        b = buckets[k]
        breakdown.append({
            "期间": k,
            "次数": b["次数"],
            "距离_km": round(b["距离_km"], 2),
            "时长": hms(b["时长_秒"]),
            "均配速": pace_str(b["距离_km"] * 1000 / b["时长_秒"]) if b["时长_秒"] else None,
        })

    best = max(rows, key=lambda r: float(r.get("distance") or 0), default=None)
    return compact({
        "范围": label,
        "运动": _type_cn(_sport_key(sport)) if _sport_key(sport) else "全部",
        "总距离_km": round(total_dist / 1000.0, 2),
        "总时长": hms(total_time),
        "次数": len(rows),
        "总爬升_m": round(total_up),
        "平均配速": pace_str(total_dist / total_time) if total_time else None,
        "平均心率": round(sum(hrs) / len(hrs)) if hrs else None,
        "最长一次": compact({
            "id": (best or {}).get("activityId"),
            "日期": ((best or {}).get("startTimeLocal") or "")[:10],
            "距离_km": km((best or {}).get("distance")),
        }) if best else None,
        "分期": breakdown,
        "说明": None if breakdown else "该区间没有记录",
    })


def t_get_training_status(s: Session, days: int = 30) -> dict:
    today = _today().isoformat()
    dn = _dn(s)
    ts = api_soft(s, f"/gc-api/metrics-service/metrics/trainingstatus/daily/{today}") or {}
    lb = api_soft(s, f"/gc-api/metrics-service/metrics/trainingloadbalance/latest/{today}") or {}
    vo2 = api_soft(s, f"/gc-api/metrics-service/metrics/maxmet/latest/{today}") or {}
    rd = api_soft(s, f"/gc-api/metrics-service/metrics/trainingreadiness/{today}") or []
    hrv = api_soft(s, f"/gc-api/hrv-service/hrv/{today}")
    start = (_today() - timedelta(days=max(7, int(days)))).isoformat()
    weekly = api_soft(s, f"/gc-api/metrics-service/metrics/trainingstatus/weekly/{dn}",
                      fromCalendarDate=start, toCalendarDate=today) or {}

    latest = {}
    for dev_id, v in ((ts.get("latestTrainingStatusData") or {})).items():
        latest[dev_id] = v
    first = next(iter(latest.values()), {}) if latest else {}
    acute = first.get("acuteTrainingLoadDTO") or {}

    bal = {}
    for dev_id, v in ((lb.get("metricsTrainingLoadBalanceDTOMap") or {})).items():
        bal = v or {}
        break

    readiness = rd[0] if isinstance(rd, list) and rd else {}
    gen = (vo2.get("generic") or {})

    series = []
    for dev_id, items in ((weekly.get("reportData") or {})).items():
        for it in (items or [])[-int(days):]:
            series.append(compact({
                "日期": it.get("calendarDate"),
                "状态": it.get("trainingStatusFeedbackPhrase"),
                "周负荷": num(it.get("weeklyTrainingLoad"), 0),
            }))
        break

    def _rng(lo, hi):
        return [lo, hi] if (lo is not None or hi is not None) else None

    balance = compact({
        "低有氧": num(bal.get("monthlyLoadAerobicLow"), 0),
        "低有氧目标": _rng(bal.get("monthlyLoadAerobicLowTargetMin"), bal.get("monthlyLoadAerobicLowTargetMax")),
        "高有氧": num(bal.get("monthlyLoadAerobicHigh"), 0),
        "高有氧目标": _rng(bal.get("monthlyLoadAerobicHighTargetMin"), bal.get("monthlyLoadAerobicHighTargetMax")),
        "无氧": num(bal.get("monthlyLoadAnaerobic"), 0),
        "无氧目标": _rng(bal.get("monthlyLoadAnaerobicTargetMin"), bal.get("monthlyLoadAnaerobicTargetMax")),
        "提示": bal.get("trainingBalanceFeedbackPhrase"),
    }) if bal else None

    return compact({
        "日期": first.get("calendarDate") or today,
        "训练状态": first.get("trainingStatusFeedbackPhrase"),
        "自": first.get("sinceDate"),
        "训练趋势": {"1": "下降", "2": "维持", "3": "提升"}.get(str(first.get("fitnessTrend")),
                                                             first.get("fitnessTrend")),
        "急性负荷": compact({
            "ATL": num(acute.get("dailyTrainingLoadAcute"), 0),
            "ACWR_%": num(acute.get("acwrPercent"), 0),
            "ACWR状态": acute.get("acwrStatus"),
        }),
        "负荷平衡": balance,
        "最大摄氧量": compact({"值": num(gen.get("vo2MaxValue"), 0),
                              "精确值": num(gen.get("vo2MaxPreciseValue"), 1),
                              "日期": gen.get("calendarDate")}),
        "训练准备程度": compact({
            "分数": readiness.get("score"),
            "等级": readiness.get("level"),
            "建议": readiness.get("feedbackLong"),
            "恢复时间_小时": num((readiness.get("recoveryTime") or 0) / 3600.0, 1) or None,
            "睡眠因子_%": readiness.get("sleepScoreFactorPercent"),
            "ACWR因子_%": readiness.get("acwrFactorPercent"),
        }) if readiness else None,
        "HRV": compact({
            "状态": (hrv or {}).get("status") if isinstance(hrv, dict) else None,
            "夜间均值": (hrv or {}).get("lastNightAvg") if isinstance(hrv, dict) else None,
        }) if hrv else None,
        "近况": series,
        "说明": None if first else "该账号还没有训练状态数据（需要设备同步过带训练负荷的活动）",
        "解读要点": "训练状态看 trainingStatusFeedbackPhrase；负荷是否均衡看三档 vs 目标区间；"
                    "准备程度低时优先休息或轻松跑",
    })


def t_get_health(s: Session, day: str | None = None) -> dict:
    d = day or _today().isoformat()
    dn = _dn(s)
    ds = api_soft(s, f"/gc-api/usersummary-service/usersummary/daily/{dn}", calendarDate=d) or {}
    sleep = api_soft(s, "/gc-api/sleep-service/sleep/dailySleepData",
                     date=d, nonSleepBufferMinutes=60) or {}
    hr = api_soft(s, "/gc-api/wellness-service/wellness/dailyHeartRate", date=d) or {}
    stress = api_soft(s, f"/gc-api/wellness-service/wellness/dailyStress/{d}") or {}
    bb = api_soft(s, "/gc-api/wellness-service/wellness/bodyBattery/reports/daily",
                  startDate=d, endDate=d) or []
    resp = api_soft(s, f"/gc-api/wellness-service/wellness/daily/respiration/{d}") or {}
    spo2 = api_soft(s, f"/gc-api/wellness-service/wellness/daily/spo2/{d}") or {}
    hrv = api_soft(s, f"/gc-api/hrv-service/hrv/{d}")
    score = {}
    try:
        sd = gql(s, f'query{{sleepScalar(date:"{d}", sleepOnly: false)}}').get("sleepScalar") or {}
        score = ((sd.get("dailySleepDTO") or {}).get("sleepScores") or {})
    except Exception:
        score = {}

    dto = sleep.get("dailySleepDTO") or {}
    bbv = (bb[0] if isinstance(bb, list) and bb else {}) or {}
    return compact({
        "日期": d,
        "步数": compact({"值": ds.get("totalSteps"), "目标": ds.get("dailyStepGoal"),
                         "距离_km": km(ds.get("totalDistanceMeters"))}),
        "心率": compact({
            "静息": ds.get("restingHeartRate") or hr.get("restingHeartRate"),
            "最低": hr.get("minHeartRate"),
            "最高": hr.get("maxHeartRate"),
            "7日均静息": hr.get("lastSevenDaysAvgRestingHeartRate"),
        }),
        "睡眠": compact({
            "总时长": hms(dto.get("sleepTimeSeconds")),
            "深睡": hms(dto.get("deepSleepSeconds")),
            "浅睡": hms(dto.get("lightSleepSeconds")),
            "REM": hms(dto.get("remSleepSeconds")),
            "清醒": hms(dto.get("awakeSleepSeconds")),
            "分数": (score.get("overall") or {}).get("value") if isinstance(score, dict) else None,
            "入睡": dto.get("sleepStartTimestampLocal") and
                    datetime.fromtimestamp(dto["sleepStartTimestampLocal"] / 1000).strftime("%H:%M"),
            "起床": dto.get("sleepEndTimestampLocal") and
                    datetime.fromtimestamp(dto["sleepEndTimestampLocal"] / 1000).strftime("%H:%M"),
        }),
        "压力": compact({"平均": ds.get("averageStressLevel") or stress.get("avgStressLevel"),
                         "最高": ds.get("maxStressLevel") or stress.get("maxStressLevel")}),
        "身体电量": compact({
            "最高": bbv.get("highest") or ds.get("bodyBatteryHighestValue"),
            "最低": bbv.get("lowest") or ds.get("bodyBatteryLowestValue"),
            "当前": bbv.get("mostRecent") or ds.get("bodyBatteryMostRecentValue"),
            "充入": bbv.get("charged"),
            "消耗": bbv.get("drained"),
        }),
        "呼吸_次分": compact({"清醒": resp.get("avgWakingRespirationValue"),
                              "睡眠": resp.get("avgSleepRespirationValue")}),
        "血氧_%": compact({"平均": spo2.get("averageSpo2") or ds.get("averageSpo2"),
                           "最低": spo2.get("lowestSpo2") or ds.get("lowestSpo2")}),
        "HRV": hrv if isinstance(hrv, dict) else None,
        "强度分钟": compact({"中等": ds.get("moderateIntensityMinutes"),
                             "高强度": ds.get("vigorousIntensityMinutes"),
                             "目标": ds.get("intensityMinutesGoal")}),
        "说明": "数值为 null 表示设备当天没有佩戴/没同步，不是接口错误",
    })


PR_LABEL = {
    1: "1K", 2: "1 英里", 3: "5K", 4: "10K", 5: "半程马拉松", 6: "全程马拉松",
    7: "最长跑步距离", 8: "最长骑行距离", 9: "最大爬升", 10: "最大功率", 11: "40K 骑行",
    12: "单日最多步数", 13: "单周最多步数", 14: "单月最多步数",
    15: "最长连续达标", 16: "当前连续达标",
    17: "最长游泳距离", 18: "100 米", 19: "100 码", 20: "400 米", 21: "500 码",
    22: "750 米", 23: "1000 米", 24: "1000 码", 25: "1500 米", 26: "1650 码",
    27: "单日最多活动时间", 28: "卧推最大重量", 29: "推举最大重量", 30: "深蹲最大重量",
    31: "硬拉最大重量", 32: "划船最大重量",
}
PR_TIME_TYPES = {1, 2, 3, 4, 5, 6, 11, 18, 19, 20, 21, 22, 23, 24, 25, 26}


def t_get_personal_records(s: Session, sport: str | None = None) -> dict:
    dn = _dn(s)
    prs = api_soft(s, f"/gc-api/personalrecord-service/personalrecord/prs/{dn}?includeHistory=false") or []
    types = {t.get("id"): t for t in (api_soft(s, f"/gc-api/personalrecord-service/personalrecordtype/prtypes/{dn}") or [])}
    want = _sport_key(sport)
    rows = []
    for p in prs:
        tid = p.get("typeId")
        meta = types.get(tid) or {}
        sp = (meta.get("sport") or "").upper()
        if want:
            mapping = {"running": "RUNNING", "cycling": "CYCLING", "swimming": "SWIMMING"}
            if sp != mapping.get(want):
                continue
        val = p.get("value")
        if tid in PR_TIME_TYPES:
            shown = hms(val)
        elif sp == "STEPS" or "steps" in (meta.get("key") or ""):
            shown = num(val, 0)
        elif "power" in (meta.get("key") or ""):
            shown = f"{num(val, 0)} W"
        else:
            shown = f"{km(val)} km" if val and val > 1000 else num(val, 1)
        rows.append(compact({
            "项目": PR_LABEL.get(tid, meta.get("key") or f"类型{tid}"),
            "成绩": shown,
            "活动": p.get("activityName"),
            "日期": (p.get("activityStartDateTimeLocalFormatted")
                     or p.get("actStartDateTimeInGMTFormatted") or "")[:10],
            "活动ID": p.get("activityId"),
            "运动": _type_cn((p.get("activityType") or "").lower()) or sp,
        }))
    return {"数量": len(rows), "个人纪录": rows}


# ==========================================================================
# 图表渲染（Pillow，无浏览器依赖，可直接作为图片返回给 agent）
# ==========================================================================
BG = (255, 255, 255)
INK = (28, 30, 34)
MUTED = (108, 112, 118)
FAINT = (170, 175, 182)
CARD = (246, 248, 250)
HAIR = (236, 239, 243)
BLUE = (0, 124, 195)        # Garmin Connect 主色
BLUE_DK = (0, 84, 140)
BLUE_LT = (108, 178, 222)
ORANGE = (240, 122, 44)
RED = (208, 62, 62)

_FONTS: dict = {}
_FONT_BOLD_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_FONT_REG_PATHS = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
_RESOLVED_FONT: dict = {}


def _find_font(bold: bool):
    key = bool(bold)
    if key in _RESOLVED_FONT:
        return _RESOLVED_FONT[key]
    for p in (_FONT_BOLD_PATHS if bold else _FONT_REG_PATHS):
        if os.path.exists(p):
            _RESOLVED_FONT[key] = p
            return p
    _RESOLVED_FONT[key] = None
    return None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    key = (size, bold)
    if key in _FONTS:
        return _FONTS[key]
    path = _find_font(bold)
    f = None
    if path:
        try:
            f = ImageFont.truetype(path, size)
        except Exception:
            f = None
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:
            f = ImageFont.load_default()
    _FONTS[key] = f
    return f


def _pace_txt(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}'{s % 60:02d}\""


def _hm(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _stat_cards(d, x0, x1, y, items, h=124):
    n = len(items)
    gap = 20
    w = (x1 - x0 - gap * (n - 1)) / n
    for i, (k, v, sub) in enumerate(items):
        cx0 = x0 + i * (w + gap)
        d.rounded_rectangle([cx0, y, cx0 + w, y + h], radius=16, fill=CARD)
        d.text((cx0 + 22, y + 20), k, font=_font(21), fill=MUTED, anchor="la")
        d.text((cx0 + 22, y + 54), v, font=_font(36, True), fill=INK, anchor="la")
        if sub:
            vw = d.textlength(v, font=_font(36, True))
            d.text((cx0 + 28 + vw, y + 66), sub, font=_font(20), fill=MUTED, anchor="la")


def _chart_activity(s: Session, activity_id: str) -> bytes:
    from PIL import Image, ImageDraw
    import io

    a = api(s, f"/gc-api/activity-service/activity/{activity_id}")
    if not a:
        raise GarminError(f"找不到活动 {activity_id}")
    sm = a.get("summaryDTO") or {}
    splits = api_soft(s, f"/gc-api/activity-service/activity/{activity_id}/splits") or {}
    zones = api_soft(s, f"/gc-api/activity-service/activity/{activity_id}/hrTimeInZones") or []

    laps = []
    for lp in splits.get("lapDTOs") or []:
        dist = float(lp.get("distance") or 0)
        dur = float(lp.get("duration") or 0)
        if dist < 200 or dur <= 0:
            continue
        laps.append({
            "km": len(laps) + 1,
            "dist": dist / 1000.0,
            "pace": dur / (dist / 1000.0),
            "hr": float(lp.get("averageHR") or 0),
            "cad": float(lp.get("averageRunCadence") or 0),
        })
    if not laps:
        raise GarminError("这条活动没有可用的分段数据（可能是手动记录）")

    W, H = 2000, 1240
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    dist_km = float(sm.get("distance") or 0) / 1000
    avg_speed = float(sm.get("averageSpeed") or 0) or 1
    tkey = (a.get("activityTypeDTO") or {}).get("typeKey")
    d.text((96, 66), f"{(sm.get('startTimeLocal') or '')[:10]}  {a.get('activityName') or ''}",
           font=_font(44, True), fill=INK, anchor="la")
    d.text((96, 128),
           f"{_type_cn(tkey)} · {dist_km:.2f} km · {_hm(sm.get('duration') or 0)}"
           f" · 平均配速 {_pace_txt(1000 / avg_speed)}"
           f" · 平均心率 {int(float(sm.get('averageHR') or 0))}"
           f" · 步频 {int(float(sm.get('averageRunCadence') or 0))}",
           font=_font(24), fill=MUTED, anchor="la")

    _stat_cards(d, 96, 1904, 190, [
        ("距离", f"{dist_km:.2f}", "km"),
        ("平均配速", _pace_txt(1000 / avg_speed), "/km"),
        ("平均心率", f"{int(float(sm.get('averageHR') or 0))}", "bpm"),
        ("训练效果", f"{float(sm.get('trainingEffect') or 0):.1f}", "有氧"),
    ])

    d.text((96, 352), "每公里配速与心率", font=_font(28, True), fill=INK, anchor="la")
    X0, X1 = 236, 1904
    TOP, BASE = 410, 900
    PH = BASE - TOP
    mx_p = max(l["pace"] for l in laps)
    mn_p = min(l["pace"] for l in laps)
    pLo, pHi = mx_p + 40, max(120, mn_p - 25)      # 慢 -> 快

    for i in range(5):
        y = BASE - PH * i / 4
        pace_at = pLo - (i / 4) * (pLo - pHi)
        d.line([(X0, y), (X1, y)], fill=HAIR, width=2)
        d.text((X0 - 22, y), _pace_txt(pace_at), font=_font(21), fill=FAINT, anchor="rm")

    n = len(laps)
    slot = (X1 - X0) / n
    bw = min(84, slot * 0.5)
    for i, l in enumerate(laps):
        cx = X0 + slot * i + slot / 2
        h = PH * (pLo - l["pace"]) / (pLo - pHi)
        y = BASE - h
        fast = l["pace"] <= mn_p + 1
        d.rounded_rectangle([cx - bw / 2, y, cx + bw / 2, BASE], radius=12,
                            fill=BLUE_DK if fast else BLUE)
        if n <= 22:
            d.text((cx, y - 16), _pace_txt(l["pace"]), font=_font(25, True), fill=INK, anchor="ms")
        label = (f"{l['km']}" if l["dist"] >= 0.9 else f"{l['km'] - 1 + l['dist']:.1f}")
        d.text((cx, BASE + 22), label, font=_font(22), fill=MUTED, anchor="ma")

    d.text((96, BASE + 22), "km", font=_font(22), fill=MUTED, anchor="lm")

    # 心率折线（右轴）
    hrs = [l["hr"] for l in laps if l["hr"]]
    if len(hrs) >= 2:
        hLo, hHi = min(hrs) - 8, max(hrs) + 8
        pts = []
        for i, l in enumerate(laps):
            if not l["hr"]:
                continue
            cx = X0 + slot * i + slot / 2
            y = BASE - PH * (l["hr"] - hLo) / max(1, hHi - hLo)
            pts.append((cx, y))
        if len(pts) >= 2:
            d.line(pts, fill=ORANGE, width=5, joint="curve")
            for p in pts:
                d.ellipse([p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6], fill=BG, outline=ORANGE, width=4)
        for i in range(5):
            y = BASE - PH * i / 4
            d.text((X1 + 22, y), f"{int(hLo + (hHi - hLo) * i / 4)}", font=_font(20),
                   fill=(190, 150, 120), anchor="lm")
        d.text((X1 + 22, BASE - PH - 26), "bpm", font=_font(19), fill=(190, 150, 120), anchor="lm")

    # 心率区间条
    if zones:
        y0 = 1010
        d.text((96, y0 - 34), "心率区间停留时间", font=_font(24, True), fill=INK, anchor="la")
        total = sum(float(z.get("secsInZone") or 0) for z in zones) or 1
        x = 96
        colors = [BLUE_LT, BLUE, BLUE_DK, ORANGE, RED]
        for i, z in enumerate(sorted(zones, key=lambda z: z.get("zoneNumber") or 0)):
            w = (X1 - 96) * float(z.get("secsInZone") or 0) / total
            if w < 1:
                continue
            d.rectangle([x, y0, x + w, y0 + 54], fill=colors[i % len(colors)])
            if w > 110:
                d.text((x + w / 2, y0 + 27), f"Z{i + 1} {_hm(z.get('secsInZone') or 0)}",
                       font=_font(20, True), fill=BG, anchor="mm")
            x += w

    d.text((96, H - 62), "数据来源：Garmin Connect · 由 garmin-mcp 生成",
           font=_font(19), fill=FAINT, anchor="la")

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def t_volume_series(s: Session, weeks: int = 12, sport: str = "run") -> list[dict]:
    weeks = max(2, min(int(weeks), 52))
    today = _today()
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    rows = _fetch_range(s, start, today, sport)
    buckets: dict[str, float] = {}
    counts: dict[str, int] = {}
    for i in range(weeks):
        ws = start + timedelta(days=7 * i)
        buckets[ws.isoformat()] = 0.0
        counts[ws.isoformat()] = 0
    for r in rows:
        try:
            d0 = date.fromisoformat((r.get("startTimeLocal") or "")[:10])
        except Exception:
            continue
        ws = d0 - timedelta(days=d0.weekday())
        k = ws.isoformat()
        if k in buckets:
            buckets[k] += float(r.get("distance") or 0) / 1000.0
            counts[k] += 1
    out = []
    for k in sorted(buckets):
        d0 = date.fromisoformat(k)
        out.append({"周": f"{d0.month}/{d0.day}", "距离_km": round(buckets[k], 2), "次数": counts[k]})
    return out


def _chart_volume(s: Session, weeks: int = 12, sport: str = "run") -> bytes:
    from PIL import Image, ImageDraw
    import io

    series = t_volume_series(s, weeks, sport)
    if not series:
        raise GarminError("没有可画的跑量数据")

    W, H = 2000, 1080
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    total = sum(x["距离_km"] for x in series)
    cnt = sum(x["次数"] for x in series)
    avg = total / len(series)
    d.text((96, 66), f"近 {len(series)} 周训练量", font=_font(46, True), fill=INK, anchor="la")
    d.text((96, 132), f"合计 {total:.1f} km · {cnt} 次 · 周均 {avg:.1f} km · 运动类型：{_type_cn(_sport_key(sport) or 'all')}",
           font=_font(24), fill=MUTED, anchor="la")

    X0, X1, TOP, BASE = 236, 1904, 300, 900
    mx = max(max(x["距离_km"] for x in series), avg * 1.25, 1)
    for i in range(5):
        y = BASE - (BASE - TOP) * i / 4
        d.line([(X0, y), (X1, y)], fill=HAIR, width=2)
        txt = f"{mx * i / 4:.0f}" + (" km" if i == 4 else "")
        d.text((X0 - 22, y), txt, font=_font(21), fill=FAINT, anchor="rm")

    n = len(series)
    slot = (X1 - X0) / n
    bw = min(76, slot * 0.56)
    for i, x in enumerate(series):
        cx = X0 + slot * i + slot / 2
        h = (BASE - TOP) * x["距离_km"] / mx
        y = BASE - h
        color = BLUE if x["距离_km"] >= avg else BLUE_LT
        if x["距离_km"] > 0:
            d.rounded_rectangle([cx - bw / 2, y, cx + bw / 2, BASE], radius=12, fill=color)
            d.text((cx, y - 16), f"{x['距离_km']:.0f}", font=_font(24, True), fill=INK, anchor="ms")
        d.text((cx, BASE + 24), x["周"], font=_font(21), fill=MUTED, anchor="ma")

    # 周均虚线
    ya = BASE - (BASE - TOP) * avg / mx
    x = X0
    while x < X1:
        d.line([(x, ya), (min(x + 14, X1), ya)], fill=ORANGE, width=3)
        x += 26
    d.text((X0 + 8, ya - 14), f"周均 {avg:.1f} km", font=_font(21, True), fill=ORANGE, anchor="ls")

    d.text((96, H - 62), "数据来源：Garmin Connect · 由 garmin-mcp 生成",
           font=_font(19), fill=FAINT, anchor="la")
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ==========================================================================
# MCP 服务
# ==========================================================================
def _out(obj) -> str:
    return json.dumps(compact(obj), ensure_ascii=False, separators=(",", ":"))


def _guard(fn):
    """把内部异常翻译成对 agent 有意义的错误信息；同时保证会话可用。"""
    import functools
    from mcp.server.mcpserver.tools.base import ToolError

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except AuthRequired as e:
            raise ToolError(
                f"{e}\n（提示：让用户说一句「登录佳明」，或运行 python3 garmin_mcp.py login）") from e
        except GarminError as e:
            raise ToolError(f"Garmin 接口错误：{e}") from e
    return wrapper


def build_server():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer(
        "garmin",
        version="0.1.0",
        instructions=(
            "Garmin Connect（佳明）跑步与健康数据分析。先用 list_activities 找活动 id，"
            "再用 get_activity 取单次详情；长期趋势用 get_training_status / get_period_stats；"
            "睡眠、静息心率、HRV 用 get_health；PB 用 get_personal_records。"
            "单位已统一为 公里/秒/配速字符串，气温为摄氏度。"
            "若返回未登录，调用 login 工具（会弹浏览器窗口让用户登录）。"
        ),
    )

    @mcp.tool(description=(
        "账号与设备概览：昵称、性别年龄、身高体重、VO2max、乳酸阈心率、心率区间、"
        "生涯总计（总距离/总步数/活动天数）、分项统计、已注册设备。"))
    @_guard
    def get_profile() -> str:
        return _out(t_get_profile(ensure_session()))

    @mcp.tool(description=(
        "活动列表（按时间倒序）。可按起止日期（YYYY-MM-DD）、运动类型"
        "（run/ride/swim/walk/hike/strength/all）、名称关键字过滤，返回每次的距离/时长/配速/心率/负荷。"
        "用于先定位到具体某次活动。"))
    @_guard
    def list_activities(start_date: str | None = None, end_date: str | None = None,
                        sport: str | None = None, keyword: str | None = None,
                        limit: int = 10) -> str:
        return _out(t_list_activities(ensure_session(), start_date, end_date, sport, keyword, limit))

    @mcp.tool(description=(
        "单次活动完整数据：总览（距离/用时/配速/心率/步频/步幅/触地时间/功率/训练效果/负荷）、"
        "每公里分段、心率区间，以及天气。include_streams=true 时附带逐点序列"
        "（配速/心率/步频/海拔/功率，按 resolution_sec 降采样，默认 30 秒一个点）。"))
    @_guard
    def get_activity(activity_id: str, include_streams: bool = False,
                     resolution_sec: int = 30) -> str:
        return _out(t_get_activity(ensure_session(), activity_id, include_streams, resolution_sec))

    @mcp.tool(description=(
        "周期汇总：period 取 week/month/year/all/custom（custom 需 date_str='2026-01-01 ~ 2026-03-01'）；"
        "sport 取 run/ride/swim/walk/hike/strength/all。返回总距离/总时长/次数/平均配速/平均心率、"
        "分期明细（周按天、月按周、年按月）与期间最长一次。"))
    @_guard
    def get_period_stats(period: str = "month", date_str: str | None = None,
                         sport: str | None = "run") -> str:
        return _out(t_get_period_stats(ensure_session(), period, date_str, sport))

    @mcp.tool(description=(
        "训练状态与负荷：训练状态短语、训练趋势、急性负荷/ACWR、三档负荷平衡（低有氧/高有氧/无氧 vs 目标区间）、"
        "VO2max、训练准备程度（含睡眠/恢复/ACWR 因子）、HRV 状态、近 N 天状态曲线。"
        "判断是否过度训练、要不要减量时优先用它。"))
    @_guard
    def get_training_status(days: int = 30) -> str:
        return _out(t_get_training_status(ensure_session(), days))

    @mcp.tool(description=(
        "某一天的健康数据（默认今天）：步数、静息/最低/最高心率、睡眠（总时长/深睡/浅睡/REM/清醒/分数/入睡起床）、"
        "压力、身体电量、呼吸、血氧、HRV、强度分钟。参数 day 格式 YYYY-MM-DD。"))
    @_guard
    def get_health(day: str | None = None) -> str:
        return _out(t_get_health(ensure_session(), day))

    @mcp.tool(description=(
        "个人纪录（PB）：1K/1英里/5K/10K/半马/全马、最长跑步、最长骑行、最大爬升、"
        "游泳各距离、力量训练最大重量等。sport 可过滤 run/ride/swim。"))
    @_guard
    def get_personal_records(sport: str | None = None) -> str:
        return _out(t_get_personal_records(ensure_session(), sport))

    @mcp.tool(name="render_chart", description=(
        "把数据画成图，直接以图片返回（不需要任何外部工具）。"
        "kind='activity' 画单次活动的每公里配速+心率+心率区间（需 activity_id）；"
        "kind='volume' 画近 N 周跑量柱状图（weeks 默认 12，可配 sport）。"))
    @_guard
    def render_chart(kind: str = "activity", activity_id: str | None = None,
                     weeks: int = 12, sport: str = "run"):
        from mcp.server.mcpserver import Image as MCPImage
        s = ensure_session()
        k = (kind or "activity").strip().lower()
        if k.startswith("vol") or k in ("weeks", "跑量"):
            png = _chart_volume(s, weeks, sport)
        else:
            if not activity_id:
                from mcp.server.mcpserver.tools.base import ToolError
                raise ToolError("kind='activity' 需要 activity_id，请先用 list_activities 查到活动 id")
            png = _chart_activity(s, str(activity_id))
        return MCPImage(data=png, format="png")

    @mcp.tool(name="login", description=(
        "登录佳明。用户说「登录佳明」，或任何工具返回未登录时调用。"
        "会先弹一个网页让用户选「佳明中国 / Garmin 国际版」，再打开对应站点的登录窗口；"
        "用户在里面正常登录（邮箱/手机号+密码+验证码），登录成功后窗口自动关闭并保存凭据。"
        "已知站点时可直接传 site=cn 或 site=global 跳过选择。"))
    @_guard
    def login(site: str | None = None) -> str:
        import contextlib
        cur = load_session()
        if cur and _probe(cur):
            return json.dumps({"ok": True, "already": True,
                               "message": f"已是登录状态（{SITES[cur.site]['label']}），无需重新登录",
                               "expires": cur.expires}, ensure_ascii=False)
        target = (site or (cur.site if cur else DEFAULT_SITE)).lower()
        with contextlib.redirect_stdout(sys.stderr):
            rc = do_login(target)
        return json.dumps(
            {"ok": rc == 0, "already": False, "site": target,
             "message": "登录成功，凭据已保存（会自动续期）" if rc == 0 else "登录未完成，请重试"},
            ensure_ascii=False)

    return mcp


# ==========================================================================
# CLI
# ==========================================================================
def cmd_status() -> int:
    s = load_session()
    if not s:
        print("❌ 未登录。运行:  python3 garmin_mcp.py login")
        return 1
    ok = _probe(s)
    exp = datetime.fromtimestamp(s.expires).strftime("%Y-%m-%d %H:%M") if s.expires else "未知"
    print(("✅ 登录有效" if ok else "⚠️  凭据存在但已失效") + f"（{SITES[s.site]['label']}）")
    print(f"   保存于: {s.saved_at}   本次有效至: {exp}")
    print(f"   凭据文件: {SESSION} (权限 {oct(SESSION.stat().st_mode)[-3:]})")
    if ok:
        try:
            p = t_get_profile(s)
            life = p.get("生涯总计") or {}
            print(f"   {p.get('昵称')} · 生涯 {life.get('总距离_km')} km · {life.get('活动天数')} 天 · "
                  f"VO2max {p.get('最大摄氧量')}")
        except Exception as e:
            print(f"   (概览获取失败: {e})")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        build_server().run()
        return 0

    cmd = argv[0]
    site: str | None = None
    for a in argv:
        if a.startswith("--site="):
            site = a.split("=", 1)[1].strip().lower() or None
    try:
        if cmd == "login":
            if "--manual" in argv:
                return do_login_manual(site or DEFAULT_SITE)
            return do_login(site)
        if cmd == "logout":
            clear_session()
            for st in SITES:
                shutil.rmtree(_profile(st), ignore_errors=True)
            print("✅ 已清除本地凭据与登录配置")
            return 0
        if cmd == "status":
            return cmd_status()
        if cmd == "call":
            if len(argv) < 2:
                print("用法: python3 garmin_mcp.py call <tool> [--args '{...}']")
                return 1
            tool = argv[1]
            args = {}
            if "--args" in argv:
                args = json.loads(argv[argv.index("--args") + 1])
            fn = {
                "get_profile": t_get_profile,
                "list_activities": t_list_activities,
                "get_activity": t_get_activity,
                "get_period_stats": t_get_period_stats,
                "get_training_status": t_get_training_status,
                "get_health": t_get_health,
                "get_personal_records": t_get_personal_records,
            }.get(tool)
            if not fn:
                print(f"未知工具: {tool}")
                return 1
            s = ensure_session()
            print(json.dumps(compact(fn(s, **args)), ensure_ascii=False, indent=1))
            return 0
        if cmd in ("chart", "render_chart"):
            s = ensure_session()
            kind = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "activity"
            out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("chart.png")
            if kind.startswith("vol"):
                png = _chart_volume(s, int(argv[argv.index("--weeks") + 1]) if "--weeks" in argv else 12)
            else:
                if "--id" not in argv:
                    print("用法: garmin_mcp.py chart activity --id 12345 --out a.png")
                    return 1
                png = _chart_activity(s, argv[argv.index("--id") + 1])
            out.write_bytes(png)
            print(f"✅ 已保存 {out}（{len(png)} 字节）")
            return 0
        if cmd in ("serve", "mcp"):
            build_server().run()
            return 0
        print(__doc__)
        return 1
    except AuthRequired as e:
        print(f"❌ {e}")
        return 2
    except GarminError as e:
        print(f"❌ {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
