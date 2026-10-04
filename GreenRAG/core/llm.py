"""LLM 对话层: 检索增强生成 (Augmented Generation)。

流程(对应教程里的 RAG 全链路):
  1. 把检索到的片段按 [1][2][3]... 编号拼进系统提示词(参考资料区);
  2. 告诉模型: 只能依据参考资料回答,并且回答里要标注引用编号 [n];
  3. 流式接收回答,结束后解析回答里出现的 [n],整理成「来源清单」
     (文档名 + 页码 + 摘要),前端就能展示"答案来自哪篇文档哪一页"。

同时内置一个 mock(演示模式)提供方: 不需要任何 API Key,
初学者可以先跑通"上传 → 提问 → 引用"全流程,再换成真实大模型。
"""
from __future__ import annotations

import json
import re
import time

import requests

# 常用国内服务商预设(展示在设置页下拉框,避免新手拼错 base_url)
PROVIDER_PRESETS = [
    {"label": "DeepSeek(深度求索)", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat", "note": "便宜好用; 不提供 embedding,嵌入请用本地模型"},
    {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-4-flash", "note": "提供 embedding 接口(embedding-3)"},
    {"label": "通义千问 Qwen(阿里云百炼)", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "note": "提供 text-embedding-v4 等嵌入模型"},
    {"label": "硅基流动 SiliconFlow", "base_url": "https://api.siliconflow.cn/v1",
     "model": "Qwen/Qwen2.5-7B-Instruct", "note": "兼容性好,也提供 BGE 系列嵌入"},
    {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o-mini", "note": "海外服务,需要可访问的网络"},
    {"label": "Ollama(本地)", "base_url": "http://localhost:11434/v1",
     "model": "qwen2.5:7b", "note": "本机离线跑模型,需先安装 Ollama 并拉取模型"},
    {"label": "内置演示模式(无需密钥)", "base_url": "", "model": "",
     "note": "mock: 用模板回答演示引用流程,不调用真实大模型"},
]


class LLMError(Exception):
    """面向用户的 LLM 错误。"""


def page_label(page: str) -> str:
    """把页码显示为人类可读形式。"""
    if not page:
        return ""
    return f"第{page}页"


def build_messages(llm_cfg: dict, retrieval_cfg: dict,
                   question: str, results: list[dict],
                   history: list[dict] | None = None) -> list[dict]:
    """组装发给大模型的消息。history: [{"role","content"},...] 最近几轮。"""
    kb_only = bool(retrieval_cfg.get("kb_only", True))
    custom = (llm_cfg.get("custom_system") or "").strip()

    # ---- 参考资料区 ----
    refs = []
    for i, r in enumerate(results, start=1):
        loc = f"{r['doc_name']}{' ' + page_label(r['page']) if r.get('page') else ''}"
        refs.append(f"[{i}] 来源: {loc}\n内容: {r['text']}")
    context = "\n\n".join(refs) if refs else "(知识库为空,没有任何参考资料)"

    if kb_only:
        base = (
            "你是一个严谨的中文知识库问答助手。请只依据下面提供的【参考资料】回答问题。\n"
            "规则:\n"
            "1. 回答中用到某条资料时,必须在对应句子的末尾标注引用编号,格式 [编号],例如:"
            " NVIDIA 的股价驱动因素之一是数据中心业务增长[1]。\n"
            "2. 引用编号只能使用参考资料中实际出现的 [1]~[N]。\n"
            "3. 如果参考资料与问题无关或没有任何资料,请直接回答:"
            "『知识库中暂未找到与该问题直接相关的内容。』并给出建议(换个问法/上传相关文档)。\n"
            "4. 引用时请尽量提及出处(文档名与页码),例如 (出自《xxx》第x页)。\n"
            "5. 回答使用中文,条理清晰,可以适度总结,但不要编造资料中没有的事实。"
        )
    else:
        base = (
            "你是一个乐于助人的中文助手。请优先依据【参考资料】回答;"
            "资料不足时,你可以结合自己的知识补充,但请明确区分"
            "『根据资料:…』与『补充说明:…』,并给资料内容标注引用编号 [编号]。"
        )
    if custom:
        base = custom + "\n\n" + base

    sys_msg = base + "\n\n【参考资料】\n" + context

    messages = [{"role": "system", "content": sys_msg}]
    for h in (history or [])[-2 * int(llm_cfg.get("history_turns", 0)):]:
        messages.append(h)
    messages.append({"role": "user", "content": question})
    return messages


# ------------------------------------------------------------------ 流式调用

def chat_stream(llm_cfg: dict, retrieval_cfg: dict, question: str,
                results: list[dict], history: list[dict] | None = None,
                max_tokens: int | None = None):
    """生成器,逐段产出事件 dict:
       {"type":"delta","text":...}
       {"type":"sources","sources":[{n,doc,page,text}]}   在回答结束时给出
       {"type":"done"}
       {"type":"error","message":...}
    """
    provider = llm_cfg.get("provider", "mock")
    if provider == "mock":
        yield from _mock_answer(llm_cfg, question, results)
        return

    messages = build_messages(llm_cfg, retrieval_cfg, question, results, history)

    # ---- 本地 GGUF 模型: 复用 OpenAI 兼容流式(引擎由 core.gguf 管理) ----
    if provider == "gguf":
        try:
            from . import gguf
            run = gguf.ensure_running(_full_cfg())
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return
        base = f"http://127.0.0.1:{run['port']}/v1"
        yield {"type": "meta", "engine": f"GGUF:{run['model']}",
               "layers": run["layers"], "ctx": run["ctx"]}
        lc = {**llm_cfg, "provider": "openai_compat", "base_url": base,
              "api_key": "", "model": run["model"]}
        yield from _stream_openai(lc, messages, results, max_tokens)
        return

    base = (llm_cfg.get("base_url") or "").rstrip("/")
    key = llm_cfg.get("api_key") or ""
    if not base:
        yield {"type": "error",
               "message": "尚未配置大模型 API(base_url 为空)。请到「设置 → 大模型」填写,"
                          "或选择『内置演示模式』先体验完整流程。"}
        return
    lc = dict(llm_cfg)
    yield from _stream_openai(lc, messages, results, max_tokens)


def _stream_openai(llm_cfg: dict, messages: list[dict], results: list[dict],
                   max_tokens: int | None):
    """向 OpenAI 兼容接口发起流式请求并产出统一事件。"""
    base = (llm_cfg.get("base_url") or "").rstrip("/")
    key = llm_cfg.get("api_key") or ""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": llm_cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "temperature": float(llm_cfg.get("temperature", 0.3)),
        "max_tokens": max_tokens or int(llm_cfg.get("max_tokens", 1200)),
        "stream": True,
    }
    try:
        resp = requests.post(f"{base}/chat/completions", json=body,
                             headers=headers, stream=True, timeout=(15, 600))
    except requests.exceptions.ConnectionError:
        yield {"type": "error",
               "message": f"无法连接 {base} —— 请检查网络,或确认服务商地址/base_url 是否正确"
                          "(Ollama 需先启动并确认模型已拉取;本地 GGUF 需先在设置中选择模型)。"}
        return
    except requests.exceptions.Timeout:
        yield {"type": "error", "message": "请求大模型超时,请稍后重试或降低 max_tokens。"}
        return

    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text[:300]
        except Exception:
            detail = resp.text[:300]
        hint = ("请检查 API Key 是否正确、模型名是否属于该服务商、账户余额是否充足。"
                if "127.0.0.1" not in base else
                "本地引擎拒绝了请求 —— 可到「设置 → 本地 GGUF」点『停止模型』后重试,"
                "或查看 data\\logs 下最新的 llama-server 日志。")
        yield {"type": "error", "message": f"模型服务返回 {resp.status_code}: {detail}\n{hint}"}
        return

    collected: list[str] = []
    for raw_b in resp.iter_lines():          # 按字节读,自己按 UTF-8 解码
        if not raw_b:
            continue
        raw = raw_b.decode("utf-8", errors="replace").strip()
        if raw.startswith("data:"):
            raw = raw[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        try:
            delta = obj["choices"][0].get("delta", {})
        except (KeyError, IndexError):
            continue
        piece = delta.get("content")
        if piece:
            collected.append(piece)
            yield {"type": "delta", "text": piece}
    full = "".join(collected)
    yield {"type": "sources", "sources": extract_citations(full, results)}
    yield {"type": "done"}


def _full_cfg() -> dict:
    from . import paths
    return paths.load_config()


def extract_citations(answer: str, results: list[dict]) -> list[dict]:
    """从回答文本中解析 [n] 引用 → 来源清单(与 results 一一对应)。"""
    used: dict[int, dict] = {}
    for m in re.finditer(r"[\[【（(]\s*(\d{1,2})\s*[\]】）)]", answer):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(results) and idx not in used:
            r = results[idx]
            used[idx] = {
                "n": idx + 1,
                "doc": r["doc_name"],
                "kind": r.get("doc_kind", ""),
                "page": r.get("page", ""),
                "doc_id": r.get("doc_id"),
                "chunk_id": r.get("id"),
                "text": r["text"][:140],
            }
    return list(used.values())


def chat_once(llm_cfg: dict, retrieval_cfg: dict, question: str,
              results: list[dict], history: list[dict] | None = None,
              max_tokens: int = 200) -> dict:
    """非流式短调用(用于「测试连接」),返回 {ok, text|error, ms}。"""
    provider = llm_cfg.get("provider", "mock")
    events = list(chat_stream(llm_cfg, retrieval_cfg, question, results,
                              history or [], max_tokens=max_tokens))
    t0 = time.time()
    buf = []
    for ev in events:
        if ev["type"] == "delta":
            buf.append(ev["text"])
        elif ev["type"] == "error":
            return {"ok": False, "error": ev["message"]}
    return {"ok": True, "text": "".join(buf)[:400], "ms": int((time.time() - t0) * 1000)}


# ------------------------------------------------------------------ 演示模式

def _mock_answer(llm_cfg: dict, question: str, results: list[dict]) -> iter:
    """内置演示模式: 不调用真实模型,演示「检索 + 引用 + 排版」的完整输出结构。"""
    q = (question or "").strip()
    note = ("> 🎓 当前为【内置演示模式】,回答由模板生成,并非真实大模型输出。"
            "到「设置」页填入任意 OpenAI 兼容服务的 API Key 后即可获得真实回答。\n\n")
    if not results:
        yield {"type": "delta", "text":
               "知识库中暂未找到与该问题直接相关的内容。\n\n建议: ① 换个说法提问; "
               "② 先在「知识库」页上传相关文档。"}
        yield {"type": "sources", "sources": []}
        yield {"type": "done"}
        return

    yield {"type": "delta", "text": note}
    head = f"你在「{results[0]['doc_name']}」等 {len(results)} 处资料中问到了"
    yield {"type": "delta", "text": f"「{q[:60]}」。\n\n"}
    yield {"type": "delta", "text": "本次混合检索命中的资料摘要如下:\n\n"}
    for i, r in enumerate(results):
        loc = f"{r['doc_name']}" + (f" 第{r['page']}页" if r.get("page") else "")
        snippet = r["text"][:110].replace("\n", " ")
        yield {"type": "delta",
               "text": f"- [{i+1}] {loc}: “{snippet}…”\n"}
    yield {"type": "delta", "text": "\n_以上为演示回答。配置真实大模型后,"
                                    "将在这里生成基于知识库的完整答案。_\n"}
    sources = [{"n": i + 1, "doc": r["doc_name"], "kind": r.get("doc_kind", ""),
                "page": r.get("page", ""), "doc_id": r.get("doc_id"),
                "chunk_id": r.get("id"), "text": r["text"][:140]}
               for i, r in enumerate(results[:3])]
    yield {"type": "sources", "sources": sources}
    yield {"type": "done"}
