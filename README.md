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
| 我这个月哪几天跑了 | `get_calendar` |
| 我这双鞋跑了多少公里、该换了吗 | `get_gear` |
| 我下周的课表是什么 | `get_training` |
| 我都有什么设备、固件多少 | `get_devices` |
| 我有哪些徽章、目标进度如何 | `get_achievements` |
| 有什么挑战可以参加 | `get_challenges` |
| 把这次活动的 GPX 导出来 | `download_activity` |
| 我的 VO2max、心率区间 | `get_profile` |

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
  → 先弹一个本地小页面：「你的佳明账号在哪个站点？」
        [ 佳明中国 ]  connect.garmin.cn   +86 手机号
        [ Garmin 国际版 ]  connect.garmin.com  邮箱 / 非 +86
  → 你点一下，随后起一个独立 Chrome 窗口（自己的 profile，不动你日常浏览器）
  → 打开对应站点的 SSO 登录页（国区是中文页，国际版是英文页）
  → 你自己输 邮箱/手机号 + 密码 + 验证码
  → 工具通过 Chrome DevTools 协议读 HttpOnly Cookie（Storage.getCookies）
  → 顺便读 <meta name="csrf-token">
  → 校验通过后写入 ~/.garmin-mcp/session.json（权限 600）
```

第一次调用任意工具时如果没登录，也会走这个流程（选站点 → 登录窗口 → 自动继续刚才那次调用）。

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
| 命令 | `login`（在弹框里选「佳明中国」） | `login`（在弹框里选「Garmin 国际版」） |
| 跳过选择 | `login --site=cn` | `login --site=global` |

工具会记住你登录的站点，之后所有请求自动走对应的域名；换站点用 `logout` 再 `login`。

---

## 提供的工具（24 个；默认只读，写操作需显式开启）

**核心数据**

| 工具 | 作用 |
|---|---|
| `list_activities` | 活动列表，可按日期 / 运动类型 / 关键字过滤 |
| `get_activity` | 单次活动详情：总览、每公里分段、心率区间、天气，可选逐点序列 |
| `get_period_stats` | 周 / 月 / 年 / 全年 / 自定义区间汇总 + 分期明细 |
| `get_calendar` | 月历：每天的活动 + 训练/赛事日程 |
| `get_training_status` | 训练状态、ACWR、三档负荷平衡、VO2max、准备程度、HRV、成绩预测、乳酸阈 |
| `get_health` | 某天：步数、心率、睡眠分期与分数、压力、身体电量、呼吸、血氧、HRV、体重、血压、补水 |
| `get_personal_records` | 1K~全马、最长跑步、游泳、力量训练的 PB |
| `get_profile` | 昵称、身高体重、VO2max、心率区间、生涯总计、分项统计 |

**训练与装备**

| 工具 | 作用 |
|---|---|
| `get_training` | 我的训练计划、训练课程列表；传 `workout_id` 看课表步骤 |
| `search_events` | 搜 Garmin 官方赛事库（名称/日期/城市/距离） |
| `get_gear` | 跑鞋/自行车：已用里程、里程上限与剩余、使用次数 |
| `get_devices` | 设备型号、固件、序列号、能力（血氧/训练准备度/HRV…）、最后同步 |

**社交与成就**

| 工具 | 作用 |
|---|---|
| `get_achievements` | 徽章、等级积分、进行中的目标 |
| `get_challenges` | 可参加的挑战、进行中的挑战 |
| `get_social` | 粉丝、关注、群组 |

**其它**

| 工具 | 作用 |
|---|---|
| `render_chart` | 生成图表：单次活动的配速+心率图 / 近 N 周跑量柱状图 |
| `download_activity` | 导出原始文件（fit / gpx / tcx / kml / csv）到本机，返回路径 |
| `login` | 选站点 + 打开登录窗口（用户说「登录佳明」时调用） |

**写操作（默认关闭，见下一节）**

| 工具 | 作用 |
|---|---|
| `create_workout` | 创建跑步/骑行/游泳课表（对应网页「训练课程 → 创建训练」） |
| `schedule_workout` | 把课表排到某一天 |
| `push_workout_to_device` | 「发送到设备」（手表下次同步即收到） |
| `delete_workout` | 删除课表（用于撤销建错的） |
| `create_event` | 把赛事加进日历（目标赛事，首页倒计时） |
| `delete_event` | 删除日历里的赛事（撤销用） |

明确**没有**封装：上传 FIT、删除活动、改标题、发评论等其它写操作。

---

## 创建课表并推到手表（可选，默认关闭）

这个能力会**修改你的账号数据**，所以分成两条通道：

| 通道 | 谁来确认 | 需要开关吗 |
|---|---|---|
| **页面**（推荐） | 你亲手点「创建到佳明」 | **不需要**，页面即确认 |
| AI 直接写（`confirm=true`） | AI 自己决定 | 需要 `GARMIN_ALLOW_WRITE=1` |

日常用页面就够了；只有想让 AI 在对话里「说完就建」时才需要开开关：

```bash
export GARMIN_ALLOW_WRITE=1        # 或在客户端配置的 env 里加
```

**创建课表默认不是“直接写”，而是弹一个页面给你确认**：AI 把你说的计划翻译成课表，
本地打开一个仿佳明的编辑器（热身/跑步/恢复/缓和 + 重复组，可以改距离、配速、组数），
你点「创建到佳明」才真正写入，页面上还能顺手排期、发送到设备。

```
你：帮我建一个热身 1.5 公里 7:30-8:00，然后 1 公里 5:00-5:30 快跑 + 1 公里 7:30-8:00 慢跑，重复 3 组
AI：（打开本地编辑器）
    热身 1.50 公里 @ 7'30"-8'00"
      重复 3 组：
        快跑 1.00 公里 @ 5'00"-5'30"
        慢跑恢复 1.00 公里 @ 7'30"-8'00"
    总距离 7.50 km · 预计 0:50:38
