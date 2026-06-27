@echo off
chcp 65001 >nul 2>&1
title 销售拜访 AI 助手

echo.
echo   ============================================
echo     销售拜访 AI 助手  v2.0
echo   ============================================
echo.
echo     启动地址: http://localhost:5000
echo     按 Ctrl+C 停止服务
echo   ============================================
echo.

cd /d "%~dp0"
python web_app.py
pause
