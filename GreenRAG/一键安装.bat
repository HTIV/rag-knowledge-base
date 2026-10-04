@echo off
chcp 65001 >nul
title GreenRAG - 一键安装依赖
cd /d "%~dp0"

echo ============================================================
echo   GreenRAG 一键安装
echo   自动: 寻找 Python → 创建虚拟环境 → 用国内镜像装齐依赖
echo   说明: 全程不需要微软商店;所有下载走国内镜像/官网直链
echo ============================================================
echo.

rem ==================== 1. 寻找 Python 3.10+ ====================
rem 搜索顺序: ① 常见安装目录(独立安装包装的位置)
rem          ② py 启动器   ③ PATH 中的真实 python(排除商店占位)
set "PYEXE="

rem ---- ① 常见安装目录 ----
for %%D in (
  "%LOCALAPPDATA%\Programs\Python\Python313"
  "%LOCALAPPDATA%\Programs\Python\Python312"
  "%LOCALAPPDATA%\Programs\Python\Python311"
  "%LOCALAPPDATA%\Programs\Python\Python310"
  "%ProgramFiles%\Python313"
  "%ProgramFiles%\Python312"
  "%ProgramFiles%\Python311"
  "%ProgramFiles%\Python310"
  "%ProgramFiles(x86)%\Python313"
  "%ProgramFiles(x86)%\Python312"
  "%ProgramFiles(x86)%\Python311"
  "%ProgramFiles(x86)%\Python310"
) do (
  if not defined PYEXE if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
)
if defined PYEXE goto :vercheck

rem ---- ② py 启动器(取它背后真实 python 的路径) ----
if not defined PYEXE (
  where py >nul 2>nul
  if not errorlevel 1 (
    for /f "usebackq delims=" %%i in (`py -3.12 -c "import sys;print(sys.executable)" 2^>nul`) do set "PYEXE=%%i"
    if not defined PYEXE for /f "usebackq delims=" %%i in (`py -3.11 -c "import sys;print(sys.executable)" 2^>nul`) do set "PYEXE=%%i"
    if not defined PYEXE for /f "usebackq delims=" %%i in (`py -3.10 -c "import sys;print(sys.executable)" 2^>nul`) do set "PYEXE=%%i"
    if not defined PYEXE for /f "usebackq delims=" %%i in (`py -3.13 -c "import sys;print(sys.executable)" 2^>nul`) do set "PYEXE=%%i"
  )
)
if defined PYEXE goto :vercheck

rem ---- ③ PATH 中的 python(跳过微软商店占位符 WindowsApps) ----
if not defined PYEXE (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
      "%%i" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
      if not errorlevel 1 if not defined PYEXE set "PYEXE=%%i"
    )
  )
)
if defined PYEXE goto :vercheck

rem ---- 全部没找到: 给出国内直链安装包指引 ----
echo [错误] 没有找到可用的 Python(需要 3.10 及以上)。
echo.
echo 如果你看到"微软商店"弹出 —— 说明系统里只有商店的 Python 占位快捷方式,
echo 而商店在你电脑上不可用。请不要使用商店,直接下载下面的独立安装包:
echo.
echo   [推荐·华为云镜像]
echo     https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe
echo.
echo   [备用·npmmirror 国内镜像]
echo     https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-amd64.exe
echo.
echo   [备用·Python 官网直链]
echo     https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
echo.
echo 安装步骤:
echo   1. 双击下载的安装包,务必勾选 Add python.exe to PATH;
echo   2. 开始菜单搜索"应用执行别名",把 python.exe / python3.exe 两个开关关掉
echo      ^(否则以后在命令行输入 python 还会弹商店^);
echo   3. 重新双击本脚本即可继续。
echo.
pause
exit /b 1

:vercheck
rem ---- 校验版本 ----
"%PYEXE%" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [错误] 找到的 Python 版本过低: "%PYEXE%"
  echo 请下载 3.12 版本安装包 ^(见上方国内直链^),或卸载旧版本后重装。
  pause
  exit /b 1
)
echo [1/4] 使用 Python: "%PYEXE%"
"%PYEXE%" --version

rem ==================== 2. 创建虚拟环境 ====================
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] 创建虚拟环境 .venv ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 (
    echo [错误] 虚拟环境创建失败,请检查上方输出。
    pause
    exit /b 1
  )
) else (
  echo [2/4] 虚拟环境已存在,跳过创建
)

set "VPY=%~dp0.venv\Scripts\python.exe"

rem ==================== 3. 升级 pip ====================
echo [3/4] 升级 pip(清华镜像)...
"%VPY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

rem ==================== 4. 安装依赖 ====================
echo [4/4] 安装依赖(清华镜像,首次约 5~15 分钟,请耐心等待)...
"%VPY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo.
  echo [提示] 清华镜像安装失败,自动改用官方源重试...
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络,或把上方错误截图反馈。
    pause
    exit /b 1
  )
)

echo.
echo ============================================================
echo   安装完成 ✓  现在双击「启动GreenRAG.bat」即可运行
echo ============================================================
pause