你：（点「创建到佳明」）
AI：已创建，id 38177913 → 在 Garmin 打开
```

页面上可以直接改：距离、时长、配速区间、重复次数、增删步骤；右边实时算总距离和预计时长。

> 设 `GARMIN_WORKOUT_EDITOR=0` 可以关掉页面，退回纯 JSON 的 dry-run。

**纯命令行/脚本场景**（以「4×800 米间歇」为例）：

```
1) create_workout  → 预览
   {"name":"800米间歇 4组","steps":[
      {"type":"warmup","duration_min":10},
      {"type":"repeat","times":4,"steps":[
         {"type":"interval","distance_m":800,"pace":"5:00"},
         {"type":"recovery","duration_min":2}]},
      {"type":"cooldown","duration_min":10}]}
   →  热身 10 分钟 / 重复 4 组: 快跑 800 米 @ 4'54"-5'04" / 慢跑恢复 2 分钟 / 缓和 10 分钟

2) 确认后 create_workout 加 confirm=true → 返回 workout_id

3) schedule_workout(workout_id, "2026-11-10", confirm=true)   排到下周二

4) push_workout_to_device(confirm=true)                       发送到设备
   → 手表下次和手机同步时收到这节课
```

支持的目标：

| 写法 | 含义 |
|---|---|
| `"pace": "5:00"` | 单值，**自动扩成 ±5 秒区间**（4'55"-5'05"） |
| `"pace_range": ["5:30", "5:00"]` | 精确区间（慢-快，顺序不敏感） |
| `"hr_zone": 4` | 心率 4 区 |

> 单值自动 ±5 秒是为了兼容佳明「配速目标必须是一个区间」的限制；
> 想要严格到时秒就显式写 `pace_range`。
支持的运动：`run` / `bike` / `swim`。

> ⚠️ 两点务必知道：
> 1. **写接口我只在网页代码里见过，没在你账号上真调过**（为了不污染数据）。第一次请先 dry-run 看 JSON，再用 `confirm=true` 建一节测试课表，确认手表上显示正常后删掉。
> 2. 课表一旦排期并下发，**手表会按它提示你训练**；建错请立刻 `delete_workout`。

---

## 手机 / 多端使用（HTTP 模式）

默认是 stdio（只给本机客户端用）。想让**手机也能连**，用 HTTP 模式：

```bash
# 只监听本机（本机调试用，不需要 token）
./.venv/bin/python garmin_mcp.py --http --port 8765

# 对外提供服务（必须带 token）
./.venv/bin/python garmin_mcp.py --http --host 0.0.0.0 --port 8765 \
    --token "$(openssl rand -hex 16)" --readonly
