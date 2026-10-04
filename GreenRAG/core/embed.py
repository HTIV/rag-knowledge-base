"""嵌入引擎 (Embedding)。

把文本变成"向量"是 RAG 检索的核心: 语义相近的句子向量距离更近。

GreenRAG 提供两种嵌入方式(在「设置」页切换):
  1. local — 本地模型 BAAI/bge-small-zh-v1.5(默认,约 95MB,无需联网推理、无 API 费用;
             中文效果好、速度快,个人级知识库的首选);
  2. api   — 调用 OpenAI 兼容的 /embeddings 接口
             (适合没有本地 GPU 又想用更强模型,或企业已有 API 网关的情况;
              注: DeepSeek 官方 API 不提供 embedding,可选智谱/硅基流动/OpenAI 等)。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import requests

from . import paths

_MODEL_LOCK = threading.Lock()
_local_model = {"key": None, "model": None}   # 进程内缓存已加载的本地模型


def repo_to_dir(repo: str) -> Path:
    return paths.MODELS_DIR / repo.replace("/", "__")


def model_local_path(repo: str) -> Path:
    """本地模型目录(含 config.json 即视为已下载)。"""
    return repo_to_dir(repo)


def model_is_ready(repo: str) -> bool:
    return (model_local_path(repo) / "config.json").exists()


def local_model_info() -> dict:
    """已下载的本地模型列表(目录 + 大小)。"""
    out = []
    if paths.MODELS_DIR.exists():
        for d in sorted(paths.MODELS_DIR.iterdir()):
            if d.is_dir() and (d / "config.json").exists():
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                out.append({"repo": d.name.replace("__", "/"), "dir": str(d),
                            "size": size})
    return out


class EmbedError(Exception):
    """面向用户的嵌入错误。"""


# ================================================================== 本地模型

def _load_local(repo: str):
    path = model_local_path(repo)
    if not (path / "config.json").exists():
        raise EmbedError(
            f"本地嵌入模型尚未下载({repo})\n"
            "请在「设置 → 嵌入模型」点击『下载模型』,或到「环境检查」页查看下载说明。"
        )
    if _local_model["key"] == str(path):
        return _local_model["model"]
    with _MODEL_LOCK:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbedError("缺少 sentence-transformers / torch,请在环境检查页安装") from e
        try:
            print(f"[embed] 加载本地模型 {repo} ...")
            t0 = time.time()
            model = SentenceTransformer(str(path), device="cpu")
            print(f"[embed] 模型加载完成, 用时 {time.time()-t0:.1f}s, "
                  f"向量维度 {model.get_sentence_embedding_dimension()}")
        except Exception as e:
            raise EmbedError(f"本地模型加载失败: {e}") from e
        _local_model["key"] = str(path)
        _local_model["model"] = model
        return model


# ================================================================== API 模式

def _api_embed(cfg: dict, texts: list[str], batch: int = 32) -> list[np.ndarray]:
    base = (cfg.get("api_base") or "").rstrip("/")
    key = cfg.get("api_key") or ""
    model = cfg.get("api_model") or ""
    if not base or not model:
        raise EmbedError("API 嵌入未配置完整(base_url / 模型名必填),请到设置页检查。")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    out: list[np.ndarray] = []
    for i in range(0, len(texts), batch):
        payload = {"model": model, "input": texts[i:i + batch]}
        try:
            r = requests.post(f"{base}/embeddings", json=payload,
                              headers=headers, timeout=120)
        except requests.exceptions.RequestException as e:
            raise EmbedError(f"嵌入接口请求失败: {e}") from e
        if r.status_code >= 400:
            raise EmbedError(
                f"嵌入接口返回 {r.status_code}: {r.text[:300]}\n"
                "请检查: ①base_url 是否正确 ②该模型名是否存在于你的服务商 ③密钥是否有效"
            )
        try:
            items = r.json()["data"]
        except Exception:
            raise EmbedError(f"嵌入接口返回格式异常: {r.text[:200]}")
        items.sort(key=lambda x: x.get("index", 0))
        for it in items:
            out.append(np.asarray(it["embedding"], dtype=np.float32))
    if len(out) != len(texts):
        raise EmbedError("嵌入接口返回条数与请求不一致,请稍后重试。")
    return out


# ================================================================== 统一入口

def embed_texts(cfg: dict, texts: list[str], progress=None) -> np.ndarray:
    """返回 [N, dim] float32 且已归一化的向量矩阵。progress(ok, total) 可选回调。"""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    mode = cfg.get("mode", "local")
    batch = 32
    vectors: list[np.ndarray] = []
    total = (len(texts) + batch - 1) // batch
    done = 0
    if mode == "api":
        for i in range(0, len(texts), batch):
            vectors.extend(_api_embed(cfg, texts[i:i + batch]))
            done += 1
            if progress:
                progress(done, total)
        mat = np.vstack(vectors)
    else:  # local
        model = _load_local(cfg.get("model", "BAAI/bge-small-zh-v1.5"))
        for i in range(0, len(texts), batch):
            vecs = model.encode(
                texts[i:i + batch],
                batch_size=min(batch, 8),
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            vectors.append(np.asarray(vecs, dtype=np.float32))
            done += 1
            if progress:
                progress(done, total)
        mat = np.vstack(vectors)
    # 兜底归一化(API 返回的可能未归一化,余弦需要)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def embed_one(cfg: dict, text: str) -> np.ndarray:
    return embed_texts(cfg, [text])[0]


def test_embedding(cfg: dict, text: str = "知识库与检索增强生成") -> dict:
    t0 = time.time()
    try:
        vec = embed_one(cfg, text)
        return {"ok": True, "dim": int(vec.shape[0]), "ms": int((time.time() - t0) * 1000),
                "preview": [f"{x:.4f}" for x in vec[:3]]}
    except EmbedError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"未知错误: {e}"}


def readiness(cfg: dict) -> dict:
    """嵌入能力就绪状态(供前端展示横幅)。"""
    mode = cfg.get("mode", "local")
    repo = cfg.get("model", "BAAI/bge-small-zh-v1.5")
    if mode == "api":
        ok = bool(cfg.get("api_base") and cfg.get("api_model"))
        return {"mode": "api", "label": "API 嵌入", "ok": ok,
                "hint": "已配置" if ok else "未配置完整(需 base_url + 模型名)"}
    has_torch = _importable("torch") and _importable("sentence_transformers")
    ready = model_is_ready(repo) and has_torch
    return {"mode": "local", "label": f"本地 {repo}", "ok": ready,
            "hint": ("已就绪" if ready else
                     ("模型已下载,但缺少 torch/sentence-transformers"
                      if model_is_ready(repo) else "模型尚未下载(约 95MB,需联网下载一次)"))}


def _importable(mod: str) -> bool:
    try:
        import importlib
        importlib.import_module(mod)
        return True
    except Exception:
        return False
