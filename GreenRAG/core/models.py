"""本地模型下载管理(嵌入模型)。

国内网络环境下下载 HuggingFace 模型的常见问题与应对:
  1. 默认开启 hf-mirror.com 镜像(可在「设置 → 网络」关闭);
  2. 下载走 huggingface_hub 的 snapshot_download,分文件、断点续传;
  3. 若镜像也失败,界面会提示 ModelScope(魔搭)备选下载命令;
  4. 模型保存在 data/models/ 下,与全局 huggingface 缓存隔离,便于迁移。
"""
from __future__ import annotations

import os
import threading
import time

from . import embed, paths

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"


def _direct_download_fallback(repo: str, dest, progress=None,
                              cancel_event: threading.Event | None = None) -> dict:
    """镜像直连兜底: 逐个文件下载(hf-mirror resolve URL)。

    huggingface_hub 走 API 握手在某些网络(代理/沙箱)下会超时,
    此时文件级直连通常仍然可用 —— 这是为大陆用户准备的"第二条路"。
    """
    import requests
    from huggingface_hub import constants as _hfc

    base = os.environ.get("HF_ENDPOINT", _hfc.ENDPOINT) or _hfc.ENDPOINT
    api = f"{base}/api/models/{repo}"
    headers = {"User-Agent": "GreenRAG/1.0"}
    try:
        r = requests.get(api, headers=headers, timeout=30)
        r.raise_for_status()
        files = [s["rfilename"] for s in r.json().get("siblings", [])]
    except Exception as e:
        raise RuntimeError(f"镜像文件列表获取失败: {e}") from e
    files = [f for f in files if not f.startswith(".")]

    size_total = 0
    size_map = {}
    # 先 HEAD 取总大小,便于进度显示
    for f in files:
        try:
            h = requests.head(f"{base}/{repo}/resolve/main/{f}",
                              headers=headers, timeout=20, allow_redirects=True)
            size_map[f] = int(h.headers.get("Content-Length") or 0)
            size_total += size_map[f]
        except Exception:
            size_map[f] = 0

    dest.mkdir(parents=True, exist_ok=True)
    got = 0
    for f in files:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("下载已取消")
        out = dest / f
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and size_map.get(f) and out.stat().st_size == size_map[f]:
            got += size_map[f]
            if progress:
                progress(got / max(1, size_total), f"校验 {f}")
            continue
        url = f"{base}/{repo}/resolve/main/{f}"
        try:
            with requests.get(url, headers=headers, timeout=(30, 180), stream=True) as resp:
                resp.raise_for_status()
                with open(out, "wb") as fh:
                    n = 0
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        fh.write(chunk)
                        n += len(chunk)
                        got += len(chunk)
                        if progress:
                            progress(got / max(1, size_total),
                                     f"下载 {f} {n/1024/1024:.1f}MB")
                        if cancel_event and cancel_event.is_set():
                            raise RuntimeError("下载已取消")
        except Exception as e:
            raise RuntimeError(f"文件 {f} 下载失败: {e}") from e
    return {"ok": True, "dir": str(dest)}


def download_model(repo: str = DEFAULT_MODEL, progress=None, cancel_event: threading.Event | None = None) -> dict:
    """下载模型到 data/models/<repo> 本地目录。
    progress(done_frac: float 0..1, msg: str) 可选回调。
    返回 {"ok": True, "dir": ..., "size": ...} 或抛 RuntimeError。
    """
    from huggingface_hub import snapshot_download
    dest = embed.model_local_path(repo)
    if (dest / "config.json").exists():
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
        return {"ok": True, "dir": str(dest), "size": size, "cached": True}

    def cb(frac: float, msg: str = ""):
        if progress:
            progress(max(0.0, min(1.0, frac)), msg)
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("下载已取消")

    paths.apply_hf_env()
    try:
        import tqdm as _tqdm_mod

        class _TqdmCb(_tqdm_mod.tqdm):
            def update(self, n=1):
                super().update(n)
                if self.total:
                    cb(self.n / self.total,
                       f"下载 {self.desc or ''} {self.n/1024/1024:.1f}/{self.total/1024/1024:.1f} MB")
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError("下载已取消")

        snapshot_download(
            repo_id=repo, local_dir=str(dest),
            tqdm_class=_TqdmCb, max_workers=4,
        )
    except Exception as e:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("下载已取消") from e
        # ---- 兜底: 直连镜像按文件逐个下载 ----
        print(f"[models] huggingface_hub 方式失败({str(e)[:80]}),"
              f"改用镜像直连下载…")
        try:
            cb(0, "使用镜像直连下载(备用通道)…")
            _direct_download_fallback(repo, dest, cb, cancel_event)
        except RuntimeError:
            raise
        except Exception as e2:
            raise RuntimeError(
                f"两种下载方式都失败了。\n最后错误: {e2}\n\n"
                "还可以用 ModelScope 魔搭下载(国内极速):\n"
                "  pip install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
                f"  python -c \"from modelscope import snapshot_download;"
                f" snapshot_download('{repo}', local_dir=r'{dest}')\""
                "\n完成后回到本应用,在「环境检查」页重新检测即可。"
            ) from e2

    if not (dest / "config.json").exists():
        raise RuntimeError("模型下载不完整(缺少 config.json),请重新下载。")
    # 仓库常同时发布 pytorch_model.bin 与 model.safetensors,保留一种即可
    if (dest / "model.safetensors").exists():
        try:
            (dest / "pytorch_model.bin").unlink(missing_ok=True)
        except OSError:
            pass
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    if size < 5 * 1024 * 1024:
        raise RuntimeError(f"模型文件不完整(仅 {size} 字节),请删除后重新下载。")
    return {"ok": True, "dir": str(dest), "size": size}


def delete_local_model(repo: str) -> dict:
    import shutil
    dest = embed.model_local_path(repo)
    if not dest.exists():
        return {"ok": False, "error": "本地不存在该模型"}
    embed._local_model["key"] = None   # 清进程内缓存引用
    shutil.rmtree(dest, ignore_errors=True)
    return {"ok": True}
