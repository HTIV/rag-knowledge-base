"""路径与全局配置工具。

集中管理数据目录、镜像环境变量等,保证程序在任意工作目录启动都一致。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # 项目根目录
DATA_DIR = APP_DIR / "data"                                # 全部运行时数据
DOCS_DIR = DATA_DIR / "originals"                          # 上传文档的原始备份
MODELS_DIR = DATA_DIR / "models"                           # 本地嵌入/重排模型
HF_CACHE_DIR = DATA_DIR / "hf_cache"                       # huggingface 下载缓存
CONFIG_PATH = DATA_DIR / "config.json"

_LOCK = threading.Lock()
_DEFAULTS: dict = {}


def ensure_dirs() -> None:
    for d in (DATA_DIR, DOCS_DIR, MODELS_DIR, HF_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)


def defaults() -> dict:
    """返回一份全新默认配置(深拷贝使用方需自行 copy)。"""
    if not _DEFAULTS:
        _DEFAULTS.update({
            # ---------- 大模型(对话) ----------
            "llm": {
                "provider": "mock",            # mock|openai_compat
                "base_url": "",                # 如 https://api.deepseek.com/v1
                "api_key": "",
                "model": "deepseek-chat",
                "temperature": 0.3,
                "max_tokens": 1200,
                "history_turns": 2,            # 携带最近 N 轮对话
                "custom_system": "",           # 自定义系统提示词
            },
            # ---------- 嵌入模型 ----------
            "embedding": {
                "mode": "local",               # local|api
                "model": "BAAI/bge-small-zh-v1.5",
                "api_base": "",                # OpenAI 兼容 /embeddings
                "api_key": "",
                "api_model": "",
            },
            # ---------- 分块与检索 ----------
            "retrieval": {
                "chunk_size": 400,             # 每块目标字符数
                "chunk_overlap": 80,           # 相邻块重叠字符数
                "top_k": 6,                    # 最终送入大模型的片段数
                "bm25_top": 100,               # 混合检索: 关键词召回数量
                "dense_top": 100,              # 混合检索: 向量召回数量
                "rrf_k": 60,                   # RRF 融合常数
                "min_cosine": 0.32,            # 低于该相似度视为"知识库无相关内容"
                "kb_only": True,               # 仅依据知识库回答
            },
            # ---------- 本地 GGUF 模型(llama.cpp) ----------
            "gguf": {
                "model": "",                   # 选中的 .gguf 文件绝对路径
                "dir": "",                     # 自定义模型文件夹(默认 models/gguf)
                "layers": 0,                   # 卸载到 GPU 的层数, 0 = 纯 CPU
                "n_ctx": 4096,                 # 上下文长度
            },
            # ---------- 高级/网络 ----------
            "advanced": {
                "hf_mirror": True,             # 使用 hf-mirror.com 国内镜像下载模型
                "model_scope_fallback": True,  # HF 失败时提示 ModelScope 备用方案
                "llama_server_path": "",       # 手动指定 llama-server.exe(可选)
                "language": "zh",
            },
        })
    return _DEFAULTS


def _deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config() -> dict:
    """读取配置,与默认值深度合并,保证字段完整。"""
    cfg = json.loads(json.dumps(defaults()))
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _deep_merge(cfg, json.load(f))
        except Exception as e:  # 配置损坏时回退默认值
            print(f"[config] 配置读取失败,已使用默认配置: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    with _LOCK:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        tmp.replace(CONFIG_PATH)


def apply_hf_env(cfg: dict | None = None) -> None:
    """把国内镜像等环境变量写入 os.environ(必须在导入 torch/hub 前生效)。"""
    ensure_dirs()
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR / "hub"))
    # 国内镜像对 HF 新版 Xet 存储协议兼容性差,统一退回传统 HTTP 下载
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if cfg is None:
        cfg = load_config()
    if cfg.get("advanced", {}).get("hf_mirror", True):
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def data_dir() -> Path:
    ensure_dirs()
    return DATA_DIR


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)
