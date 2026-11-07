# 在 Codex CLI 里使用

## 方式 A：配置文件（通用）

编辑 `~/.codex/config.toml`（没有就新建），追加：

```toml
[mcp_servers.garmin]
command = "/绝对路径/garmin-mcp/.venv/bin/python"
args = ["/绝对路径/garmin-mcp/garmin_mcp.py"]
```

登录工具会等用户在浏览器里操作，建议把超时放宽：

```toml
[mcp_servers.garmin]
command = "/绝对路径/garmin-mcp/.venv/bin/python"
args = ["/绝对路径/garmin-mcp/garmin_mcp.py"]
startup_timeout_sec = 30.0
tool_timeout_sec = 600.0
```

## 方式 B：命令行

较新版本的 Codex 支持：

```bash
codex mcp add garmin -- /绝对路径/garmin-mcp/.venv/bin/python /绝对路径/garmin-mcp/garmin_mcp.py
```

版本不支持时用方式 A。

## 验证

```bash
codex
```

然后在里面问：

```
我昨天跑了多少
```

也可以 `codex mcp list` 查看已注册的服务器。

## 注意

- TOML 里的路径同样必须是绝对路径
- 表名固定为 `[mcp_servers.<名字>]`
- Windows 上路径要写成 `C:/Users/xxx/...` 或转义反斜杠
