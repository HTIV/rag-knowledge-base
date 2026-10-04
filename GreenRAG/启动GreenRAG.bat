@echo off
chcp 65001 >nul
title GreenRAG - RAG 学习助手
cd /d "%~dp0"

set "VPY="
if exist ".venv\Scripts\python.exe" set "VPY=%~dp0.venv\Scripts\python.exe"

if not defined VPY (
  rem 没有虚拟环境时,检查系统 python(跳过微软商店占位符,避免弹出商店)
  where python >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%i in ('where python 2^>nul') do (
      echo %%i | findstr /i "WindowsApps" >nul
      if errorlevel 1 (
        "%%i" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
        if not errorlevel 1 if not defined VPY set "VPY=%%i"
      )
    )
  )
)

if not defined VPY (
  echo.
  echo [提示] 没有找到可用的 Python 环境,请先双击「一键安装.bat」完成安装。
  echo        若刚才弹出微软商店,说明系统只有商店占位符 —— 一键安装脚本里
  echo        有国内直链安装包 ^(华为云 / npmmirror^),无需使用微软商店。
  echo.
  pause
  exit /b 1
)

echo 启动 GreenRAG ...
echo 提示: 首次启动会在「设置」页下载嵌入模型(约95MB,走国内镜像)
echo       关闭本窗口即可退出程序
echo.

"%VPY%" app.py

echo.
echo 程序已退出。
pause
