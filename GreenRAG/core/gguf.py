"""GGUF 本地大模型引擎(基于 llama.cpp 官方 llama-server.exe)。

为什么用 llama-server 而不是 llama-cpp-python?
  · 官方在每个 Windows 版本发布即用二进制(CPU / CUDA 12.x / 13.x / Vulkan...),
    免编译、不受 Python 版本限制 —— 新手下载解压就能用;
  · llama-server 自带 OpenAI 兼容接口(/v1/chat/completions),
    GreenRAG 可以复用整套"检索 + 引用标注 + 流式"链路,零额外依赖;
  · GPU 卸载 = 启动参数 --n-gpu-layers N(把 N 层模型权重放进显存,0 = 纯 CPU)。

工作方式:
  设置页选择 GGUF 文件 + GPU 卸载层数后,首次问答时本模块自动:
  找引擎 → 拉起 llama-server 子进程 → 轮询 /health 直到就绪 → 开始流式问答;
  引擎在退出程序、更换模型/参数、或显式点击"停止"时被释放。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import gpuinfo, paths

DEFAULT_GGUF_DIR = Path(paths.APP_DIR) / "models" / "gguf"      # 默认放 GGUF 的位置
DEFAULT_LLAMA_DIR = Path(paths.APP_DIR) / "models" / "llama"    # 默认放 llama-server.exe 的位置

_LOCK = threading.Lock()
_FIND: dict = {"at": 0.0, "explicit": "", "result": None}   # 引擎查找缓存(10s)
_RUN: dict = {"proc": None, "sig": "", "port": 0,
              "log_path": None, "started_at": 0.0,
              "model": "", "layers": 0, "ctx": 0, "server": ""}


class GGUFError(Exception):
    """面向用户的中文错误。"""


# ---------------------------------------------------------------- 模型扫描

def scan_models(custom_dir: str = "") -> list[dict]:
    """列出 GGUF 模型文件: 默认目录 + 用户自定义目录(去重)。"""
    dirs: list[Path] = []
    for d in (DEFAULT_GGUF_DIR, Path(custom_dir).expanduser() if custom_dir else None):
        if d and d.is_dir() and d.resolve() not in [x.resolve() for x in dirs]:
            dirs.append(d)
    out: list[dict] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            files = sorted(list(d.glob("*.gguf")) + list(d.glob("*.GGUF")))
        except OSError:
            continue
        for f in files:
            key = str(f.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append({"path": str(f), "name": f.name,
                        "size_mb": round(f.stat().st_size / 1048576, 1)})
    out.sort(key=lambda x: x["name"].lower())
    return out


def pick_model(path: str | None) -> dict | None:
    """校验配置里选择的模型是否可用。"""
    if not path:
        return None
    p = Path(path)
    if not p.exists() or p.suffix.lower() != ".gguf":
        return None
    return {"path": str(p), "name": p.name,
            "size_mb": round(p.stat().st_size / 1048576, 1)}


# ---------------------------------------------------------------- 引擎定位

def find_server(explicit: str = "") -> dict | None:
    """寻找 llama-server.exe: ①设置里填的路径 ②models/llama 及其子目录
    ③PATH 环境变量。返回 {path, source, version} 或 None。"""
    def ver(exe: str) -> str:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=10, creationflags=0x08000000)
            return (r.stdout or r.stderr).strip().splitlines()[0][:60] if (r.stdout or r.stderr) else ""
        except Exception:
            return ""

    now = time.time()
    if (_FIND.get("at") and now - _FIND["at"] < 10
            and _FIND["explicit"] == explicit):
        return dict(_FIND["result"]) if _FIND["result"] else None

    cands: list[tuple[str, str]] = []
    if explicit and Path(explicit).is_file():
        cands.append((explicit, "手动指定"))

    # 引擎优先级: 有 NVIDIA 显卡 → 优先 CUDA/Vulkan 子目录; 否则 → 根目录 CPU 版
    if DEFAULT_LLAMA_DIR.is_dir():
        has_nvidia = bool(gpuinfo.nvidia_only())
        found: list[tuple[int, str, str]] = []
        try:
            for sub in DEFAULT_LLAMA_DIR.iterdir():
                if sub.is_dir():
                    f = sub / "llama-server.exe"
                    if f.is_file():
                        low = sub.name.lower()
                        if "cuda" in low:
                            found.append((0, str(f), f"models/llama/{sub.name}(CUDA)"))
                        elif "vulkan" in low:
                            found.append((1, str(f), f"models/llama/{sub.name}(Vulkan)"))
                        else:
                            found.append((3, str(f), f"models/llama/{sub.name}"))
            for f in (DEFAULT_LLAMA_DIR / "llama-server.exe",
                      DEFAULT_LLAMA_DIR / "bin" / "llama-server.exe"):
                if f.is_file():
                    found.append((2 if has_nvidia else 1, str(f), "models/llama"))
        except OSError:
            pass
        if has_nvidia:
            found.sort(key=lambda x: x[0])     # CUDA 0 > Vulkan 1 > 根目录 2 > 其它 3
        else:
            found.sort(key=lambda x: 0 if x[0] == 1 else x[0] + 4)
        if found:
            cands.append((found[0][1], found[0][2]))
    if not cands:
        w = shutil.which("llama-server")
        if w:
            cands.append((w, "PATH"))
    if not cands:
        _FIND.update(at=now, explicit=explicit, result=None)
        return None
    exe = cands[0][0]
    result = {"path": exe, "source": cands[0][1], "version": ver(exe)}
    _FIND.update(at=now, explicit=explicit, result=result)
    return dict(result)


# ---------------------------------------------------------------- 进程管理

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def status(cfg: dict) -> dict:
    """综合状态(设置页展示用),不启动任何进程。cfg = 完整配置。"""
    gg = cfg.get("gguf", {})
    model = pick_model(gg.get("model") or "")
    srv = find_server((cfg.get("advanced", {}).get("llama_server_path") or ""))
    gpu = gpuinfo.detect()
    layers_cfg = int(gg.get("layers", 0) or 0)
    with _LOCK:
        running = _RUN["proc"] is not None and _RUN["proc"].poll() is None
        run = None
        if running:
            run = {k: _RUN[k] for k in ("sig", "port", "started_at", "model",
                                        "layers", "ctx", "server")}
            try:
                run["pid"] = _RUN["proc"].pid
            except Exception:
                run["pid"] = None
    return {
        "model": model,
        "models": scan_models(gg.get("dir") or ""),
        "dir": str(DEFAULT_GGUF_DIR),
        "custom_dir": gg.get("dir") or "",
        "server": srv,
        "gpu": {"nvidia": gpu["nvidia"], "total_vram_gb": gpu["total_vram_gb"],
                "summary": gpu["summary"], "cuda_hint": gpu["cuda_hint"]},
        "layers_cfg": layers_cfg,
        "running": running,
        "running_detail": run,
        "ready": bool(model and srv),
    }


def _sig(cfg: dict) -> str:
    gg = cfg.get("gguf", {})
    adv = cfg.get("advanced", {})
    return "|".join([str(gg.get("model") or ""), str(gg.get("layers") or 0),
                     str(gg.get("n_ctx") or 4096), str(adv.get("llama_server_path") or "")])


def ensure_running(cfg: dict, wait_timeout: int = 900) -> dict:
    """确保 llama-server 已按当前配置运行(首次启动需要加载模型,较慢)。"""
    gg = cfg.get("gguf", {})
    model = pick_model(gg.get("model") or "")
    if not model:
        raise GGUFError(
            "还没有选择 GGUF 模型: 请先把 .gguf 文件放进 models\\gguf 文件夹,"
            "然后在「设置 → 大模型」选择「本地 GGUF」并勾选模型文件。")
    srv = find_server((cfg.get("advanced", {}).get("llama_server_path") or ""))
    if not srv:
        raise GGUFError(
            "未找到 llama-server.exe(本地大模型的推理引擎)。\n"
            "请到「环境检查」页查看 GGUF 引擎条目的下载指引: "
            "下载 llama.cpp 官方 Windows 版(有 NVIDIA 显卡选 CUDA 版,否则选 CPU 版),"
            "解压后把 llama-server.exe 放进 models\\llama 文件夹即可。")

    layers = int(gg.get("layers", 0) or 0)
    n_ctx = int(gg.get("n_ctx", 4096) or 4096)
    gpu = gpuinfo.detect()
    effective = layers
    if layers > 0 and not gpu["nvidia"]:
        effective = 0   # 没有 NVIDIA 显卡时 CUDA 卸载无从谈起,自动退回 CPU

    sig = "|".join([model["path"], str(effective), str(n_ctx), srv["path"]])
    with _LOCK:
        proc = _RUN["proc"]
        if proc is not None and proc.poll() is None and _RUN["sig"] == sig:
            return {"port": _RUN["port"], "model": model["name"],
                    "layers": effective, "ctx": n_ctx, "fresh": False}
        if proc is not None:
            _stop_locked()
        port = _free_port()
        log_path = paths.DATA_DIR / "logs" / f"llama-server-{int(time.time())}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        alias = Path(model["path"]).name
        cmd = [srv["path"], "-m", model["path"],
               "--host", "127.0.0.1", "--port", str(port),
               "-c", str(n_ctx),
               "--alias", alias,
               "--log-file", str(log_path)]
        if effective > 0:
            cmd += ["--n-gpu-layers", str(effective)]
        cmd += ["--no-webui"]
        try:
            proc = subprocess.Popen(cmd, creationflags=0x08000000)  # 无控制台窗口
        except Exception as e:
            raise GGUFError(f"无法启动 llama-server: {e}") from e
        _RUN.update(proc=proc, sig=sig, port=port, log_path=str(log_path),
                    started_at=time.time(), model=model["name"],
                    layers=effective, ctx=n_ctx, server=srv["path"])

    # 轮询就绪
    base = f"http://127.0.0.1:{port}"
    t0 = time.time()
    last_err = ""
    while time.time() - t0 < wait_timeout:
        if proc.poll() is not None:
            last_err = _tail_log(_RUN["log_path"])
            raise GGUFError(f"llama-server 启动后立即退出。\n{last_err}")
        try:
            import urllib.request
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    return {"port": port, "model": model["name"],
                            "layers": effective, "ctx": n_ctx, "fresh": True}
        except Exception:
            pass
        time.sleep(1.0)
    raise GGUFError("llama-server 就绪超时(模型加载过慢?)。\n"
                    f"日志: {_RUN['log_path']}\n{_tail_log(_RUN['log_path'])}")


def _tail_log(path: str | None, n: int = 8) -> str:
    if not path:
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])[:1500]
    except Exception:
        return ""


def _stop_locked() -> None:
    proc = _RUN.get("proc")
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass
    _RUN.update(proc=None, sig="", port=0, log_path=None)


def stop() -> dict:
    """主动停止本地模型(释放显存/内存)。"""
    with _LOCK:
        if _RUN["proc"] is None or _RUN["proc"].poll() is not None:
            _RUN.update(proc=None)
            return {"ok": True, "stopped": False}
        _stop_locked()
        return {"ok": True, "stopped": True}


def stop_all() -> None:
    """程序退出时调用。"""
    with _LOCK:
        _stop_locked()


def sync_on_config_change(old: dict, new: dict) -> None:
    """配置保存后调用: 换模型/参数/引擎路径 → 重启; 换走 GGUF → 停止。"""
    if new.get("llm", {}).get("provider") != "gguf":
        stop()
        return
    if _sig(old) != _sig(new):
        with _LOCK:
            if _RUN["proc"] is not None:
                _stop_locked()
