# 常见问题排查

## 工具列表里没有 garmin

1. 确认配置里的 `command` 是**绝对路径**，指向 `.venv/bin/python`
2. 手动跑一次，看有没有报错：

```bash
/绝对路径/garmin-mcp/.venv/bin/python /绝对路径/garmin-mcp/garmin_mcp.py status
```

3. 有输出 `❌ 未登录` 说明服务端本身是好的，只是没登录
4. 改完配置后**重启客户端**（多数客户端只在启动时读配置）

## 提示「尚未登录佳明」

两种办法：

- 在对话里说「登录佳明」→ 调用 `login` 工具：**先弹网页选国区/国际版，再打开登录窗口**
- 或终端执行：

```bash
./.venv/bin/python garmin_mcp.py login              # 弹框里选站点
./.venv/bin/python garmin_mcp.py login --site=cn       # 直接国区
./.venv/bin/python garmin_mcp.py login --site=global   # 直接国际版
```

## 登录窗口打不开 / 一闪而过

- 确认装了 **Google Chrome**（本工具不依赖 Chrome 之外的浏览器）
- macOS 首次运行可能提示「无法验证开发者」：右键 → 打开
- 上次的 Chrome 没退干净时会报「调试端口未就绪」：把残留的 Chrome 关掉再试
- 实在不行用兜底：

```bash
./.venv/bin/python garmin_mcp.py login --manual
```

## 登录后 2 小时就失效？

不会。工具会在会话快过期时自动调 `/modern/di-oauth/refreshToken` 续期，
只要不是连续 2~3 小时完全不用，就不会掉线。

但注意：**佳明的续期凭据是一次性的**。如果你在浏览器里又登录了一次，
或者在两个地方同时用同一个账号，可能会互相顶掉，重新 `login` 一次即可。

## 为什么「同一个账号在浏览器里登录着」反而更容易掉线

佳明的续期凭据是**一次性**的：谁先刷新，谁就拿到新会话，旧的刷新凭据作废。

所以如果你把**浏览器正在用的会话**（比如从 DevTools 复制 Cookie、或读 Chrome profile）
交给工具，浏览器自己每隔约 1 小时也会刷新一次，就会把工具的刷新凭据「用掉」。
表现是：工具能用到 2 小时，然后就要重新登录。

**正确做法**：用工具自己的 `login` 窗口登录一次，得到一个**独立会话**，
之后工具自己续期，不依赖浏览器。

## 会话一直掉 / 总是 403

- 403 通常是 **CSRF 失效或缺少 `Sec-Fetch-Site`**，工具已自动处理，出现说明会话真的过期了
- 如果你在公司网络/代理下，确认 `connect.garmin.cn`（或 `.com`）不被拦截
- 检查本机时间是否准确（偏差过大也会导致签名类校验失败）

## 图表里中文变成方块

系统缺少中文字体。按顺序自动查找：

```
macOS:   STHeiti Medium / Hiragino Sans GB
Linux:   Noto Sans CJK（apt install fonts-noto-cjk）
Windows: 微软雅黑（系统自带）
```

都没有时会退化成 Pillow 内置字体（中文变方块）。

## 创建课表提示「写操作未开启」

默认只读。要建课表/排期/下发到设备，需要：

```bash
export GARMIN_ALLOW_WRITE=1
```

客户端里则在 MCP 配置的 `env` 加，例如：

```json
"env": { "GARMIN_ALLOW_WRITE": "1" }
```

即便如此，写工具也默认 dry-run，要真正执行必须 `confirm=true`。

## 数据看起来不对

- 距离单位是**公里**，时长 `时:分:秒`，配速 `分'秒"`，速度原始值是 **m/s**
- 跑步机记录**没有 GPS**，爬升和轨迹为空是正常的
- 气温是**摄氏度**（接口原始值是华氏度，工具已转换）
- 睡眠/静息心率/HRV 等需要**佩戴手表睡觉**才有值，返回 null 不是接口错误

## 我用的是国际版账号

```bash
./.venv/bin/python garmin_mcp.py login --site=global
```

之后所有请求都走 `connect.garmin.com`。国区和国际版的接口路径完全一致
（同一份前端代码），只是域名和 SSO 不同。

## 安装依赖失败

- 需要 **Python 3.11+**，推荐 3.11–3.13
- 公司网络需要代理时，先 `export https_proxy=...` 再跑 `./install.sh`
