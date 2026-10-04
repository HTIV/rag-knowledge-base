"""SQLite 存储层。

个人级 RAG 不需要外部数据库,SQLite 单文件即可保存:
  - docs   文档元信息(文件名/类型/原始备份路径/来源URL)
  - chunks 分块文本 + 向量(blob)
  - meta   键值元信息(例如"最后使用的分块参数")
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Iterator

import numpy as np

from . import paths

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,               -- pdf | docx | txt | md | html | url
  origin TEXT DEFAULT '',           -- 本地备份文件绝对路径; URL 文档为原始地址
  url TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  sha256 TEXT DEFAULT '',
  pages INTEGER DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,             -- 块在文档内的顺序
  page TEXT DEFAULT '',             -- '3' | '3-5' | '' (无页码来源)
  text TEXT NOT NULL,
  emb BLOB                          -- float32 向量字节
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _connect() -> sqlite3.Connection:
    paths.ensure_dirs()
    conn = sqlite3.connect(paths.DATA_DIR / "rag.db", timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(_SCHEMA)


# ------------------------------------------------------------------ meta

def meta_get(key: str, default: str = "") -> str:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ------------------------------------------------------------------ docs

def add_doc(name: str, kind: str, origin: str = "", url: str = "",
            size: int = 0, sha256: str = "", pages: int = 0) -> int:
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO docs(name,kind,origin,url,size,sha256,pages,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, kind, origin, url, size, sha256, pages, time.time()),
        )
        return int(cur.lastrowid)


def list_docs() -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS n_chunks "
            "FROM docs d ORDER BY d.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_doc(doc_id: int) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS n_chunks "
            "FROM docs d WHERE d.id=?", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def update_doc_src(doc_id: int, origin: str = "", url: str = "") -> None:
    with _LOCK, _connect() as conn:
        conn.execute("UPDATE docs SET origin=?, url=? WHERE id=?", (origin, url, doc_id))


def find_dup(sha256: str, name: str) -> dict | None:
    if not sha256:
        return None
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM docs WHERE sha256=? OR (name=? AND kind NOT IN('url'))",
            (sha256, name),
        ).fetchone()
    return dict(row) if row else None


def delete_doc(doc_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM docs WHERE id=?", (doc_id,))


def stats() -> dict:
    with _LOCK, _connect() as conn:
        n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        dims = 0
        row = conn.execute(
            "SELECT length(emb) AS L FROM chunks WHERE emb IS NOT NULL LIMIT 1"
        ).fetchone()
        if row and row["L"]:
            dims = int(row["L"] / 4)
    return {"docs": n_docs, "chunks": n_chunks, "dims": dims}


# ------------------------------------------------------------------ chunks

def insert_chunks(doc_id: int, items: list[tuple[int, str, str, np.ndarray]]) -> None:
    """items: [(seq, page, text, emb_vec)] 逐条写库(个人级规模足够快)。"""
    with _LOCK, _connect() as conn:
        for seq, page, text, vec in items:
            conn.execute(
                "INSERT INTO chunks(doc_id,seq,page,text,emb) VALUES(?,?,?,?,?)",
                (doc_id, seq, page, text, vec.astype(np.float32).tobytes()),
            )


def iter_chunks(doc_id: int | None = None) -> Iterator[dict]:
    sql = "SELECT id,doc_id,seq,page,text FROM chunks"
    args: tuple = ()
    if doc_id is not None:
        sql += " WHERE doc_id=?"
        args = (doc_id,)
    sql += " ORDER BY doc_id, seq"
    with _LOCK, _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    for r in rows:
        yield dict(r)


def corpus() -> tuple[list[dict], np.ndarray | None]:
    """加载全库检索语料: 返回 (元信息行, [N,D] float32 矩阵|None)。

    行 i 与矩阵第 i 行一一对应。库为空或没有向量时矩阵为 None。
    """
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT c.id,c.doc_id,c.seq,c.page,c.text,d.name AS doc_name,"
            "d.kind AS doc_kind, d.origin AS doc_origin "
            "FROM chunks c JOIN docs d ON d.id=c.doc_id ORDER BY c.id"
        ).fetchall()
        if not rows:
            return [], None
        embs = conn.execute(
            "SELECT emb FROM chunks WHERE emb IS NOT NULL ORDER BY id"
        ).fetchall()
    metas = [dict(r) for r in rows]
    if len(embs) != len(metas):
        # 存在没有向量的块(例如中途失败),此时向量检索只能部分工作
        pass
    buf = b"".join(r["emb"] for r in embs if r["emb"])
    if not buf or not embs:
        return metas, None
    matrix = np.frombuffer(buf, dtype=np.float32).reshape(len(embs), -1).copy()
    return metas, matrix


def corpus_version() -> tuple[int, int]:
    """(总块数, 最大id) 用于判断向量/BM25 缓存是否过期。"""
    with _LOCK, _connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        m = conn.execute("SELECT COALESCE(MAX(id),0) FROM chunks").fetchone()[0]
    return int(n), int(m)


def chunk_cfg_snapshot() -> dict | None:
    raw = meta_get("chunk_cfg", "")
    return json.loads(raw) if raw else None


def mark_chunk_cfg(cfg: dict) -> None:
    meta_set("chunk_cfg", json.dumps(cfg, ensure_ascii=False))
