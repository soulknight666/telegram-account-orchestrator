#!/usr/bin/env bash
# Telegram 账号管理器 · 一键配置并启动（macOS / Linux）
# 自动装依赖、写配置；启动前可选：自己电脑/服务器、网页/机器人。
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo
  echo "  [X] 没有找到 Python 3。"
  echo "  macOS:  brew install python"
  echo "  Ubuntu: sudo apt install python3 python3-venv"
  echo
  exit 1
fi

echo
echo "  Telegram 账号管理器 · 一键启动"
echo "  ----------------------------------------"
echo "  自动：环境检查 → 装依赖 → 生成密钥 → 体检"
echo "  可选：自己电脑/服务器 · 网页/机器人/双端"
echo "  停止服务请按 Ctrl+C"
echo "  ----------------------------------------"
echo "  也可跳过菜单直接指定，例如："
echo "    ./start.sh --deploy local --frontend web"
echo "    ./start.sh --deploy server --frontend both --token"
echo "  ----------------------------------------"
echo

exec "$PY" setup.py --auto "$@"
