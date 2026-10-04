"""混合检索 (Hybrid Retrieval) + 倒数排名融合 (RRF)。

单用某一种检索都有短板:
  - BM25(关键词/稀疏检索): 精确匹配术语、缩写、编号(如"GPU"、"第12条")很强,
    但对"换个说法"的同义表达无能为力;
  - 向量(稠密检索): 理解语义,却可能忽略精确关键词,且偶有"跑题"命中。

GreenRAG 的做法: 两种检索各取前 N 名,用 RRF 融合排名:
    RRF(chunk) = Σ 1/(k + rank_i)      (k 为融合常数,默认 60)
只有"两边都靠前"的片段才能拿到最高分,天然互补 —— 这就是混合检索。
"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

from . import db, embed

_CACHE = {"key": None, "metas": [], "matrix": None,
          "tokens": None, "bm25": None}
_CACHE_LOCK = threading.Lock()
_jieba_lock = threading.Lock()
_jieba = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba
            with _jieba_lock:
                jieba.initialize()
            _jieba = jieba
        except ImportError:
            _jieba = False
    return _jieba or None


def tokenize(text: str) -> list[str]:
    """中英文混合分词: 中文走 jieba 搜索模式,连续英文/数字串直接保留。"""
    jieba = _get_jieba()
    toks: list[str] = []
    if jieba is not None:
        for w in jieba.cut_for_search(text):
            if w.strip() and not re.fullmatch(r"[\W_]+", w):
                toks.append(w.lower())
    seen = set(toks)
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._+\-]{1,}", text):
        w = m.group().lower()
        if w not in seen:
            seen.add(w)
            toks.append(w)
    return toks


def invalidate() -> None:
    """文档增删后调用,缓存懒重建。"""
    with _CACHE_LOCK:
        _CACHE["key"] = None


def _ensure_index() -> None:
    n, m = db.corpus_version()
    key = f"{n}:{m}"
    if _CACHE["key"] == key:
        return
    with _CACHE_LOCK:
        if _CACHE["key"] == key:
            return
        metas, matrix = db.corpus()
        tokens = [tokenize(mt["text"]) for mt in metas]
        bm25 = None
        if tokens:
            try:
                from rank_bm25 import BM25Okapi
                bm25 = BM25Okapi(tokens)
            except Exception:
                bm25 = None
        _CACHE.update(key=key, metas=metas, matrix=matrix,
                      tokens=tokens, bm25=bm25)


def hybrid_search(query: str, retrieval_cfg: dict, embed_cfg: dict) -> dict:
    """混合检索入口(带真实向量嵌入)。

    返回:
      results: 最终 top_k 片段,每条含 来源文档/页码/两种排名/RRF分/余弦分/命中词
      detail : 耗时、查询分词、是否命中知识库(covered)
    """
    empty = {"results": [], "detail": {"dense_ms": 0, "bm25_ms": 0, "rrf_ms": 0,
                                       "query_tokens": [], "covered": True,
                                       "best_cosine": 0.0, "n_chunks": 0}}
    q = (query or "").strip()
    if not q:
        return empty
    _ensure_index()
    metas = _CACHE["metas"]
    n = len(metas)
    empty["detail"]["n_chunks"] = n
    if not metas:
        return empty

    top_k = max(1, int(retrieval_cfg.get("top_k", 6)))
    dense_top = int(retrieval_cfg.get("dense_top", 100))
    bm25_top = int(retrieval_cfg.get("bm25_top", 100))
    rrf_k = int(retrieval_cfg.get("rrf_k", 60))
    matrix = _CACHE["matrix"]
    bm25 = _CACHE["bm25"]
    q_tokens = tokenize(q)
    dense_scores = np.full(n, -1.0)   # 未被召回的片段为 -1
    bm25_scores = np.zeros(n)

    # ---------- 1) 稠密检索(向量) ----------
    dense_ranks: dict[int, int] = {}
    if matrix is not None and matrix.shape[0] == n:
        t_d = time.time()
        try:
            qv = embed.embed_one(embed_cfg, q)
            scores = matrix @ qv      # 向量已归一化 → 点积即余弦
            dense_scores = scores
            for rk, idx in enumerate(np.argsort(scores)[::-1][:dense_top]):
                dense_ranks[int(idx)] = rk
        except embed.EmbedError:
            pass   # 嵌入不可用(模型未下载/API 未配置)时,向量路直接跳过
        empty["detail"]["dense_ms"] = int((time.time() - t_d) * 1000)

    # ---------- 2) BM25(关键词) ----------
    bm25_ranks: dict[int, int] = {}
    if bm25 is not None and q_tokens:
        t_b = time.time()
        scores = bm25.get_scores(q_tokens)
        bm25_scores = scores
        for rk, idx in enumerate(np.argsort(scores)[::-1][:bm25_top]):
            if scores[idx] > 0:
                bm25_ranks[int(idx)] = rk
        empty["detail"]["bm25_ms"] = int((time.time() - t_b) * 1000)

    # ---------- 3) RRF 融合 ----------
    t_r = time.time()
    fused: dict[int, float] = {}
    for idx, rk in dense_ranks.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (rrf_k + rk)
    for idx, rk in bm25_ranks.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (rrf_k + rk)
    order = sorted(fused, key=lambda i: -fused[i])[:top_k]
    empty["detail"]["rrf_ms"] = int((time.time() - t_r) * 1000)

    # ---------- 4) 组装结果 ----------
    results: list[dict] = []
    best_cosine = 0.0
    toksets = _CACHE["tokens"] or []
    for idx in order:
        mt = metas[idx]
        cos = float(dense_scores[idx]) if dense_scores[idx] >= 0 else None
        best_cosine = max(best_cosine, cos or 0.0)
        tokset = set(toksets[idx]) if idx < len(toksets) else set()
        hit, seen_h = [], set()
        for w in q_tokens:
            if w in tokset and w not in seen_h:
                seen_h.add(w)
                hit.append(w)
            if len(hit) >= 6:
                break
        results.append({
            "id": int(mt["id"]), "doc_id": int(mt["doc_id"]),
            "doc_name": mt["doc_name"], "doc_kind": mt["doc_kind"],
            "seq": int(mt["seq"]), "page": mt["page"], "text": mt["text"],
            "dense_rank": dense_ranks.get(idx),
            "bm25_rank": bm25_ranks.get(idx),
            "rrf": round(fused[idx], 4),
            "dense_score": round(cos, 4) if cos is not None else None,
            "bm25_score": round(float(bm25_scores[idx]), 2) if idx in bm25_ranks else None,
            "hit_tokens": hit,
        })
    empty["results"] = results
    empty["detail"]["query_tokens"] = q_tokens[:20]
    empty["detail"]["covered"] = best_cosine >= float(retrieval_cfg.get("min_cosine", 0.32))
    empty["detail"]["best_cosine"] = round(best_cosine, 4)
    return empty
