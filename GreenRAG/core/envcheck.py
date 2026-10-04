"""环境检查引擎。

把运行 GreenRAG 需要的每一项环境列成清单,逐项检测:
  · 基础:      Python 版本 / pip
  · 系统组件:  WebView2(桌面窗口) / VC++ 运行库(本地推理需要)
  · Python 包: flask、pymupdf、python-docx、torch、sentence-transformers...
  · 模型资源:  本地嵌入模型是否已下载
每项返回: 状态 ok / 缺什么 / 怎么做 —— 缺失项的 actions 里附
「国内镜像下载链接」与「可直接复制的一键安装命令」,新手照着点即可补齐。
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
import time
import winreg
from pathlib import Path

from . import embed, gpuinfo, paths

PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"

# (模块名, pip 包名, 中文说明)
PACKAGES = [
    ("flask", "flask", "后端 Web 服务框架"),
    ("fitz", "pymupdf", "PDF 解析(提取文字与页码)"),
    ("docx", "python-docx", "Word .docx 文档解析"),
    ("bs4", "beautifulsoup4", "网页/HTML 正文提取"),
    ("requests", "requests", "网页抓取与 API 调用"),
    ("jieba", "jieba", "中文分词(BM25 关键词检索)"),
    ("rank_bm25", "rank-bm25", "BM25 稀疏检索算法"),
    ("numpy", "numpy", "科学计算(向量/矩阵)"),
    ("torch", "torch", "PyTorch(本地嵌入模型推理引擎)"),
    ("sentence_transformers", "sentence-transformers", "句向量嵌入框架"),
    ("huggingface_hub", "huggingface_hub", "模型下载工具"),
    ("webview", "pywebview", "桌面窗口外壳(把网页包成桌面应用)"),
]

PIP_REQUIREMENTS = ("flask pymupdf python-docx beautifulsoup4 requests jieba "
                    "rank-bm25 numpy torch sentence-transformers pywebview")

MIRROR_COMMAND = ("python -m pip install -r requirements.txt "
                  f"-i {PYPI_MIRROR}")


def _act_url(label: str, url: str, primary: bool = False) -> dict:
    return {"label": label, "url": url, "primary": primary}


def _act_copy(label: str, text: str, primary: bool = True) -> dict:
    return {"label": label, "copy": text, "primary": primary}


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _pkg_version(pipname: str) -> str:
    try:
        from importlib import metadata
        return metadata.version(pipname)
    except Exception:
        return ""


def _reg_exists(path: str, subkey: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{path}\\{subkey}") as k:
            winreg.QueryValueEx(k, "")
            return True
    except OSError:
        return False


def _webview2_ok() -> bool:
    """检测 WebView2 Runtime(桌面窗口需要)。"""
    client = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"   # WebView2 Runtime 固定 GUID
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        for sub in (f"SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{client}",
                    f"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{client}"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub, 0, winreg.KEY_READ | view) as k:
                    val, _ = winreg.QueryValueEx(k, "pv")
                    if val:
                        return True
            except OSError:
                continue
    # 常见安装路径兜底
    for cand in (
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "EdgeWebView" / "Application",
    ):
        if cand.exists():
            return True
    return False


def _vcredist_ok() -> bool:
    sysdir = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    return (sysdir / "msvcp140.dll").exists() and (sysdir / "vcruntime140_1.dll").exists()


def run_checks() -> dict:
    """执行全部检查,返回 {summary: {...}, items: [...]}。"""
    cfg = paths.load_config()
    items: list[dict] = []

    # ================================================== 1) 基础: Python/pip
    ver = sys.version_info
    py_ok = ver >= (3, 10)
    items.append({
        "cat": "基础环境", "key": "python", "name": "Python 运行时",
        "desc": "本程序基于 Python 开发,需要 3.10 及以上(推荐 3.11 / 3.12)。"
                "下方均为独立安装包直链,不需要微软商店",
        "required": True, "ok": py_ok,
        "detail": f"当前: {platform.python_version()}"
                  + ("" if py_ok else " —— 版本过低或未安装")
                  + ("。若运行 python 时弹出微软商店,说明系统只有商店占位符,"
                     "请用右侧国内直链安装真实 Python(安装时勾选 Add to PATH,"
                     "并到『应用执行别名』关闭 python 占位)"),
        "actions": [
            _act_url("华为云直链(推荐,3.12.10 amd64)",
                     "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe", True),
            _act_url("npmmirror 直链(国内备用)",
                     "https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-amd64.exe", True),
            _act_url("Python 官网直链",
                     "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"),
        ],
    })

    pip_ok = shutil.which("pip") is not None or _have("pip")
    items.append({
        "cat": "基础环境", "key": "pip", "name": "pip(包管理器)",
        "desc": "安装 Python 库的工具", "required": True, "ok": pip_ok,
        "detail": "随 Python 一起安装" if pip_ok else "未找到 pip,请重新安装 Python 并勾选 Add pip",
        "actions": [
            _act_copy("复制升级命令(清华源)",
                      "python -m pip install --upgrade pip -i " + PYPI_MIRROR),
        ],
    })

    # ================================================== 2) 系统组件
    wv_ok = _webview2_ok()
    items.append({
        "cat": "系统组件", "key": "webview2", "name": "WebView2 运行时",
        "desc": "桌面原生窗口的内核(仅影响窗口外观;缺失时应用自动用系统自带 Edge 打开,不影响使用)",
        "required": False, "ok": wv_ok,
        "detail": ("已安装" if wv_ok else
                   "未检测到 —— 不是必需品: 应用会自动降级为 Edge 窗口模式继续用;"
                   "想补装请用右侧官方独立 exe(不走微软商店)"),
        "actions": [
            _act_url("官方独立安装包(exe,非商店)",
                     "https://go.microsoft.com/fwlink/p/?LinkId=2124703", True),
            _act_copy("winget 安装命令(备选)",
                      "winget install Microsoft.EdgeWebView2Runtime"),
        ],
    })

    vc_ok = _vcredist_ok()
    items.append({
        "cat": "系统组件", "key": "vcredist", "name": "Microsoft VC++ 运行库",
        "desc": "PyTorch 等本地推理库依赖的底层运行库",
        "required": True, "ok": vc_ok,
        "detail": "已安装" if vc_ok else "未检测到(运行本地嵌入模型会报错)",
        "actions": [
            _act_url("微软官方下载(x64,非商店)", "https://aka.ms/vs/17/release/vc_redist.x64.exe", True),
            _act_copy("winget 安装命令(备选)",
                      "winget install Microsoft.VCRedist.2015+.x64"),
        ],
    })

    # ================================================== 3) Python 包
    missing = []
    for mod, pipname, desc in PACKAGES:
        ok = _have(mod)
        ver_s = _pkg_version(pipname)
        if not ok:
            missing.append(pipname)
        items.append({
            "cat": "Python 依赖", "key": f"pkg_{mod}", "name": pipname,
            "desc": desc, "required": True, "ok": ok,
            "detail": f"已安装 {ver_s}" if ok else "未安装",
            "actions": [] if ok else [
                _act_url("清华 PyPI 镜像首页", PYPI_MIRROR, True),
                _act_copy("复制安装命令(清华源)",
                          f"python -m pip install {pipname} -i {PYPI_MIRROR}"),
            ],
        })

    if missing:
        items.append({
            "cat": "Python 依赖", "key": "all_pkgs", "name": "一键安装全部依赖",
            "desc": "项目根目录提供 requirements.txt,以下命令可一次装齐所有包",
            "required": True, "ok": False,
            "detail": f"缺少 {len(missing)} 个包: {', '.join(missing[:6])}"
                      + (" …" if len(missing) > 6 else ""),
            "actions": [
                _act_url("清华 PyPI 镜像首页", PYPI_MIRROR, True),
                _act_copy("一键安装命令(清华源,推荐)", MIRROR_COMMAND),
                _act_copy("官方源命令(镜像不可用时)",
                          "python -m pip install -r requirements.txt"),
            ],
        })

    # ================================================== 4) 模型资源
    repo = cfg.get("embedding", {}).get("model", "BAAI/bge-small-zh-v1.5")
    mdir = embed.model_local_path(repo)
    m_ok = mdir.exists() and (mdir / "config.json").exists()
    size = ""
    if m_ok:
        size = paths.fmt_bytes(sum(f.stat().st_size for f in mdir.rglob("*") if f.is_file()))
    mirror_on = cfg.get("advanced", {}).get("hf_mirror", True)
    items.append({
        "cat": "模型资源", "key": "embed_model", "name": f"嵌入模型 {repo}",
        "desc": "本地向量化模型(约 95MB,仅首次使用需下载,之后完全离线)",
        "required": True, "ok": m_ok and mirror_on is not None,
        "detail": (f"已下载 {size} · {'HF 国内镜像已开启' if mirror_on else 'HF 国内镜像已关闭'}"
                   if m_ok else
                   "未下载 —— 可在「设置 → 嵌入模型」点击『下载模型』(自动走国内镜像,约 95MB)"),
        "actions": [
            _act_url("hf-mirror 模型页(国内镜像)", f"{HF_MIRROR}/{repo}", True),
            _act_url("ModelScope 魔搭页(国内,备选)", f"https://modelscope.cn/models/{repo}", True),
            _act_copy("魔搭命令行下载(备选)",
                      f"pip install modelscope -i {PYPI_MIRROR}\n"
                      f"python -c \"from modelscope import snapshot_download;"
                      f"snapshot_download('{repo}', local_dir=r'{mdir}')\""),
        ],
    })

    # ================================================== 4b) 本地 GGUF 模型(可选组件)
    from . import gguf as gguf_mod
    srv = gguf_mod.find_server(cfg.get("advanced", {}).get("llama_server_path") or "")
    items.append({
        "cat": "模型资源", "key": "gguf_engine", "name": "GGUF 引擎(llama-server.exe)",
        "desc": "运行本地 GGUF 模型(如 Qwen)的官方预编译引擎,免编译、免商店;"
                "NVIDIA 显卡下载 win-cuda 包,否则下载 win-cpu 包;新版还需配套下载同版本"
                "cudart-llama-bin 运行库包 —— 两个包都解压放进 models/llama/cuda 即可自动识别",
        "required": False, "ok": bool(srv),
        "detail": (f"已找到: {srv['path']} · {srv['version']}"
                   if srv else
                   "未找到 —— 不影响在线 API / 演示模式;想本地跑 GGUF 时按右侧指引下载(约 17MB CPU / 140MB CUDA)"),
        "actions": [] if srv else [
            _act_url("GitHub 官方发布页(选最新 bXXXX)", "https://github.com/ggml-org/llama.cpp/releases", True),
            _act_copy("国内加速下载示例(ghproxy 系,自行替换)",
                      "把上面的发布页地址改为: https://ghfast.top/https://github.com/ggml-org/llama.cpp/releases"),
        ],
    })

    gpu = gpuinfo.detect()
    items.append({
        "cat": "模型资源", "key": "gguf_gpu", "name": "GPU 检测",
        "desc": "决定本地 GGUF 推理用 CPU 还是 CUDA 版引擎;有 NVIDIA 显卡可在设置里开启 GPU 卸载",
        "required": False, "ok": True,
        "detail": gpu["summary"],
        "actions": [
            _act_copy("复制 GPU 信息", gpu["summary"] + (" | " + gpu["cuda_hint"] if gpu else "")),
        ],
    })

    models_list = gguf_mod.scan_models(cfg.get("gguf", {}).get("dir") or "")
    items.append({
        "cat": "模型资源", "key": "gguf_model", "name": "GGUF 模型文件",
        "desc": "把你从网上下载的 .gguf 放进 models/gguf 文件夹即被识别"
                "(0.5B≈0.4GB / 1.5B≈1.1GB / 7B≈4.7GB,新手建议先用 0.5B 跑通流程)",
        "required": False, "ok": bool(models_list),
        "detail": ("已识别 " + str(len(models_list)) + " 个: " +
                   "、".join(m["name"] for m in models_list[:5]) +
                   (" …" if len(models_list) > 5 else "")
                   if models_list else "未找到模型文件"),
        "actions": [
            _act_url("hf-mirror 模型页(国内镜像)", "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF", True),
            _act_url("ModelScope 魔搭(国内)", "https://modelscope.cn/models?name=GGUF", True),
            _act_copy("下载 0.5B 示例模型命令(hf 镜像)",
                      f"python -c \"from huggingface_hub import hf_hub_download;"
                      f"hf_hub_download('Qwen/Qwen2.5-0.5B-Instruct-GGUF',"
                      f"'qwen2.5-0.5b-instruct-q4_k_m.gguf', local_dir=r'{str(gguf_mod.DEFAULT_GGUF_DIR)}')\""),
        ],
    })

    # ================================================== 5) 数据目录
    ok_dir = paths.DATA_DIR.exists()
    items.append({
        "cat": "数据目录", "key": "data_dir", "name": "数据目录(data/)",
        "desc": "文档、向量库、配置都保存在项目 data 目录,可整体备份迁移",
        "required": False, "ok": ok_dir,
        "detail": str(paths.DATA_DIR),
        "actions": [],
    })

    # ---------------- 汇总 ----------------
    req_items = [i for i in items if i["required"]]
    ok_count = sum(1 for i in items if i["ok"])
    req_ok = sum(1 for i in req_items if i["ok"])
    return {"summary": {"total": len(items),
                        "ok": ok_count,
                        "bad": len(items) - ok_count,
                        "req_total": len(req_items),
                        "req_ok": req_ok,
                        "ready": req_ok == len(req_items) and len(req_items) > 0,
                        "checked_at": time.time()},
            "items": items}
