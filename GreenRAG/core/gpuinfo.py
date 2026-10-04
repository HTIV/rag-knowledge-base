"""GPU 检测: 识别显卡型号与显存,决定 GGUF 本地模型的卸载策略。

检测顺序:
  1. nvidia-smi(如果有 NVIDIA 驱动,信息最准: 型号 / 显存 / 驱动版本);
  2. Windows 管理接口(WMI)枚举显示适配器(兼容 AMD/Intel,只给型号);
只读本机信息,不安装任何驱动。
"""
from __future__ import annotations

import subprocess
import threading

_CACHE: dict = {}
_LOCK = threading.Lock()


def _run(cmd: list[str], timeout: float = 12) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        return (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    except Exception:
        return ""


def _via_nvidia_smi() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits"])
    gpus: list[dict] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 1 and parts[0]:
            gpus.append({
                "name": parts[0],
                "vram_gb": round(float(parts[1]) / 1024, 1) if len(parts) > 1
                           and parts[1].replace(".", "", 1).isdigit() else None,
                "driver": parts[2] if len(parts) > 2 else "",
            })
    return gpus


def _via_wmi() -> list[dict]:
    """powershell 枚举 Win32_VideoController(兼容无 nvidia-smi 的环境)。"""
    ps = ("Get-CimInstance Win32_VideoController | "
          "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress")
    out = _run(["powershell", "-NoProfile", "-NonInteractive",
                "-Command", ps], timeout=15)
    gpus: list[dict] = []
    try:
        import json
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        for d in data:
            name = (d.get("Name") or "").strip()
            if not name:
                continue
            ram = d.get("AdapterRAM")
            vram_gb = round(int(ram) / 1073741824, 1) if isinstance(ram, int) and ram > 0 else None
            if vram_gb is not None and vram_gb > 64:
                vram_gb = None  # WMI 32 位溢出时丢弃
            gpus.append({"name": name, "vram_gb": vram_gb, "driver": ""})
    except Exception:
        pass
    return gpus


def detect(refresh: bool = False) -> dict:
    """返回: {ok, gpus:[{name,vram_gb,driver}], summary, cuda_build_hint}"""
    with _LOCK:
        if _CACHE and not refresh:
            return dict(_CACHE)
        gpus = _via_nvidia_smi()
        via = "nvidia-smi"
        if not gpus:
            gpus = _via_wmi()
            via = "wmi"
        # 只看"独立/可加速"类,忽略常见集显? 保留全部但标注
        nvidia = [g for g in gpus if "nvidia" in g["name"].lower()]
        has_cuda = bool(nvidia) and any(g.get("driver") for g in nvidia) is not False
        total_vram = sum(g["vram_gb"] or 0 for g in gpus if g.get("vram_gb"))
        summary = ""
        if nvidia:
            names = "、".join(g["name"] for g in nvidia)
            vr = f" 显存合计约 {total_vram:.1f}GB" if total_vram else ""
            summary = f"检测到 NVIDIA GPU: {names}{vr} —— 推荐下载 CUDA 版 llama-server,可开启 GPU 卸载"
            cuda_hint = "cuda"
        elif gpus:
            names = "、".join(g["name"] for g in gpus)
            summary = (f"检测到显卡(非 NVIDIA): {names} —— llama.cpp 官方 Windows 包"
                       "目前以 CPU/CUDA 为主,建议先用 CPU 版;也可自行尝试 Vulkan 版")
            cuda_hint = "cpu"
        else:
            summary = "未检测到可用 GPU —— 将使用 CPU 推理(小模型也可接受,速度较慢)"
            cuda_hint = "cpu"
        _CACHE.update({"ok": True, "gpus": gpus, "via": via,
                       "nvidia": nvidia, "total_vram_gb": total_vram,
                       "has_cuda_gpu": bool(nvidia),
                       "summary": summary, "cuda_hint": cuda_hint})
        return dict(_CACHE)


def nvidia_only() -> list[dict]:
    return detect()["nvidia"]


def best_gpu_layers_cap(vram_gb: float | None, model_mb: int | None = None) -> int:
    """粗略给出可卸载层数的建议上限(纯经验值,仅供 UI 展示)。"""
    if not vram_gb:
        return 0
    # GGUF 量化模型约 4bit/权重,7B≈4GB 权重;留 1~1.5GB 给上下文
    usable = vram_gb - 1.2
    if usable <= 0.3:
        return 0
    if model_mb:
        weight_gb = model_mb / 1024
        if weight_gb <= usable:
            return 999   # 全部卸载
    # 无法精确换算层数,给个保守上限
    return min(99, max(16, int(usable * 12)))


# ---------------------------------------------------------------- 实时遥测

_LIVE_CACHE: dict = {"at": 0.0, "data": None}


def live_stats(ttl: float = 1.2, force: bool = False) -> dict:
    """实时读取 NVIDIA 显卡状态(仅本机 nvidia-smi,开销很小,带缓存)。

    返回 {"ok": true, "gpus": [{name, util_pct, vram_used_gb, vram_total_gb,
                                 temp_c, driver}], "source": "nvidia-smi"}
    非 NVIDIA / 无独显时返回空列表,由前端降级显示。
    """
    import time as _t
    now = _t.time()
    if _LIVE_CACHE["data"] and not force and now - _LIVE_CACHE["at"] < ttl:
        return dict(_LIVE_CACHE["data"])
    out = {"ok": True, "gpus": [], "source": "nvidia-smi"}
    raw = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,"
                            "temperature.gpu,driver_version",
                "--format=csv,noheader,nounits"], timeout=6)
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6 or not parts[0]:
            continue

        def _num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None

        name = parts[0]
        util = _num(parts[1])
        mem_used_mb = _num(parts[2])
        mem_total_mb = _num(parts[3])
        temp = _num(parts[4])
        out["gpus"].append({
            "name": name,
            "util_pct": round(util) if util is not None else None,
            "vram_used_gb": round(mem_used_mb / 1024, 1) if mem_used_mb is not None else None,
            "vram_total_gb": round(mem_total_mb / 1024, 1) if mem_total_mb is not None else None,
            "temp_c": round(temp) if temp is not None else None,
            "driver": parts[5],
        })
    _LIVE_CACHE.update(at=now, data=out)
    return dict(out)
