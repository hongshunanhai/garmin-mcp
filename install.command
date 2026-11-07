#!/bin/zsh
# macOS 双击运行：等价于 ./install.sh
cd "$(dirname "$0")"
./install.sh "$@"
echo ""
read -r "?按回车键关闭…"
