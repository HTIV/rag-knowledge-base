"""分块器 (Chunking)。

为什么要把文档切成小块?
  1. 嵌入模型一次只能编码有限长度,超长内容会被截断、丢失语义;
  2. 检索需要"命中"粒度 —— 一整本书的向量无法指向具体某一段;
  3. 每块独立记录页码,回答时才能标注"第 X 页"。

策略说明(新手可对照「教程」页理解):
  - 以段落为最小单元聚合到目标长度 target_size(避免从句子中间切断);
  - 超长段落(如整页代码/长表格)按句子边界切割;
  - 相邻块保留 chunk_overlap 字符的重叠,防止"答案正好被切在两块之间";
  - 一块跨越多个 PDF 页时,页码记作 "3-4"(引用时可引导用户翻该范围)。
"""
from __future__ import annotations

import re

_SENT_END = "。！？!?；;…\n"


def _cut_long_paragraph(text: str, target: int) -> list[str]:
    """把单个超长段落按句子边界切成 ≤ target 的若干段。"""
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= target and ch in _SENT_END:
            parts.append(buf.strip())
            buf = ""
        elif len(buf) >= target * 1.5:
            # 句子特别长(如公式行),硬切但尽量在标点处
            cut = re.search(r"[，,、;；\s]", buf[::-1])
            idx = len(buf) - (cut.start() + 1) if cut else target
            parts.append(buf[:idx].strip())
            buf = buf[idx:]
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def chunk_document(raw_pages: list[dict], chunk_size: int = 400,
                   chunk_overlap: int = 80) -> list[dict]:
    """raw_pages: loader.parse_bytes 的 pages 字段。
    返回: [ {"seq":0,"page":"1","text":"..."}, ... ]  page 可为 "3" / "3-4" / ""
    """
    # 第一步: 展平成 "单元" (段落或长段落切出的子段),并记录所属页
    units: list[tuple[str, str | None]] = []   # (text, page)
    for pg in raw_pages:
        page = pg.get("page")
        for para in pg.get("paragraphs", []):
            if len(para) <= chunk_size:
                units.append((para, page))
            else:
                for piece in _cut_long_paragraph(para, chunk_size):
                    units.append((piece, page))

    if not units:
        return []

    # 第二步: 贪心聚合到目标长度(不加重叠的版本)
    merged: list[list[tuple[str, str | None]]] = []
    cur: list[tuple[str, str | None]] = []
    cur_len = 0
    for u_text, u_page in units:
        add = len(u_text) + 1
        if cur and cur_len + add > chunk_size:
            merged.append(cur)
            cur = []
            cur_len = 0
        cur.append((u_text, u_page))
        cur_len += add
    if cur:
        merged.append(cur)

    # 第三步: 应用重叠 —— 把上一块末尾 overlap 个字符"预粘"到下一块开头
    chunks: list[dict] = []
    prev_tail = ""
    seq = 0
    for group in merged:
        body = "\n".join(t for t, _ in group)
        pages = sorted({int(p) for t, p in group if p is not None})
        if len(group) > 1 and not pages:
            page = ""
        elif pages:
            if len(pages) == 1:
                page = str(pages[0])
            elif max(pages) - min(pages) == len(pages) - 1:
                page = f"{min(pages)}-{max(pages)}"   # 连续页区间
            else:
                page = f"{min(pages)}-{max(pages)}"   # 不连续页: 给范围并标注起止
        else:
            page = ""
        text = body
        if prev_tail and chunk_overlap > 0:
            text = prev_tail + "\n" + body
        chunks.append({"seq": seq, "page": page, "text": text})
        seq += 1
        # 重叠尾巴优先取"上一个自然段末尾",控制在 overlap 附近
        tail = body[-chunk_overlap:] if len(body) > chunk_overlap else body
        prev_tail = tail

    return chunks
