# DSH bundle（可选安装方式）

这是一个「只含配置」的 bundle，供 DeepSeek Harness 使用。

**大多数用户不需要它** —— 直接跑根目录的 `./install.sh --client=dsh` 就行，
脚本会自动把正确的绝对路径写进你的 profile。

只有想把配置分发给别人时，才需要这个目录：

1. 修改 `cordis.patch.yml` 里三处 `/绝对路径/garmin-mcp/...`（command、args、cwd）
2. 让 DSH 安装这个目录，或用 `plugin_manager` 的 `install_bundle`
3. 重启 DSH
