"""Flask 服务层: 前端界面与后端核心之间的全部 API。

设计为纯本地服务(127.0.0.1): 既可以被桌面窗口加载,也可以随时在浏览器打开,
保证任何环境下应用都可用。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

from core import chunker, db, embed, envcheck, gguf, gpuinfo, llm, loader, models, paths, search, tasks

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# ---------------------------------------------------------------- 配置工具


def _mask(v: str) -> str:
    if not v:
        return ""
    return "••••" + v[-4:] if len(v) > 6 else "••••"


def _safe_view(cfg: dict) -> dict:
    """返回给前端展示的配置 —— 真实密钥绝不下发,只给掩码与是否存在标记。"""
    out = json.loads(json.dumps(cfg))
    for sec in ("llm", "embedding"):
        key = out.get(sec, {}).get("api_key")
        out[sec]["api_key_set"] = bool(key)
        if key:
            out[sec]["api_key_masked"] = _mask(key)
        out[sec].pop("api_key", None)
    return out


def _is_masked(v) -> bool:
    return isinstance(v, str) and bool(v) and v.startswith("•")


def _apply_patch(cfg: dict, patch: dict) -> dict:
    def rec(base, p):
        for k, v in p.items():
            if k.endswith("_masked"):
                continue                       # 打码回显字段不写回
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                rec(base[k], v)
            elif isinstance(v, str) and _is_masked(v):
                continue                       # 未修改的密钥
            elif isinstance(base.get(k), bool):
                base[k] = bool(v)
            elif isinstance(base.get(k), (int, float)) and not isinstance(v, bool):
                try:
                    base[k] = type(base.get(k))(v)
                except (TypeError, ValueError):
                    pass
            else:
                base[k] = v
    rec(cfg, patch)
    return cfg


def _cfg() -> dict:
    return paths.load_config()


def _llm_usability(cfg: dict, llm_cfg: dict) -> dict:
    """判断当前大模型是否真正可用,并给出可执行的提示(前端据此禁用聊天/引导修复)。"""
    provider = llm_cfg.get("provider", "mock")
    gg = cfg.get("gguf", {})
    model = gguf.pick_model(gg.get("model") or "")
    srv = gguf.find_server(cfg.get("advanced", {}).get("llama_server_path") or "")
    base = (llm_cfg.get("base_url") or "").strip()
    out = {"provider": provider, "model": llm_cfg.get("model", ""),
           "configured": False, "usable": False,
           "label": _provider_label(llm_cfg), "hint": "", "fix_view": "settings"}
    if provider == "mock":
        out.update(configured=True, usable=True, hint="内置演示模式: 无需任何配置即可体验全流程")
    elif provider == "gguf":
        if not model:
            out.update(hint="还没有选择 GGUF 模型 —— 请把 .gguf 文件放进 models/gguf 文件夹,"
                            "再到设置页点「刷新」并勾选一个模型")
        elif not srv:
            out.update(hint="GGUF 推理引擎(llama-server)未安装 —— 到「环境检查」页 GGUF 引擎条目"
                            "下载官方 Windows 版并放入 models/llama", fix_view="env")
        else:
            out.update(configured=True, usable=True, hint=f"本地模型就绪: {model['name']}")
    else:  # openai_compat
        if not base:
            out.update(hint="在线 API 模式缺少接口地址 —— 到设置页选一个服务商预设填充,"
                            "或改用『本地 GGUF』/『演示模式』")
        else:
            local_hint = ""
            if "localhost" in base or "127.0.0.1" in base:
                local_hint = ("(Ollama 需先启动并已拉取模型) ")
            out.update(configured=True, usable=True,
                       hint=f"在线 API 已配置{local_hint}—— 建议先点「测试连接」确认可用")
    return out


def _provider_label(cfg: dict) -> str:
    p = cfg.get("provider", "mock")
    if p == "mock":
        return "内置演示模式(无需密钥)"
    if p == "gguf":
        full = _cfg().get("gguf", {})
        m = gguf.pick_model(full.get("model") or "")
        srv = gguf.find_server(_cfg().get("advanced", {}).get("llama_server_path") or "")
        name = m["name"] if m else "未选择模型"
        eng = "引擎就绪" if srv else "未安装引擎(见环境检查)"
        return f"本地 GGUF · {name} · {eng}"
    b = (cfg.get("base_url") or "").replace("https://", "").replace("http://", "").rstrip("/")
    return f"{cfg.get('model') or '?'} @ {b}"


# ---------------------------------------------------------------- app

app = Flask(__name__, static_folder=None)   # 禁用默认 /static,统一走下方自定义路由
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024   # 上传上限 512MB
tasks_mgr = tasks.TaskManager()


def _ok(**kw):
    return jsonify({"ok": True, **kw})


def _err(message: str, code: int = 400, **kw):
    return jsonify({"ok": False, "message": message, **kw}), code


# ---------------------------------------------------------------- 状态/配置

@app.get("/api/health")
def health():
    return _ok(ts=time.time())


@app.get("/api/state")
def state():
    cfg = _cfg()
    embed_cfg, llm_cfg, ret_cfg = cfg["embedding"], cfg["llm"], cfg["retrieval"]
    st = db.stats()
    used_cfg = {k: ret_cfg.get(k) for k in ("chunk_size", "chunk_overlap")}
    stale = bool(st["chunks"]) and db.chunk_cfg_snapshot() != used_cfg
    llm_state = _llm_usability(cfg, llm_cfg)
    return _ok(
        config=_safe_view(cfg),
        llm=llm_state,
        embed=embed.readiness(embed_cfg),
        stats=st,
        index_stale=stale,
        data_dir=str(paths.DATA_DIR),
    )


@app.get("/api/config")
def get_config():
    return _ok(config=_safe_view(_cfg()))


@app.post("/api/config")
def set_config():
    patch = request.get_json(force=True, silent=True) or {}
    old_cfg = _cfg()
    cfg = _apply_patch(old_cfg, patch)
    paths.save_config(cfg)
    paths.apply_hf_env(cfg)      # 镜像开关即时生效
    gguf.sync_on_config_change(old_cfg, cfg)   # GGUF 换了模型/参数 → 重启本地引擎
    return _ok(config=_safe_view(cfg))


@app.get("/api/providers")
def providers():
    return _ok(presets=llm.PROVIDER_PRESETS)


@app.get("/api/open-data")
def open_data():
    os.startfile(str(paths.DATA_DIR))   # noqa: type: ignore
    return _ok()


# ---------------------------------------------------------------- 测试连接

@app.post("/api/llm/test")
def llm_test():
    patch = request.get_json(force=True, silent=True) or {}
    cfg = _cfg()
    if patch:
        cfg = _apply_patch(cfg, {"llm": patch})
    lc = cfg["llm"]
    if lc.get("provider") == "mock":
        return _ok(ok=True, provider="mock",
                   text="✅ 演示模式无需联网。配置真实 API 后,这里会返回真实模型的回复。",
                   ms=0)
    if not lc.get("base_url"):
        return _err("请先填写 base_url(可使用预设一键填充)。")
    res = llm.chat_once(lc, cfg["retrieval"], "你好,请用一句话回复: 连接成功。",
                        [], None, max_tokens=60)
    if not res["ok"]:
        return _err(res["error"], code=502)
    return _ok(ok=True, text=res["text"], ms=res["ms"], provider=lc.get("model"))


@app.get("/api/hardware/status")
def hardware_status():
    """顶部『灵动岛』GPU 实时遥测: 显卡负载 + GGUF 引擎状态(本地,带缓存)。"""
    cfg = _cfg()
    gpu = gpuinfo.detect()
    live = gpuinfo.live_stats()
    ll = gguf.status(cfg)
    return _ok(
        gpus=live["gpus"],
        source=live["source"],
        summary=gpu["summary"],
        has_nvidia=gpu["has_cuda_gpu"],
        llama={"running": ll["running"], "detail": ll["running_detail"]},
    )


@app.get("/api/gguf/info")
def gguf_info():
    cfg = _cfg()
    return _ok(**gguf.status(cfg))


@app.post("/api/gguf/start")
def gguf_start():
    """异步启动本地 GGUF 引擎(加载模型可能较慢,走任务进度)。"""
    cfg = _cfg()
    if cfg.get("llm", {}).get("provider") != "gguf":
        return _err("请先在「设置 → 大模型」把运行模式切到『本地 GGUF 模型』并选择模型文件。")
    task = tasks_mgr.create("gguf", "启动本地 GGUF 引擎", lambda emit: (
        gguf.ensure_running(_cfg(),
                            wait_timeout=900) or True))
    return _ok(task=task)


@app.post("/api/gguf/open-dir")
def gguf_open_dir():
    cfg = _cfg()
    d = gguf.DEFAULT_GGUF_DIR
    custom = (cfg.get("gguf", {}).get("dir") or "").strip()
    if custom:
        p = os.path.abspath(custom)
        if os.path.isdir(p):
            d = p
    d.mkdir(parents=True, exist_ok=True)
    os.startfile(str(d))  # noqa
    return _ok(dir=str(d))


@app.post("/api/gguf/stop")
def gguf_stop():
    res = gguf.stop()
    return _ok(**res)


@app.post("/api/embed/test")
def embed_test():
    cfg = _cfg()
    text = (request.get_json(force=True, silent=True) or {}).get("text") or "知识库与检索增强生成"
    res = embed.test_embedding(cfg["embedding"], text)
    if not res["ok"]:
        return _err(res["error"], code=502)
    return _ok(**res)


# ---------------------------------------------------------------- 模型下载

@app.get("/api/model/status")
def model_status():
    cfg = _cfg()
    repo = cfg["embedding"].get("model", "BAAI/bge-small-zh-v1.5")
    return _ok(repo=repo, ready=embed.model_is_ready(repo),
               models=embed.local_model_info(),
               readiness=embed.readiness(cfg["embedding"]))


@app.post("/api/model/download")
def model_download():
    cfg = _cfg()
    body = request.get_json(force=True, silent=True) or {}
    repo = (body.get("repo") or cfg["embedding"].get("model")
            or "BAAI/bge-small-zh-v1.5").strip()
    paths.apply_hf_env(cfg)
    task = tasks_mgr.create("model", f"下载嵌入模型 {repo}",
                            lambda emit: models.download_model(
                                repo, progress=lambda frac, msg: emit(frac, "下载模型", msg)))
    return _ok(task=task)


@app.post("/api/model/delete")
def model_delete():
    body = request.get_json(force=True, silent=True) or {}
    repo = body.get("repo") or "BAAI/bge-small-zh-v1.5"
    return _ok(**models.delete_local_model(repo))


# ---------------------------------------------------------------- 任务

@app.get("/api/tasks")
def tasks_list():
    return _ok(tasks=tasks_mgr.list())


@app.get("/api/tasks/<tid>")
def task_get(tid: str):
    t = tasks_mgr.get(tid)
    if not t:
        return _err("任务不存在", 404)
    return _ok(task=t)


# ---------------------------------------------------------------- 文档摄入

def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", name).strip()
    return name or "untitled"


def _ingest(emit, parsed: dict, data: bytes, filename: str, url: str = "") -> dict:
    """单文档全流程(解析结果已就绪): 分块 → 向量化 → 入库。
    parsed 由 loader.parse_bytes / loader.fetch_url 产生。
    """
    cfg = _cfg()
    ret_cfg, embed_cfg = cfg["retrieval"], cfg["embedding"]
    name, kind = parsed["name"], parsed["kind"]

    n_paras = sum(len(p["paragraphs"]) for p in parsed["pages"])
    if not n_paras:
        raise RuntimeError(f"{filename}: 文档解析后没有可用文字内容。")

    dup = db.find_dup(parsed["sha256"], name)
    if dup:
        raise RuntimeError(
            f"跳过重复文档: 《{name}》(库中已存在,无需重复上传)。"
            "如需更新请先删除旧文档。")

    emit(0.2, "分块 (Chunking)", f"{filename}: {n_paras} 个自然段 → "
         f"{ret_cfg['chunk_size']} 字符/块(重叠 {ret_cfg['chunk_overlap']})…")
    chunks = chunker.chunk_document(parsed["pages"],
                                    int(ret_cfg["chunk_size"]),
                                    int(ret_cfg["chunk_overlap"]))
    if not chunks:
        raise RuntimeError(f"{filename}: 分块结果为空。")
    texts = [c["text"] for c in chunks]

    emit(0.35, "向量化 (Embedding)",
         f"{filename}: {len(chunks)} 块 → 向量,分批转换中…")
    try:
        def prog(done, total):
            emit(0.35 + 0.55 * done / max(1, total), "向量化 (Embedding)",
                 f"{filename}: 已完成 {done * 32}/{len(texts)} 块")
        matrix = embed.embed_texts(embed_cfg, texts, progress=prog)
    except Exception as e:
        raise RuntimeError(f"向量化失败: {e}") from e

    emit(0.93, "入库 (Indexing)", f"{filename}: 写入向量库…")
    doc_id = db.add_doc(name=name, kind=kind, size=parsed.get("size", len(data)),
                        sha256=parsed["sha256"], pages=parsed["n_pages"])
    origin, db_url = "", url if kind == "url" else ""
    if kind != "url" and data:
        ext = os.path.splitext(filename)[1] or ""
        target = paths.DOCS_DIR / f"{doc_id:05d}_{_safe_name(os.path.splitext(filename)[0])[:60]}{ext.lower()}"
        target.write_bytes(data)
        origin = str(target)
    db.update_doc_src(doc_id, origin, db_url)
    db.insert_chunks(doc_id, [(c["seq"], c["page"], c["text"], matrix[i])
                              for i, c in enumerate(chunks)])
    db.mark_chunk_cfg({k: ret_cfg.get(k) for k in ("chunk_size", "chunk_overlap")})
    search.invalidate()
    return {"doc_id": doc_id, "name": name, "kind": kind,
            "chunks": len(chunks), "pages": parsed["n_pages"]}


@app.post("/api/docs/upload")
def docs_upload():
    """上传本地文件(files[] 多选),或粘贴网页链接(url 字段)。"""
    files = request.files.getlist("files")
    url = (request.form.get("url") or "").strip()
    if not files and not url:
        return _err("没有收到内容: 请选择文件,或粘贴 http(s) 网页链接。")

    payloads: list[dict] = []
    if url:
        if not re.match(r"^https?://", url):
            return _err("网页链接需以 http:// 或 https:// 开头。")
        try:
            pr = loader.fetch_url(url)          # 先抓取并解析,失败立即反馈
        except loader.LoadError as e:
            return _err(f"网页抓取失败: {e}")
        payloads.append({"parsed": pr, "data": b"", "filename": url, "url": url})
    for f in files:
        raw = f.read()
        if not raw:
            continue
        fname = f.filename or "untitled"
        try:
            parsed = loader.parse_bytes(raw, fname)
        except loader.LoadError as e:
            # 单个文件解析失败: 标记错误,不影响其它文件
            payloads.append({"parsed": None, "error": str(e),
                             "data": raw, "filename": fname, "url": ""})
            continue
        payloads.append({"parsed": parsed, "data": raw, "filename": fname, "url": ""})
    if not payloads:
        return _err("文件内容为空。")

    def run(emit):
        results, errors = [], []
        for i, p in enumerate(payloads):
            try:
                if p.get("parsed") is None:
                    raise RuntimeError(p.get("error") or "解析失败")
                emit(i / max(1, len(payloads)), "文档摄入", f"[{i+1}/{len(payloads)}] {p['filename']}")
                r = _ingest(lambda frac, st, msg: emit(
                    (i + frac) / max(1, len(payloads)), st, msg),
                    p["parsed"], p["data"], p["filename"], p["url"])
                results.append(r)
            except Exception as e:
                errors.append({"file": p["filename"], "error": str(e)})
        return {"results": results, "errors": errors}

    task = tasks_mgr.create("upload", f"导入 {len(payloads)} 个文档", run)
    return _ok(task=task)


@app.get("/api/docs")
def docs_list():
    return _ok(docs=db.list_docs())


@app.get("/api/docs/<int:doc_id>")
def docs_detail(doc_id: int):
    d = db.get_doc(doc_id)
    if not d:
        return _err("文档不存在", 404)
    q = (request.args.get("q") or "").strip()
    chunks = [c for c in db.iter_chunks(doc_id) if (not q or q in c["text"])]
    return _ok(doc=d, chunks=chunks, total=len(chunks))


@app.delete("/api/docs/<int:doc_id>")
def docs_delete(doc_id: int):
    d = db.get_doc(doc_id)
    if not d:
        return _err("文档不存在", 404)
    if d.get("origin") and d["kind"] != "url":
        cand = paths.DOCS_DIR / os.path.basename(d["origin"])
        if cand.exists():
            cand.unlink(missing_ok=True)
    db.delete_doc(doc_id)
    search.invalidate()
    return _ok()


@app.post("/api/docs/<int:doc_id>/open")
def docs_open(doc_id: int):
    d = db.get_doc(doc_id)
    if not d:
        return _err("文档不存在", 404)
    if d["kind"] == "url" and d.get("url"):
        webbrowser.open(d["url"])
        return _ok(opened="url")
    if d.get("origin"):
        cand = paths.DOCS_DIR / os.path.basename(d["origin"])
        if cand.exists():
            os.startfile(str(cand))  # noqa
            return _ok(opened="file")
    return _err("原始文件已不存在(可能被手动删除)。知识库数据不受影响。")


@app.post("/api/docs/reindex")
def docs_reindex():
    """按当前分块/嵌入设置重建全部文档(改过分块参数后使用)。"""
    def run(emit):
        docs = db.list_docs()
        if not docs:
            return {"note": "知识库为空,无需重建。"}
        with db._connect() as conn:
            conn.execute("DELETE FROM chunks")
        search.invalidate()
        results, errors = [], []
        for i, d in enumerate(docs):
            try:
                emit(i / max(1, len(docs)), f"重建《{d['name']}》", "准备中…")
                if d["kind"] == "url" and d.get("url"):
                    pr = loader.fetch_url(d["url"])
                    r = _ingest(lambda frac, st, msg: emit(
                        (i + frac) / max(1, len(docs)), st, msg),
                        pr, b"", d["name"], d["url"])
                else:
                    cand = paths.DOCS_DIR / os.path.basename(d["origin"])
                    if not cand.exists():
                        raise RuntimeError(f"原始文件丢失: {d['origin']}")
                    pr = loader.parse_bytes(cand.read_bytes(), d["name"])
                    r = _ingest(lambda frac, st, msg: emit(
                        (i + frac) / max(1, len(docs)), st, msg),
                        pr, b"", d["name"])
                results.append(r)
            except Exception as e:
                errors.append({"doc": d["name"], "error": str(e)})
        return {"results": results, "errors": errors}

    task = tasks_mgr.create("reindex", "重建全部文档索引", run)
    return _ok(task=task)


# ---------------------------------------------------------------- 检索调试(教学)

@app.post("/api/search")
def debug_search():
    """只做混合检索并返回详情(问答页『查看检索过程』也复用此接口)。"""
    body = request.get_json(force=True, silent=True) or {}
    q = (body.get("question") or "").strip()
    if not q:
        return _err("请输入问题。")
    cfg = _cfg()
    res = search.hybrid_search(q, cfg["retrieval"], cfg["embedding"])
    return _ok(**res)


# ---------------------------------------------------------------- 问答(流式 NDJSON)

@app.post("/api/chat")
def chat():
    body = request.get_json(force=True, silent=True) or {}
    q = (body.get("question") or "").strip()
    if not q:
        return _err("请输入问题。")
    cfg = _cfg()

    def gen():
        def sse(obj):
            yield json.dumps(obj, ensure_ascii=False) + "\n"

        yield from sse({"type": "begin"})
        # 1) 混合检索
        t0 = time.time()
        try:
            res = search.hybrid_search(q, cfg["retrieval"], cfg["embedding"])
        except Exception as e:
            yield from sse({"type": "error", "message": f"检索失败:{e}"})
            return
        yield from sse({"type": "retrieval", "results": res["results"],
                        "detail": res["detail"], "ms": int((time.time() - t0) * 1000)})
        if not res["results"]:
            yield from sse({"type": "error",
                            "message": "知识库还没有可检索的内容。请先到「知识库」页上传文档再提问。"})
            return
        # 2) 生成回答(流式; llm.chat_stream 自身以 done/error 事件收尾)
        try:
            events = llm.chat_stream(cfg["llm"], cfg["retrieval"], q,
                                     res["results"], body.get("history") or [])
            for ev in events:
                yield from sse(ev)
        except Exception as e:
            yield from sse({"type": "error", "message": f"生成回答失败:{e}"})

    return Response(gen(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- 环境检查

@app.get("/api/envcheck")
def env_check():
    return _ok(**envcheck.run_checks())


# ---------------------------------------------------------------- 静态页面

@app.get("/")
def index():
    return send_from_directory(_WEB_DIR, "index.html")


@app.get("/tutorial.html")
def tutorial_page():
    return send_from_directory(_WEB_DIR, "tutorial.html")


@app.route("/static/<path:fp>")
def static_files(fp):
    return send_from_directory(os.path.join(_WEB_DIR, "static"), fp)


# ---------------------------------------------------------------- 启动

def start_server(port: int = 8765, host: str = "127.0.0.1"):
    """启动后台 HTTP 服务,返回 server 对象(供 app.py 关闭)。"""
    paths.ensure_dirs()
    db.init_db()
    paths.apply_hf_env()
    import atexit as _atexit
    _atexit.register(gguf.stop_all)   # 退出时释放本地 GGUF 引擎
    srv = make_server(host, port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name="greenrag-http").start()
    return srv
