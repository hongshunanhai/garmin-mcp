# Garmin MCP

[中文](README.md) | [English](README.en.md)

把 **佳明（Garmin Connect）** 的训练与健康数据接进你的 AI 助手，让它直接回答
「我这个月跑了多少」「分析一下我昨天那次跑步」「我最近是不是练过量了」「我昨晚睡得怎么样」。

> 只读。不会修改或删除你的任何记录，**也永远不接触你的账号密码**。
> 国区（connect.garmin.cn）和国际版（connect.garmin.com）都支持。

```
你：我上周跑得怎么样
AI：（调用 get_period_stats + list_activities）上周 3 次共 17.8 km，平均配速 6'37"，比前一周少 12%…
```

---

## 它能做什么

| 你可以这样问 | 它调用的工具 |
|---|---|
| 我昨天跑了多少 | `list_activities` |
| 分析一下我昨天那次跑步，配速稳不稳 | `get_activity` |
| 画一下我这次的分段配速和心率 | `render_chart` |
| 我这个月/今年跑了多少 | `get_period_stats` |
| 我这周是不是练过量了 | `get_training_status` |
| 我昨晚睡得怎么样、静息心率多少 | `get_health` |
| 我的 5K/半马 PB 是多少 | `get_personal_records` |
| 我的 VO2max、心率区间、都有什么设备 | `get_profile` |

图表是服务端直接生成的 PNG，跑友不需要装浏览器自动化之类的东西。

## 数据来自哪

| 类别 | 内容 |
|---|---|
| 活动 | 距离、用时、配速、心率、步频、步幅、触地时间、垂直振幅、功率、训练效果、训练负荷、每公里分段、心率区间、天气 |
| 训练状态 | 训练状态短语、ACWR、三档负荷平衡、VO2max、训练准备程度、HRV、近 N 天曲线 |
| 健康 | 步数、静息/最低/最高心率、睡眠分期与分数、压力、身体电量、呼吸、血氧、HRV、强度分钟 |
| 其他 | 个人纪录、生涯总计、设备列表、心率区间阈值 |

---

## 支持哪些客户端

同一份代码，五个客户端都能接——区别只在「配置文件写在哪」。

| 客户端 | 配置文件 | 说明 |
|---|---|---|
| **Claude Code** | `~/.claude.json`（用户级）或项目内 `.mcp.json` | 也可用 `claude mcp add` |
| **Codex CLI** | `~/.codex/config.toml` → `[mcp_servers.garmin]` | TOML 格式 |
| **ZCode** | `~/.zcode/cli/config.json`（`mcp.servers`） | 也可用设置面板「导入」 |
| **WorkBuddy** | `~/.workbuddy/mcp.json` | 界面里粘贴 JSON 即可 |
| **DeepSeek Harness** | profile 的 `cordis.patch.yml` | 用 `@deepseek-ai/dsh-mcp-client` |
| 其它（通用） | `~/.agents/mcp.json` | 业界通用约定，ZCode / 部分工具会读 |

每个客户端的图文步骤见 [`docs/`](docs/)。

---

## 快速开始

### 方式一：一键脚本（推荐）

```bash
git clone https://github.com/hongshunanhai/garmin-mcp.git
cd garmin-mcp
./install.sh
```

脚本会自动完成四件事：

```
[1/4] 找 Python（3.11+）
[2/4] 建 .venv 并装依赖（mcp / websockets / pillow，不污染系统）
[3/4] 弹出登录窗口 → 你正常登录一次
[4/4] 自动检测并写入各客户端配置
```

想只配某一个客户端 / 用国际版账号：

```bash
./install.sh --client=claude
./install.sh --site=global
./install.sh --client=codex --no-login
```

macOS 用户也可以直接**双击 `install.command`**。

### 方式二：手动配置

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python garmin_mcp.py login     # 弹窗登录
```

然后把这两行填进你客户端的 MCP 配置（路径换成你的实际路径）：

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/绝对路径/garmin-mcp/.venv/bin/python",
      "args": ["/绝对路径/garmin-mcp/garmin_mcp.py"]
    }
  }
}
```

---

## 登录是怎么工作的

佳明和大多数国内运动 App 不一样，有三个坑：

1. **关键 Cookie 全是 HttpOnly**（`session` / `JWT_WEB` / `SESSIONID`）——`document.cookie` 读不到；
2. 每个请求还要带 `Connect-Csrf-Token`（页面 `<meta name="csrf-token">`）和 `Sec-Fetch-Site: same-origin`，缺一个直接 403；
3. 会话只有 **约 2 小时**，但可以静默续期。

所以登录是这样做的：

```
python3 garmin_mcp.py login
  → 起一个独立 Chrome 窗口（自己的 profile，不动你日常浏览器）
  → 打开 connect.garmin.cn（或 connect.garmin.com）→ 跳转到佳明 SSO 登录页
  → 你自己输 邮箱/手机号 + 密码 + 验证码
  → 工具通过 Chrome DevTools 协议读 HttpOnly Cookie（Storage.getCookies）
  → 顺便读 <meta name="csrf-token">
  → 校验通过后写入 ~/.garmin-mcp/session.json（权限 600）
```

之后：

- **自动续期**：每次调用前检查有效期，快过期就 `POST /modern/di-oauth/refreshToken`
  静默换新 Cookie（佳明的续期凭据是**一次性**的，工具会立刻落盘，不用你管）
- **掉线兜底**：真过期了，工具会**自动弹出登录窗口**让你重新登录（多数情况 SSO Cookie
  还在，点一下「Sign In」就进去了）
- **换账号**：`python3 garmin_mcp.py logout` 后再 `login`

> **账号密码全程只输入给佳明自己的登录页**，本工具只保存登录后的会话 Cookie，
> 而且只在你的机器上。

