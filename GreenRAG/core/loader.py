"""文档加载器: 把 PDF / Word / TXT / Markdown / HTML / 网页 URL 解析为"带页码的段落"。

统一输出结构:
  [ { "page": 3, "paragraphs": ["段落1", "段落2", ...] }, ... ]   # PDF 有真实页码
  [ { "page": None, "paragraphs": [...] } ]                        # 无页码来源

这是 RAG 的第 1 步 —— Ingestion(文档摄入)。解析质量直接决定后续检索与引用的质量:
  1. PDF 逐页提取文本,记录页码 → 这是回答里"第 X 页"的来源;
  2. Word 无法获得真实页码(未用排版引擎),只保留文档级引用;
  3. 扫描版 PDF(纯图片、无文字层)无法直接解析,程序会给出明确提示。
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from urllib.parse import urlparse

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 GreenRAG/1.0")


class LoadError(Exception):
    """带用户可读提示的解析错误。"""


# ---------------------------------------------------------------- 文本工具

def _norm(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = unicodedata.normalize("NFKC", s)
    return s.strip()


def _split_lines_to_paragraphs(lines: list[str]) -> list[str]:
    """把 PDF 文本行重组成自然段落。

    PDF 行与"段落"不是一回事: 段中换行没有空白行隔开,
    而大多数中文/英文文本以句号结尾。这里用"是否以结束标点收尾"判断。
    """
    paras: list[str] = []
    cur: list[str] = []
    for raw in lines:
        ln = raw.strip()
        if not ln:
            if cur:
                paras.append("".join(cur))
                cur = []
            continue
        cur.append(ln)
        if ln[-1] in "。！？!?;；:：":
            paras.append("".join(cur))
            cur = []
    if cur:
        paras.append("".join(cur))
    out: list[str] = []
    for p in paras:
        p = _norm(p)
        if len(p) >= 2:
            out.append(p)
    return out


# ---------------------------------------------------------------- 各类型解析

def _load_pdf_bytes(data: bytes) -> list[dict]:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise LoadError("缺少 PyMuPDF 库,请在「环境检查」页安装后重试") from e
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise LoadError(f"无法打开 PDF 文件(可能已损坏): {e}") from e
    pages: list[dict] = []
    total_chars = 0
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        text = page.get_text("text")
        total_chars += len(text.strip())
        paras = _split_lines_to_paragraphs(text.splitlines()) if text.strip() else []
        if paras:
            pages.append({"page": pno + 1, "paragraphs": paras})
    doc.close()
    if total_chars < 40:
        raise LoadError(
            "这份 PDF 没有可提取的文字层(常见于扫描件/图片型 PDF),"
            "GreenRAG 暂不支持 OCR。请改用文字版 PDF、Word 或复制粘贴文本。"
        )
    return pages


def _load_docx_bytes(data: bytes, name: str) -> list[dict]:
    try:
        from docx import Document
    except ImportError as e:
        raise LoadError("缺少 python-docx 库,请在「环境检查」页安装后重试") from e
    try:
        d = Document(io.BytesIO(data))
    except Exception as e:
        raise LoadError(f"无法打开 Word 文档 {name}: {e}") from e
    paras: list[str] = []
    for p in d.paragraphs:
        t = _norm(p.text)
        if len(t) >= 2:
            paras.append(t)
    if not paras:
        raise LoadError("Word 文档中未读取到文字内容。")
    return [{"page": None, "paragraphs": paras}]


def _load_html_bytes(data: bytes) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise LoadError("缺少 beautifulsoup4 库,请在「环境检查」页安装后重试") from e
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "header", "footer", "nav"]):
        tag.decompose()
    paras: list[str] = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote", "td"]):
        t = _norm(el.get_text(" ", strip=True))
        if len(t) >= 2:
            paras.append(t)
    if not paras:
        # 兜底: 直接取 body 全文
        body = soup.find("body") or soup
        t = _norm(body.get_text(" ", strip=True))
        if t:
            paras.append(t)
    if not paras:
        raise LoadError("网页中未解析出正文内容。")
    return [{"page": None, "paragraphs": paras}]


def _load_txt_bytes(data: bytes) -> list[dict]:
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise LoadError("文本文件编码无法识别(仅支持 UTF-8 / GBK)。")
    paras = [_norm(p) for p in text.splitlines() if _norm(p)]
    if not paras:
        raise LoadError("文件中没有文本内容。")
    return [{"page": None, "paragraphs": paras}]


# ---------------------------------------------------------------- 统一入口

def parse_bytes(data: bytes, filename: str, url: str = "") -> dict:
    """解析任意受支持的文档,返回:
       {"name": 展示名, "kind": 类型, "pages": [..带页码段落..],
        "sha256": ..., "n_pages": 有文本的页数}
    识别失败时抛出 LoadError(消息面向用户)。
    """
    low = filename.lower()
    if low.endswith(".pdf"):
        kind, name, parsed = "pdf", filename, _load_pdf_bytes(data)
    elif low.endswith(".docx"):
        kind, name, parsed = "docx", filename, _load_docx_bytes(data, filename)
    elif low.endswith((".doc",)):
        raise LoadError(
            f"暂不支持旧版 .doc 格式。请用 Word/WPS 打开后「另存为 .docx」再上传。"
        )
    elif low.endswith((".txt", ".md", ".markdown")):
        kind = "md" if low.endswith((".md", ".markdown")) else "txt"
        name, parsed = filename, _load_txt_bytes(data)
    elif low.endswith((".html", ".htm")):
        kind, name, parsed = "html", filename, _load_html_bytes(data)
    elif url:
        kind, name, parsed = "url", url, _load_html_bytes(data)
    else:
        raise LoadError(
            f"暂不支持的文件类型(.{low.rsplit('.', 1)[-1] if '.' in low else ''})。"
            "支持: PDF / Word(.docx) / TXT / Markdown / HTML,或粘贴网页链接。"
        )
    n_pages = len(parsed)
    return {
        "name": name,
        "kind": kind,
        "pages": parsed,
        "n_pages": n_pages,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def fetch_url(url: str) -> dict:
    """抓取一个网页 URL(也支持直接指向 PDF 的链接)。"""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
        r.raise_for_status()
    except requests.exceptions.MissingSchema:
        raise LoadError("URL 格式不正确,请以 http:// 或 https:// 开头。")
    except requests.exceptions.Timeout:
        raise LoadError("网页请求超时,请检查网络后重试。")
    except requests.exceptions.RequestException as e:
        raise LoadError(f"网页抓取失败: {e}") from e

    ctype = r.headers.get("Content-Type", "").lower()
    low_url = urlparse(url).path.lower()
    data = r.content

    if "pdf" in ctype or low_url.endswith(".pdf"):
        parsed = _load_pdf_bytes(data)
        name = low_url.rsplit("/", 1)[-1] or url
        kind = "pdf"
    else:
        parsed = _load_html_bytes(data)
        kind = "url"
        name = url
    n_pages = len(parsed)
    return {
        "name": name,
        "kind": kind,
        "pages": parsed,
        "n_pages": n_pages,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
