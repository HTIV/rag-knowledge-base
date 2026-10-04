"""生成 GreenRAG 演示用示例文档,并上传到知识库(供截图/试用)。
用法: python tests/seed_demo.py [--clean]
"""
import json
import time
import urllib.request

from docx import Document
import fitz  # noqa: F401  (fitz API 提示弃用,忽略)


def make_pdf(path):
    doc = fitz.open()
    pages = [
        ("什么是 RAG?\n\n"
         "RAG(Retrieval-Augmented Generation)检索增强生成,是一种把大语言模型的生成能力"
         "与外部知识库结合起来的技术。传统大模型只能依据训练时见过的数据回答,"
         "对私有文档、最新资讯一无所知,而且容易一本正经地编造答案(幻觉)。\n\n"
         "RAG 的思路很简单: 提问时,先到你的知识库里检索最相关的片段,"
         "把片段和问题一起交给模型,并规定它只依据片段作答、标明出处。"
         "这样既能让模型回答私有资料里的问题,也能大幅降低幻觉。"),
        ("混合检索为什么更好?\n\n"
         "本系统使用混合检索: 同时运行 BM25 关键词检索与向量语义检索两条通道。"
         "BM25 擅长精确匹配术语、编号,比如“GB/T 9704”或“RNN”。"
         "向量检索擅长理解同义改写,例如问“怎么防止模型说胡话”也能找到写“缓解幻觉”的段落。\n\n"
         "两条通道各自返回前 100 名,再用 RRF(倒数排名融合)把两个榜单合并:"
         "某个片段只有在至少一条通道中排名足够靠前才能进入最终结果,"
         "两条通道都靠前的片段分数最高。"),
        ("引用与页码: 让答案可溯源\n\n"
         "解析 PDF 时,程序逐页提取文字并记录页码。分块时每一块都记住自己来自第几页,"
         "如果一块横跨两页,会标注为“3-4”。\n\n"
         "回答生成后,程序扫描输出中的 [1][2] 等引用编号,整理成来源清单:"
         "文档名 + 页码 + 原文摘要。点击来源卡片即可定位到对应片段,"
         "人工核对答案是否忠于原文 —— 这是 RAG 相比微调的核心优势之一。"),
    ]
    for txt in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(52, 58, 545, 780), txt,
                            fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()


def make_docx(path):
    d = Document()
    d.add_heading("GreenRAG 常见问题 FAQ", level=0)
    d.add_heading("1. 上传 PDF 后为什么不能标页码以外的引用?", level=1)
    d.add_paragraph("PDF 是版式文档,每一页是固定的,所以可以精确到页码。"
                    "Word 是流式文档,同一内容换行换页都会变,程序没有排版引擎,"
                    "无法知道某句话落在哪一页,因此只标注文档名。这是业界普遍做法。")
    d.add_heading("2. 扫描版 PDF 为什么导入失败?", level=1)
    d.add_paragraph("扫描件本质是图片,没有文字层,需要 OCR 才能提取文字。"
                    "GreenRAG 暂不支持 OCR,请改用文字版 PDF 或 Word 文档。"
                    "判断方法: 能否用鼠标选中 PDF 里的文字,能选中就是文字版。")
    d.add_heading("3. 回答没有引用编号怎么办?", level=1)
    d.add_paragraph("先看该回答上方的「查看检索过程」: 如果每条余弦相似度都很低,"
                    "说明知识库里确实没有相关内容,模型按规定回答“未找到”;"
                    "如果命中正常却没有编号,可能是模型输出不规范,"
                    "可以尝试把温度调低到 0.2 或更换更强的模型。")
    d.save(path)


def api(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def upload_file(path, kind):
    import io
    boundary = "----greenragdemo"
    body = io.BytesIO()
    with open(path, "rb") as f:
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="files"; filename="{path.split("/")[-1]}"\r\n'.encode())
        body.write(f"Content-Type: {kind}\r\n\r\n".encode())
        body.write(f.read())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/docs/upload", data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    import os
    os.makedirs("examples", exist_ok=True)
    make_pdf("examples/RAG原理入门.pdf")
    make_docx("examples/GreenRAG常见问题.docx")
    t1 = upload_file("examples/RAG原理入门.pdf", "application/pdf")
    t2 = upload_file("examples/GreenRAG常见问题.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    for t in (t1, t2):
        tid = t["task"]["id"]
        for _ in range(400):
            st = api(f"/api/tasks/{tid}")["task"]
            if st["state"] in ("done", "error"):
                print(tid, st["state"], st.get("error", "")[:120] or
                      [(r["name"], r["chunks"]) for r in (st.get("result") or {}).get("results", [])])
                break
            time.sleep(1.5)
    print("示例文档已入库:", [d["name"] for d in api("/api/docs")["docs"]])


if __name__ == "__main__":
    main()
