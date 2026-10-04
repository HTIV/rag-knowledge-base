"""GreenRAG 桌面应用入口。

启动顺序:
  1. 在本机 127.0.0.1 上起一个本地 HTTP 服务(应用的所有功能都走它);
  2. 用 pywebview 打开一个原生桌面窗口(白绿主题界面);
  3. 若 pywebview 不可用(缺 WebView2 / pythonnet),自动降级:
     先用 Edge/Chrome 的 --app 模式开"单窗口应用",
     再不行就调用系统默认浏览器 —— 保证程序永远能跑起来。

用法:
  python app.py            # 正常启动(自动挑选可用窗口方式)
  python app.py --no-gui   # 仅本地服务(供测试/远程访问)
  python app.py --browser  # 强制用系统浏览器打开
"""
from __future__ import annotations

import argparse
import atexit
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url: str, timeout: float = 15) -> bool:
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url + "api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def find_browser_app() -> str | None:
    """找 Edge 或 Chrome 的可执行文件(用于 --app 无边框窗口)。"""
    import shutil
    cands = [
        os.environ.get("PROGRAMFILES", "") + r"\\Microsoft\\Edge\\Application\\msedge.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\\Microsoft\\Edge\\Application\\msedge.exe",
        os.environ.get("PROGRAMFILES", "") + r"\\Google\\Chrome\\Application\\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\\Google\\Chrome\\Application\\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    p = shutil.which("msedge") or shutil.which("chrome")
    return p


def launch_gui(url: str, mode: str, port: int) -> None:
    """按 mode 打开界面并阻塞,直到窗口关闭。"""
    full = f"{url}?port={port}" if False else url
    if mode == "webview":
        try:
            import webview
            window = webview.create_window(
                "GreenRAG · 混合检索知识库学习助手",
                full,
                width=1320, height=860,
                min_size=(1080, 700),
                background_color="#FFFFFF",
            )
            webview.start(gui=None, debug=False)
            print("[gui] 桌面窗口已关闭,程序退出。")
            return
        except Exception as e:
            print(f"[gui] pywebview 启动失败({e}),降级为浏览器窗口模式…")
    # 降级 1: Edge/Chrome --app 模式(看起来仍像桌面应用)
    exe = find_browser_app()
    if exe and not mode == "browser":
        try:
            print(f"[gui] 使用浏览器内核单窗口: {os.path.basename(exe)}")
            subprocess.Popen([exe, "--app=" + full, "--new-window"])
            return
        except Exception:
            pass
    # 降级 2: 系统默认浏览器
    webbrowser.open(full)
    print("[gui] 已在系统浏览器中打开界面(按 Ctrl+C 或关闭服务退出)。")


def main() -> None:
    ap = argparse.ArgumentParser(description="GreenRAG - 个人混合检索 RAG 学习助手")
    ap.add_argument("--no-gui", action="store_true", help="只启动本地服务,不打开窗口")
    ap.add_argument("--browser", action="store_true", help="强制使用系统默认浏览器")
    ap.add_argument("--port", type=int, default=0, help="指定端口(默认自动选择空闲端口)")
    args = ap.parse_args()

    # 冷启动准备: 让日志/依赖尽早暴露错误
    import server
    port = args.port or free_port()
    srv = server.start_server(port)
    url = f"http://127.0.0.1:{port}/"
    atexit.register(srv.shutdown)
    print(f"[GreenRAG] 本地服务已启动: {url}")

    if args.no_gui:
        print("[GreenRAG] --no-gui 模式: 请在浏览器访问上面的地址。Ctrl+C 退出。")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    if not wait_ready(url):
        print("[GreenRAG] 服务启动异常,请查看上方报错。")
        sys.exit(1)

    mode = "browser" if args.browser else "webview"
    launch_gui(url, mode, port)

    # 阻塞等待(浏览器模式关闭时由用户 Ctrl+C 退出)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
