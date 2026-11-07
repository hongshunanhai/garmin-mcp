# 在 DeepSeek Harness（DSH）里使用

DSH 的 MCP 通过插件配置接入，有两种方式。

## 方式 A：一键脚本（推荐）

```bash
./install.sh --client=dsh
```

它会往你的 profile 里追加一段 `mcp-garmin` 配置（自动备份原文件），**重启 DSH 后生效**。

## 方式 B：手动追加

编辑 profile 的 patch 文件，一般在：

```
~/.dsh/profiles/<profile名>/cordis.patch.yml
```

在末尾追加：

```yaml
- id: mcp-garmin
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: garmin
    transport: stdio
    command: "/绝对路径/garmin-mcp/.venv/bin/python"
    args:
      - "/绝对路径/garmin-mcp/garmin_mcp.py"
    failOnStartupError: false
    toolCallTimeoutMs: 600000
```

重启 DSH，工具会出现在列表里，名字形如 `mcp__garmin__get_activity`。

## 方式 C：作为 bundle 安装

仓库里的 `dsh-bundle/` 是一个只含配置的 bundle，适合分发给别人：

1. 把 `dsh-bundle/cordis.patch.yml` 里的**三处**绝对路径（command、args、cwd）改成你机器上的值
2. 让 DSH 里的 agent 安装这个目录，或通过插件管理界面导入
3. 重启生效

## 注意

- `toolCallTimeoutMs` 建议给足（`login` 会等用户操作，默认 60 秒不够）
- 如果路径以后变了（比如移动了仓库目录），要同步改配置，否则启动会失败
- DSH 里登录窗口由 MCP 子进程拉起，和 DSH 本身无关；登录窗口关闭后凭据已落盘，工具可继续用
