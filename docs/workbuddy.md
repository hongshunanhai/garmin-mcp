# 在 WorkBuddy 里使用

## 方式 A：界面配置（推荐）

1. 左侧栏 **插件** → 右上角 **MCP 服务器** → **配置 MCP**
2. 在编辑器里粘贴下面的 JSON（把路径换成你自己的）
3. 保存后看状态灯：🟢 绿色表示连接成功，🔴 红色表示配置有误

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

## 方式 B：改配置文件

配置分两个级别：

| 级别 | 文件路径 | 适用 |
|---|---|---|
| 用户级 | `~/.workbuddy/mcp.json` | 所有项目复用 |
| 项目级 | `<项目目录>/.workbuddy/mcp.json` | 仅当前项目 |

内容与方式 A 的 JSON 相同。`./install.sh --client=workbuddy` 会自动写入用户级配置。

## 验证

在任务对话里直接说：

```
用 Garmin 查一下我本周跑了多少
```

## 注意

- WorkBuddy 的 MCP 配置是标准的 `mcpServers` 结构，和 Claude Code / ZCode 的 `.agents/mcp.json` 通用
- JSON 格式错误时状态灯会变红，优先检查括号和引号
- 登录窗口会从后台弹出，注意别把它当成弹窗广告关掉