### 国区 / 国际版

两站部署的是**同一份前端代码**，接口路径完全一致，只有域名和 SSO 不同：

| | 国区 | 国际版 |
|---|---|---|
| 站点 | `connect.garmin.cn` | `connect.garmin.com` |
| 登录 | `sso.garmin.cn` | `sso.garmin.com` |
| 命令 | `login` | `login --site=global` |

工具会记住你登录的站点，之后所有请求自动走对应的域名。

---

## 提供的工具（9 个，全部只读）

| 工具 | 作用 |
|---|---|
| `list_activities` | 活动列表，可按日期 / 运动类型 / 关键字过滤 |
| `get_activity` | 单次活动详情：总览、每公里分段、心率区间、天气，可选逐点序列 |
| `render_chart` | 生成图表：单次活动的配速+心率图 / 近 N 周跑量柱状图 |
| `get_period_stats` | 周 / 月 / 年 / 全年 / 自定义区间汇总 |
| `get_training_status` | 训练状态、ACWR、负荷平衡、VO2max、准备程度、HRV |
| `get_health` | 某天的步数、心率、睡眠分期、压力、身体电量、呼吸、血氧 |
| `get_personal_records` | 1K~全马、最长跑步、游泳、力量训练的 PB |
| `get_profile` | 昵称、身高体重、VO2max、心率区间、生涯总计、设备 |
| `login` | 打开登录窗口（用户说「登录佳明」时调用） |

明确**没有**封装：上传 FIT、删除活动、改标题、发评论等一切写操作。

---

## 隐私

**数据的流向要说清楚：**

```
你的机器 ──请求──▶ connect.garmin.cn / .com（佳明官方服务器）
   │
   └──工具返回的训练数据/图表──▶ 你使用的 AI 客户端 ──▶ 模型服务商
```

也就是说：本工具本身不经过任何第三方服务器，但**工具返回的数据会进入你所用 AI 客户端的对话**。
如果你用的是云端模型（DeepSeek、Claude、GPT 等），这部分数据会按该服务商的条款被提交处理——
这点和你把跑步数据手动贴进对话框没有区别。

**本机保存的东西：**

| 位置 | 内容 | 说明 |
|---|---|---|
| `~/.garmin-mcp/session.json` | 会话 Cookie + CSRF（权限 600） | 删除即退出登录 |
| `~/.garmin-mcp/chrome-profile-cn/`、`chrome-profile-global/` | 登录用的独立 Chrome 配置目录 | 含该浏览器的缓存，不再需要可整个删除 |

仓库里没有任何用户数据；`.gitignore` 已排除 `.venv/`、`session.json`、`charts/`、本地 bundle。

---

## 常见问题

**Q：提示「登录已过期」**
A：工具会自己弹窗，跟着登录一次即可；或者终端跑 `./.venv/bin/python garmin_mcp.py login`。

**Q：Windows / Linux 能用吗？**
A：能。工具会自动找 Chrome（macOS / Windows / Linux 的常见路径都覆盖），
找不到时用兜底：`garmin_mcp.py login --manual`，按提示粘贴 Cookie。

注意虚拟环境里的解释器路径不同：

| 平台 | Python 路径 |
|---|---|
| macOS / Linux | `.venv/bin/python` |
| Windows | `.venv\Scripts\python.exe` |

Windows 的 `install.sh` 需要 Git Bash / WSL；没有就用上面的手动配置。

**Q：会话老是掉？**
A：佳明的续期 token 是**一次性**的。如果你在浏览器里也登录了同一个账号，
或者在两个客户端里同时用，可能互相顶掉。重新 `login` 一次即可。
另外登录时勾上 **Remember Me** 会明显更稳。

**Q：会不会把我的数据传出去？**
A：本工具自身只请求佳明官方域名，没有任何第三方中转。数据隐私见上面的「隐私」一节。

**Q：为什么不用 npx / uvx 分发？**
A：单文件 Python + venv，好处是不依赖 Node 生态、跑友机器上只要有个 Python 就能跑。

更多见 [`docs/troubleshooting.md`](docs/troubleshooting.md)。

---

## 项目结构

```
garmin-mcp/
├── garmin_mcp.py        # 服务端主文件（登录 + 9 个工具 + 画图）
├── install.sh           # 一键安装器（自动配置 5 个客户端）
├── install.command      # macOS 双击版
├── requirements.txt
├── scripts/
│   ├── smoke_test.py    # CI 用的协议冒烟测试
│   ├── unit_tests.py    # 纯逻辑单测（不联网）
│   └── preflight.sh     # 发布前自检
├── docs/                # 各客户端接入说明 + 排错
├── examples/            # 可直接复制的配置样例
├── dsh-bundle/          # DeepSeek Harness 的 bundle 形式安装包
└── .github/workflows/   # CI：静态检查 + 冒烟测试 + 安装器测试
```

## 开发

**发布前跑一次自检**（推荐）：

```bash
./scripts/preflight.sh
```

它会依次检查：shell 语法、是否有未提交的改动、跟踪文件里有没有 Cookie 或个人绝对路径、
单元测试、MCP 握手。任何一项不过都会以非零码退出。

单独跑某一项：

```bash
./.venv/bin/python scripts/unit_tests.py   # 纯逻辑单测（不联网）
./.venv/bin/python scripts/smoke_test.py   # MCP 协议握手 + 工具清单
bash -n install.sh                         # 安装脚本语法
```

## 免责声明

本项目为个人开发的第三方工具，与 Garmin 官方无关，通过公开的网页接口读取**你自己账号**的数据。
请遵守 Garmin 的服务条款，不要用于批量抓取或商业用途。

## License

MIT
