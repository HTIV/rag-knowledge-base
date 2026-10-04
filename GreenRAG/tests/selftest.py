"""GreenRAG 端到端自测脚本。

造一份样例 PDF(3 页)与 Word 文档 → 通过 HTTP API 上传
→ 等待向量化完成 → 混合检索 → 问答(演示模式) → 断言关键结果。
用法: .venv/Scripts/python.exe tests/selftest.py
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.request

import os

BASE = f"http://127.0.0.1:{os.environ.get('GREENRAG_PORT', '8765')}"
PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


def post(path, payload=None, raw: bytes | None = None, ctype: str = "application/json"):
    data = raw if raw is not None else json.dumps(payload or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def wait_task(tid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = get(f"/api/tasks/{tid}")["task"]
        if t["state"] in ("done", "error"):
            return t
        time.sleep(1.5)
    return {"state": "timeout"}


# ---------------------------------------------------------------- 造文档
def make_pdf(path: str, pages: list[str]):
    import fitz
    doc = fitz.open()
    for txt in pages:
        page = doc.new_page()
        rect = fitz.Rect(50, 60, 545, 780)
        page.insert_textbox(rect, txt, fontsize=11, fontname="china-s")
    doc.save(path)
    doc.close()


def make_docx(path: str, paras: list[str]):
    from docx import Document
    d = Document()
    for i, p in enumerate(paras):
        if p.startswith("## "):
            d.add_heading(p[3:], level=1)
        else:
            d.add_paragraph(p)
    d.save(path)


def main():
    # 统一用演示模式跑回归(不受本地 GGUF / API 配置影响)
    post("/api/config", {"llm": {"provider": "mock"}})
    print("== 1. 构造样例文档 ==")
    make_pdf("data/_sample_rag.pdf", [
        "检索增强生成(RAG)技术入门\n\nRAG 是 Retrieval-Augmented Generation 的缩写,"
        "中文称为检索增强生成。它通过在生成回答之前先从外部知识库检索相关资料,"
        "再把资料与问题一起交给大语言模型,从而显著降低模型幻觉,并让回答可以溯源。"
        "\n\n与直接让模型回答相比,RAG 的最大优势是知识可以随时更新:"
        "你只需替换知识库中的文档,无需重新训练模型。",
        "混合检索与 RRF 融合\n\n混合检索通常同时使用 BM25 稀疏检索与向量稠密检索两条路径。"
        "BM25 对精确关键词、术语与编号的匹配能力强;向量检索则善于理解同义改写。"
        "将两条路径的排序结果用倒数排名融合 RRF 合并,公式为 1 除以 k 加排名。"
        "\n\nRRF 的优点是两路得分尺度不同也能直接融合,工程实现简单且鲁棒。",
        "分块策略与引用\n\n文档先按语义切分为若干块,每块独立编码为向量。"
        "块过大会稀释语义,过小会丢失上下文,通常取 300 到 600 字并保留少量重叠。"
        "每个块记录来源文档与页码,回答时大模型需在引用处标注编号,"
        "这样用户就能回到原文核对答案。GreenRAG 正是基于这些思想实现的个人知识库工具。",
    ])
    make_docx("data/_sample_notes.docx", [
        "## 深度学习面试要点",
        "反向传播通过链式法则计算损失对每个参数的梯度。学习率过大导致训练震荡,"
        "过小导致收敛缓慢,常用余弦退火等调度策略。",
        "BatchNorm 在小 batch 上统计不稳定;LayerNorm 对序列任务更友好。",
        "防止过拟合的手段包括: 数据增强、早停、Dropout 与权重衰减。",
    ])
    print("  生成完毕 data/_sample_rag.pdf / data/_sample_notes.docx")

    print("== 2. 上传(走完整任务流水线) ==")
    with open("data/_sample_rag.pdf", "rb") as f1, open("data/_sample_notes.docx", "rb") as f2:
        import mimetypes
        boundary = "----greenragtest"
        body = io.BytesIO()
        for name, fp, ct in (("files", f1, "application/pdf"),
                             ("files", f2, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"; filename="{fp.name.split("/")[-1]}"\r\n'.encode())
            body.write(f"Content-Type: {ct}\r\n\r\n".encode())
            body.write(fp.read())
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
    up = post("/api/docs/upload", raw=body.getvalue(),
              ctype=f"multipart/form-data; boundary={boundary}")
    check("上传任务创建", up.get("ok"))
    t = wait_task(up["task"]["id"])
    check("上传任务完成", t["state"] == "done", f"-> {t.get('detail','')[:60]} {t.get('error','')[:160]}")
    if t["state"] != "done":
        print("  任务输出:", json.dumps(t, ensure_ascii=False)[:500])
    res = t.get("result") or {}
    check("两个文档均入库", len(res.get("results", [])) == 2,
          str([(r["name"][-16:], r["chunks"]) for r in res.get("results", [])]))

    docs = get("/api/docs")["docs"]
    pdf_doc = next((d for d in docs if "sample_rag" in d["name"]), None)
    docx_doc = next((d for d in docs if "sample_notes" in d["name"]), None)
    check("PDF 文档带页码元信息", bool(pdf_doc and pdf_doc["pages"] == 3), f"pages={pdf_doc and pdf_doc['pages']}")
    check("Word 文档入库", bool(docx_doc and docx_doc["n_chunks"] > 0),
          f"chunks={docx_doc and docx_doc['n_chunks']}")

    # 分块页码校验
    if pdf_doc:
        det = get(f"/api/docs/{pdf_doc['id']}")
        pages = {c["page"] for c in det["chunks"] if c["page"]}
        ok_pages = {"1", "2", "3", "1-2", "2-3", "1-3"}
        check("分块包含页码", pages and pages <= ok_pages, f"pages={sorted(pages)}")

    print("== 3. 混合检索(语义 + 关键词) ==")
    qs = ["RAG 相比直接问答有什么好处?", "RRF 融合公式是什么", "GB/T 不存在的编号 9719"]
    for q in qs:
        r = post("/api/search", {"question": q})
        det = r["detail"]
        check(f"检索「{q[:14]}」", bool(r["results"]) and det["dense_ms"] >= 0,
              f"→ top1: {r['results'][0]['doc_name'][-14:] if r['results'] else '无'} "
              f"page={r['results'][0]['page'] if r['results'] else '-'} "
              f"cos={det.get('best_cosine')} tokens={len(det.get('query_tokens', []))}")

    print("== 4. 问答(演示模式,流式) ==")
    body = json.dumps({"question": "混合检索为什么比单路检索好?"}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    events = []
    with urllib.request.urlopen(req, timeout=120) as r:
        for ln in r:
            ln = ln.decode().strip()
            if ln:
                events.append(json.loads(ln))
    types = [e["type"] for e in events]
    check("事件序列完整", "retrieval" in types and "delta" in types and "done" in types,
          "->".join(types))
    ret = next(e for e in events if e["type"] == "retrieval")
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    check("生成内容非空且含引用编号", len(text) > 100 and "[" in text, f"len={len(text)}")
    src = next((e for e in events if e["type"] == "sources"), None)
    check("引用来源含文档+页码", bool(src and src["sources"]),
          str([(s["doc"][-14:], s.get("page")) for s in (src or {}).get("sources", [])][:3]))
    if ret and ret["results"]:
        first = ret["results"][0]
        check("命中结果含双路排名/RRF/命中词字段",
              all(k in first for k in ("dense_rank", "bm25_rank", "rrf", "dense_score")),
              f"dense#{first['dense_rank']} bm25#{first['bm25_rank']} rrf={first['rrf']} "
              f"hits={first['hit_tokens']}")

    print("== 5. 状态与清理 ==")
    st = get("/api/state")
    check("状态统计正确", st["stats"]["docs"] >= 2 and st["stats"]["chunks"] >= 3,
          f"docs={st['stats']['docs']} chunks={st['stats']['chunks']} dims={st['stats']['dims']}")
    check("嵌入模型就绪", st["embed"]["ok"], st["embed"]["hint"])
    for d in docs:
        post(f"/api/docs/{d['id']}/open") if False else None
    # 清理测试文档(保留样例供用户体验)
    # for d in get("/api/docs")["docs"]: post 删除用 DELETE
    import urllib.request as u
    for d in get("/api/docs")["docs"]:
        req2 = urllib.request.Request(BASE + f"/api/docs/{d['id']}", method="DELETE")
        with urllib.request.urlopen(req2, timeout=30):
            pass
    print(f"\n==== 结果: {PASS} 通过 / {FAIL} 失败 ====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