```

| 参数 | 说明 |
|---|---|
| `--http` | 用 streamable-http 传输（默认 stdio） |
| `--host` / `--port` | 监听地址，默认 `0.0.0.0:8765`，端点路径 `/mcp` |
| `--token` | Bearer 鉴权；监听非本机地址时**必填**，否则拒绝启动 |
| `--readonly` | 只暴露只读工具（**公网强烈建议**），建课表等写操作直接拒绝 |

客户端填：`URL = http://<host>:8765/mcp`，Header `Authorization: Bearer <token>`。

**远程模式的三个差异**（避免手机端体验错位）：

1. 不会弹「选站点」页，也不会弹课表编辑器 —— 这些窗口只会开在服务器屏幕上
2. 登录必须在**有浏览器的本机**完成，然后把 `~/.garmin-mcp/session.json` 复制到服务器
3. 会话续期由服务器进程负责；如果续期失败，把新的 `session.json` 再拷一次

### 部署到一台常开的服务器

```bash
# 1. 本机先登录
./.venv/bin/python garmin_mcp.py login

# 2. 传代码和凭据
rsync -av --exclude .venv ./ user@server:/opt/garmin-mcp/
scp ~/.garmin-mcp/session.json user@server:/home/user/.garmin-mcp/session.json

# 3. 服务器上装依赖并启动
ssh user@server
cd /opt/garmin-mcp && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python garmin_mcp.py --http --port 8765 --token <token> --readonly
```

建议用 systemd 常驻，并在前面加一层 HTTPS（Caddy / Nginx 反代，或 Cloudflare Tunnel）：
大部分手机端 App 只接受 `https://` 的 MCP 地址。

## 给 AI / 搜索引擎看的摘要

仓库根目录有 [`llms.txt`](llms.txt)，用一段话说明了这个项目是什么、怎么装、能干什么，
方便 AI 助手和搜索引擎直接引用。

## 许可证（重要）

本项目采用 **PolyForm Noncommercial License 1.0.0** —— 你可以：

- ✅ 自己用、改、分享给朋友
- ✅ 用于个人学习、业余爱好、跑步训练
- ✅ 学校、慈善机构、公益组织、政府机构使用

但**不可以**：

- ❌ 拿去卖钱（收费安装、付费托管、卖服务）
- ❌ 用在商业产品里（公司内部生产环境也算）

> 一句话：**随便用，别赚钱。** 需要商用请联系我单独授权。

注意：这不是 OSI 认可的开源协议，属于 **source-available（源码可用）**。
如果你 Fork 或分发，需要保留 `LICENSE` 里的 `Required Notice` 一行。

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

仓库里没有任何用户数据；`.gitignore` 已排除 `.venv/`、`__pycache__/`、`.ruff_cache/`、`session.json`、本地 bundle。

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
├── garmin_mcp.py        # 服务端主文件（登录 + 24 个工具 + 画图）
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

**登录后逐工具自检**（推荐先跑这个）：

```bash
./.venv/bin/python scripts/live_check.py
```

输出一张表：每个工具的状态（✅ / ⚠️ 无数据 / ❌ 报错）、耗时和一句摘要；
有 ❌ 时退出码为 1。也支持 `--json` / `--only get_profile,get_gear` / `--activity <id>`。

单独跑某一项：

```bash
./.venv/bin/python scripts/unit_tests.py     # 纯逻辑单测（不联网）
./.venv/bin/python scripts/smoke_test.py     # MCP 协议握手 + 工具清单
./.venv/bin/python scripts/installer_test.py # 安装器深测（临时 HOME，5 种形态）
./.venv/bin/python scripts/live_check.py     # 登录后逐工具跑一遍（要账号）
./.venv/bin/ruff check .                     # 静态检查
bash -n install.sh                           # 安装脚本语法
```

`scripts/preflight.sh` 会依次跑：shell 语法 → 未提交改动 → 敏感信息（Cookie/JWT/CSRF/SSO）
→ 单元测试 → MCP 握手 → ruff。CI 在 Ubuntu 和 macOS 上各跑一遍同样的东西。

## 免责声明

本项目为个人开发的第三方工具，与 Garmin 官方无关，通过公开的网页接口读取**你自己账号**的数据。
请遵守 Garmin 的服务条款，不要用于批量抓取或商业用途。

## License

MIT
