# 在 Claude Code 里使用

## 方式 A：命令行（推荐）

```bash
claude mcp add --scope user garmin -- \
  /绝对路径/garmin-mcp/.venv/bin/python \
  /绝对路径/garmin-mcp/garmin_mcp.py
```

`--scope user` 表示对所有项目生效。查看是否成功：

```bash
claude mcp list
```

应该能看到 `garmin`。

## 方式 B：改配置文件

用户级配置文件是 `~/.claude.json`，在里面加：

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

> 改之前先备份：`cp ~/.claude.json ~/.claude.json.bak`

## 方式 C：只给某个项目用

在项目根目录建 `.mcp.json`：

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

## 验证

新开一个会话，输入：

```
我这个月跑了多少公里
```

它会调用 `get_period_stats`。工具名显示为 `mcp__garmin__get_period_stats`。

第一次调用如果没登录，会**自动弹出一个 Chrome 窗口**让你登录佳明；登录完窗口自动关闭，
刚才那次调用会继续执行。如果客户端等不及超时了，重问一次即可（凭据已经存好了）。

## 注意

- `command` **必须是绝对路径**，`~` 不会展开
- 路径含空格时用引号包住
- 修改配置后需要重开会话
- 登录窗口需要在 5 分钟内完成，超时会失败
