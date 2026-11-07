# 在 ZCode 里使用

ZCode 支持三种方式，任选一种。

## 方式 A：设置面板（最简单）

1. 打开 **设置 → MCP 服务器**
2. 点右上角 **新建 MCP 服务器**
3. 类型选 `stdio（本地命令）`
4. 命令填 `/绝对路径/garmin-mcp/.venv/bin/python`
5. 参数填 `/绝对路径/garmin-mcp/garmin_mcp.py`
6. 添加后确认开关是启用状态

或者切到 **完整配置** 模式，直接粘贴：

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

## 方式 B：一键导入

如果你已经在 **Claude Code** 或 **Codex** 里配好了，ZCode 可以直接导入：

**设置 → MCP 服务器 → 右上角「导入」** → 勾选 `garmin` → 导入

它会读取这些来源：

- Claude Code：`~/.claude/settings.json`
- Codex CLI：`~/.codex/config.toml`
- OpenCode：`~/.config/opencode/opencode.json`
- 通用：`~/.agents/mcp.json`

## 方式 C：直接改文件

用户级（所有工作区可用）：`~/.zcode/cli/config.json`

```json
{
  "mcp": {
    "servers": {
      "garmin": {
        "command": "/绝对路径/garmin-mcp/.venv/bin/python",
        "args": ["/绝对路径/garmin-mcp/garmin_mcp.py"],
        "env": {}
      }
    }
  }
}
```

工作区级（仅当前项目）：`<项目根>/.zcode/config.json`，结构相同。

## 注意

- ZCode 原生配置在 `.zcode` 下用 `mcp.servers`；`.agents/mcp.json` 则用通用的 `mcpServers`
- **同一作用域内 `.zcode` 优先**：只要 `.zcode` 里有任何 MCP，`.agents/mcp.json` 会被整体忽略
- 在面板里停用服务会写入 `"enable": false`
