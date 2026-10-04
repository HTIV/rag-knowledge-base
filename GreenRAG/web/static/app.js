/* ============================================================
   GreenRAG 前端逻辑
   视图: 工作台 / 知识库 / 问答 / 环境检查 / 设置 / 教程
   ============================================================ */
"use strict";

/* ---------------- 全局状态 ---------------- */
const S = {
  state: null,        // /api/state
  presets: [],        // 大模型服务商预设
  docs: [],
  env: null,          // /api/envcheck
  view: "home",
  tasks: {},          // 任务卡片展示表
  guideDone: 0,
  msgSeq: 0,
  chatBusy: false,
  chatAbort: null,
  chatHistory: [],    // 本会话 [{role,content}]
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const trunc = (s, n) => (s && s.length > n ? s.slice(0, n) + "…" : s);
const qr = (sel) => document.querySelector(sel);
const K = { pdf: "PDF", docx: "DOC", url: "URL", txt: "TXT", md: "MD", html: "HTM" };
const KI = { pdf: "▤", docx: "W", url: "◎", txt: "T", md: "M↓", html: "<>" };

const TITLES = {
  home: ["工作台", "看状态、学概念、开始你的第一个 RAG"],
  kb: ["知识库", "上传文档 → 自动分块 → 向量化入库(RAG 第 1 步)"],
  chat: ["智能问答", "混合检索 + 生成回答,引用标注到「文档 + 页码」"],
  env: ["环境检查", "逐项检测所需环境,缺失即给国内镜像下载入口"],
  settings: ["设置", "大模型 / 嵌入 / 检索参数 —— 每个参数都配了说明"],
  tutorial: ["学习教程", "从零学会 RAG:概念、手把手操作、进阶与 FAQ"],
};

/* ---------------- 基础 API ---------------- */
async function api(path, opts = {}) {
  const opt = { headers: {}, ...opts };
  if (opt.json !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(opt.json);
    delete opt.json;
  }
  const r = await fetch("/api" + path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { }
  if (!r.ok || (data && data.ok === false)) {
    throw new Error((data && data.message) || ("请求失败 HTTP " + r.status));
  }
  return data;
}

/* ---------------- 路由 ---------------- */
function showView(v) {
  if (!TITLES[v]) v = "home";
  S.view = v;
  if (["env", "settings", "kb", "chat"].includes(v)) markGuide(v);   // 任何入口到达即推进新手步骤
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  const sec = $("view-" + v);
  if (sec) sec.classList.add("active");
  document.querySelectorAll(".nav-item").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === v));
  $("pageTitle").textContent = TITLES[v][0];
  $("pageSub").textContent = TITLES[v][1];
  if (v === "tutorial") $("tutFrame").src = "/tutorial.html";
  if (v === "home") renderHome();
  if (v === "kb") refreshDocs();
  if (v === "env") refreshEnv();
  if (v === "chat") renderChatMeta();
  if (v === "settings") fillSettings();
}

/* ============================================================
   Toast / Modal / 新手引导
   ============================================================ */
function toast(msg, type = "info", ms = 4200) {
  const el = document.createElement("div");
  el.className = "toast" + (type === "err" ? " err" : "");
  el.innerHTML = (type === "err"
    ? '<svg viewBox="0 0 24 24"><path d="M12 8v5m0 3.5h.01M10.3 3.8 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.8a2 2 0 0 0-3.4 0Z"/></svg>'
    : '<svg viewBox="0 0 24 24"><path d="m4.5 12.5 5 5 10-11"/></svg>')
    + `<div>${esc(msg)}</div>`;
  $("toastRoot").appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .4s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 450);
  }, ms);
}

function openModal(title, html) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = html;
  $("modal").hidden = false;
}
function closeModal() { $("modal").hidden = true; }

document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-close]");
  if (c) closeModal();
  else if (e.target === $("modal")) closeModal();
  const h = e.target.closest(".help-head");
  if (h) h.closest(".help-card").classList.toggle("open");
});

function confirmBox(title, text, onOk, okLabel = "确认") {
  openModal(title, `
    <p style="font-size:14px;color:var(--txt2)">${esc(text)}</p>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:22px">
      <button class="btn outline" onclick="closeModal()">取消</button>
      <button class="btn primary danger" id="cfOk">${esc(okLabel)}</button>
    </div>`);
  $("cfOk").onclick = () => { closeModal(); onOk(); };
}

/* ---- 新手引导 ---- */
const GUIDE_STEPS = [
  { v: "env", t: "检查运行环境", p: "跑一遍检测清单 —— 缺什么旁边就有国内镜像下载链接,按提示补齐。" },
  { v: "settings", t: "配置大模型(可选)", p: "有 API Key 就选服务商填入;没有请保持「内置演示模式」,全流程照样能跑。" },
  { v: "kb", t: "上传第一篇文档", p: "拖入一份 PDF 或 Word,亲眼看着「解析 → 分块 → 向量化 → 入库」完成。" },
  { v: "chat", t: "提问并查看引用", p: "问文档里的内容,看回答的 [1][2] 引用,点「查看检索过程」理解混合检索。" },
];

function renderGuide() {
  $("guideSteps").innerHTML = "";
  GUIDE_STEPS.forEach((g, i) => {
    const d = document.createElement("div");
    d.className = "g-step" + (i < S.guideDone ? " done" : "");
    d.innerHTML = `<div class="g-no">${i + 1}</div>
      <div><b>${esc(g.t)}</b><p>${esc(g.p)}</p></div>`;
    d.onclick = () => { if (i === S.guideDone) { closeGuide(); location.hash = "#" + g.v; } };
    $("guideSteps").appendChild(d);
  });
  $("btnGuideGo").textContent = S.guideDone >= GUIDE_STEPS.length ? "重新学习一遍" : "开始 →";
}
function openGuide() { renderGuide(); $("guideMask").hidden = false; }
function closeGuide() {
  $("guideMask").hidden = true;
  if ($("guideSkip").checked) saveGuide();
}
function saveGuide() { try { localStorage.setItem("gr_guided", String(S.guideDone)); } catch (e) { } }
function markGuide(v) {
  const i = GUIDE_STEPS.findIndex((g) => g.v === v);
  if (i >= 0 && i === S.guideDone) { S.guideDone = i + 1; saveGuide(); }
}

/* ============================================================
   启动
   ============================================================ */
async function boot() {
  try { S.guideDone = Math.min(parseInt(localStorage.getItem("gr_guided") || "0", 10) || 0, 4); } catch (e) { }
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.addEventListener("click", () => { if (a.dataset.view !== "tutorial") markGuide(a.dataset.view); });
  });
  window.addEventListener("hashchange", () => showView((location.hash || "#home").slice(1)));
  $("heroGoKb").onclick = () => location.hash = "#kb";
  $("heroGoTut").onclick = () => location.hash = "#tutorial";
  $("heroGoChat").onclick = () => location.hash = "#chat";
  $("btnGuideReopen").onclick = openGuide;
  $("btnGuideGo").onclick = () => {
    if (S.guideDone >= GUIDE_STEPS.length) { S.guideDone = 0; renderGuide(); return; }
    closeGuide();
    location.hash = "#" + GUIDE_STEPS[S.guideDone].v;
  };
  $("btnOpenData").onclick = () => api("/open-data").catch((e) => toast(e.message, "err"));
  $("btnOpenData2").onclick = () => api("/open-data").catch((e) => toast(e.message, "err"));
  bindSettingsEvents();
  bindKbEvents();
  bindChatEvents();
  try {
    S.state = await api("/state");
    S.presets = (await api("/providers")).presets || [];
    fillPresets();
    await refreshDocs();
  } catch (e) {
    toast("连接本地服务失败: " + e.message, "err", 9000);
  }
  showView((location.hash || "#home").slice(1));
  refreshEnvSilent();
  if (S.guideDone < GUIDE_STEPS.length) setTimeout(openGuide, 450);
  startTaskPoller();
  startHwPoll();
  bindIslandEvents();
}
document.addEventListener("DOMContentLoaded", boot);

/* ============================================================
   状态渲染
   ============================================================ */
async function reloadState() {
  try { S.state = await api("/state"); } catch (e) { return; }
  renderChips();
  if (S.view === "home") renderHome();
  refreshModelStatus();
}

function dotH(kind) { return `<span class="dot ${kind}"></span>`; }

function renderChips() {
  const st = S.state;
  if (!st) return;
  $("chipEmbed").innerHTML = dotH(st.embed.ok ? "ok" : "bad") +
    `<span>嵌入: ${st.embed.mode === "api" ? "API" : "本地"}` +
    (st.embed.ok ? " ✓" : " · " + esc(st.embed.hint || "")) + "</span>";
  const okLlm = !!st.llm.usable;
  $("chipLlm").innerHTML = dotH(okLlm ? "ok" : "bad") +
    `<span>大模型: ${okLlm ? esc(st.llm.label) : "不可用(点设置修复)"}</span>`;
  $("chipLlm").title = st.llm.hint || "";
  const ready = !!(S.env && S.env.summary.ready);
  $("sideEnvText").textContent = ready ? "环境就绪 ✓" : (S.env ? `待补齐 ${S.env.summary.req_total - S.env.summary.req_ok} 项` : "未检测");
  const dot = $("sideEnvDot");
  dot.className = "dot" + (ready ? " ok" : (S.env ? " bad" : ""));
  const hasDoc = st.stats.docs > 0;
  const llmOk = okLlm;
  const fixView = st.llm.fix_view || "settings";
  if (!hasDoc) {
    $("chatBannerTitle").textContent = "知识库还是空的 —— 先去上传文档";
    $("chatBannerSub").textContent = "上传 PDF / Word / 网页后,这里会给出带引用标注的回答";
  } else if (!llmOk) {
    $("chatBannerTitle").textContent = "⚠ 当前大模型不可用,无法回答";
    $("chatBannerSub").innerHTML =
      `<span style="color:var(--amber)">${esc(st.llm.hint || "请在「设置」完成大模型配置")}</span>
       <a href="#${fixView}" style="margin-left:8px;font-weight:700">去修复 →</a>`;
    $("chatBanner").style.background = "#fff7e8";
  } else {
    $("chatBannerTitle").textContent = "知识库就绪,开始提问吧";
    $("chatBannerSub").textContent =
      `共 ${st.stats.docs} 篇文档 · ${st.stats.chunks} 个分块 · 回答将标注「文档 + 页码」`;
    $("chatBanner").style.background = "";
  }
  const hero = $("heroGoChat");
  hero.disabled = !(hasDoc && llmOk);
  hero.title = hero.disabled ? (hasDoc ? "当前大模型不可用,见顶部横幅提示" : "先上传至少一篇文档") : "";
  updateChatGate();
}

/* 大模型/知识库不可用时,禁用提问框并给出提示,避免"问了没反应" */
function updateChatGate() {
  const st = S.state;
  const ta = $("chatInput"), btn = $("btnSend");
  if (!st || !ta) return;
  const ok = st.stats.docs > 0 && !!st.llm.usable;
  ta.disabled = !ok;
  btn.disabled = !ok;
  ta.placeholder = !ok
    ? (st.stats.docs > 0 ? "大模型当前不可用,请先看上方提示完成配置" : "请先到「知识库」上传文档")
    : "输入问题,Enter 发送 · Shift+Enter 换行";
  const box = ta.closest(".chat-input");
  if (box) box.style.opacity = ok ? "1" : ".55";
}

/* ============================================================
   工作台
   ============================================================ */
function renderHome() {
  const st = S.state;
  if (!st) return;
  const envReady = !!(S.env && S.env.summary.ready);
  $("homeStats").innerHTML = `
    <div class="card stat"><b>${S.env ? S.env.summary.req_ok + "/" + S.env.summary.req_total : "?"}</b>
      <span>环境必备项就绪</span><small>到「环境检查」逐项补齐</small></div>
    <div class="card stat"><b>${st.stats.docs}</b><span>知识库文档</span><small>${st.stats.docs ? "已解析入库" : "尚未上传"}</small></div>
    <div class="card stat"><b>${st.stats.chunks}</b><span>检索分块</span><small>原文 + 向量 + 页码</small></div>
    <div class="card stat"><b>${(st.llm.configured || st.llm.provider === "mock") ? "可用" : "未配置"}</b>
      <span>大模型(对话)</span><small>${esc(st.llm.label)}</small></div>`;

  const flow = [
    ["01", "上传文档", "PDF / Word / 网页;PDF 精确记录页码", "kb"],
    ["02", "解析与分块", "切成小块、块间重叠、记住页码", "kb"],
    ["03", "嵌入向量化", "本地 bge 模型把文字变成向量", "settings"],
    ["04", "混合检索", "BM25 + 向量双路召回,RRF 融合", "chat"],
    ["05", "生成与引用", "大模型依据命中片段回答,标注 [n] 出处", "chat"],
  ];
  $("homeFlow").innerHTML = flow.map((f, i) =>
    `<div class="flow-step" style="cursor:pointer" onclick="location.hash='#${f[3]}'" title="前往「${TITLES[f[3]][0]}」">
      <span class="fs-no">${f[0]}</span><b>${f[1]}</b><p>${f[2]}</p></div>` +
    (i < flow.length - 1 ? '<div class="flow-arrow">→</div>' : "")).join("");

  const done0 = envReady, done1 = !!st.llm.usable,
        done2 = st.embed.ok, done3 = st.stats.docs > 0, done4 = S.chatHistory.length > 0;
  $("homeStepsHint").textContent = `已完成 ${[done0, done1, done2, done3, done4].filter(Boolean).length}/5`;
  const steps = [
    [done0, "检查环境", S.env ? `必备项 ${S.env.summary.req_ok}/${S.env.summary.req_total}` : "尚未检测", "#env"],
    [done1, "配置大模型", st.llm.label || "", "#settings"],
    [done2, "就绪嵌入模型", st.embed.hint || "", "#settings"],
    [done3, "上传文档", st.stats.docs ? `${st.stats.docs} 篇 / ${st.stats.chunks} 块` : "还没有文档", "#kb"],
    [done4, "完成一次带引用的问答", done4 ? "已问过 ✓" : (st.stats.docs ? "去问第一个问题" : "先上传文档"), "#chat"],
  ];
  $("homeSteps").innerHTML = steps.map((s) => `
    <li class="${s[0] ? "done" : ""}">
      <div style="flex:1"><b>${s[1]}</b><small>${esc(s[2])}</small>
        <div class="st-act"><button class="btn outline sm" onclick="location.hash='${s[3]}'">
          ${s[0] ? "查看" : "去完成 →"}</button></div></div></li>`).join("");

  const docs = S.docs.slice(0, 4);
  $("homeKbMin").innerHTML = docs.length ? `
    <div style="padding:2px 18px 16px">
      ${docs.map((d) => `<div class="doc-row" style="padding:9px 2px">
        <div class="doc-ic ${esc(d.kind)}">${esc(KI[d.kind] || "•")}<span>${esc(K[d.kind] || d.kind)}</span></div>
        <div class="doc-info"><b>${esc(d.name)}</b>
          <p>${d.n_chunks} 个分块${d.pages ? " · " + d.pages + " 页" : ""}</p></div>
        <button class="btn ghost sm" onclick="viewDocChunks(${d.id})">查看</button>
      </div>`).join("")}
      <button class="btn outline sm" style="width:100%" onclick="location.hash='#kb'">管理全部 →</button>
    </div>` : `
    <div class="empty" style="padding:26px"><b>暂无文档</b>
      <p>上传后这里会列出最近文档与分块概况</p>
      <button class="btn primary sm" style="margin-top:12px" onclick="location.hash='#kb'">去上传 →</button></div>`;
}

/* ============================================================
   知识库
   ============================================================ */
function bindKbEvents() {
  const dz = $("dropzone");
  dz.onclick = () => $("fileInput").click();
  $("fileInput").onchange = (e) => uploadFiles(e.target.files);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove("over");
  }));
  dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
  $("btnFetchUrl").onclick = fetchUrl;
  $("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") fetchUrl(); });
  $("btnRefreshDocs").onclick = refreshDocs;
  $("btnReindex").onclick = () => confirmBox("重建全部索引",
    "将按当前「分块大小 / 重叠 / 嵌入方式」重新解析并向量化全部文档(原始文件已备份,不会丢失)。",
    async () => {
      try {
        const r = await api("/docs/reindex", { method: "POST" });
        appendTask(r.task, "taskArea");
        toast("已开始重建索引,请留意任务进度");
      } catch (e) { toast(e.message, "err"); }
    }, "开始重建");
}

async function uploadFiles(fileList) {
  const files = [...fileList].filter((f) => f.size <= 512 * 1024 * 1024);
  if (!files.length) return;
  if (files.length !== fileList.length) toast("部分文件超过 512MB 上限,已跳过", "err");
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  try {
    const r = await fetch("/api/docs/upload", { method: "POST", body: fd });
    const data = await r.json();
    if (!data.ok) { toast(data.message || "上传失败", "err", 8000); return; }
    appendTask(data.task, "taskArea");
    toast(`已开始处理 ${files.length} 个文件: 解析 → 分块 → 向量化`);
  } catch (e) { toast("上传失败: " + e.message, "err", 8000); }
  $("fileInput").value = "";
}

async function fetchUrl() {
  const url = $("urlInput").value.trim();
  if (!url) { toast("先粘贴一个网页链接(https://…)再点抓取", "err"); return; }
  if (!/^https?:\/\//i.test(url)) { toast("链接需以 http:// 或 https:// 开头", "err"); return; }
  const fd = new FormData();
  fd.append("url", url);
  const btn = $("btnFetchUrl");
  btn.disabled = true; btn.textContent = "抓取中…";
  try {
    const r = await fetch("/api/docs/upload", { method: "POST", body: fd });
    const data = await r.json();
    if (!data.ok) { toast(data.message || "抓取失败", "err", 8000); return; }
    appendTask(data.task, "taskArea");
    $("urlInput").value = "";
  } catch (e) { toast("请求失败: " + e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "抓取网页"; }
}

async function refreshDocs() {
  try { S.docs = (await api("/docs")).docs || []; }
  catch (e) { return; }
  renderDocs();
  renderChips();
  renderChatMeta();
}
function renderDocs() {
  const box = $("docList");
  $("kbCount").textContent = S.docs.length + " 篇";
  box.hidden = !S.docs.length;
  $("kbEmpty").hidden = !!S.docs.length;
  if (S.state && S.state.index_stale) {
    $("kbStale").innerHTML = '<span style="color:var(--amber);font-weight:700">⚠ 分块参数已修改,索引待重建(点右上角按钮)</span>';
  } else $("kbStale").innerHTML = "";
  if (!S.docs.length) return;
  box.innerHTML = S.docs.map((d) => {
    const t = new Date(d.created_at * 1000);
    const ts = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
    return `<div class="doc-row">
      <div class="doc-ic ${esc(d.kind)}">${esc(KI[d.kind] || "•")}<span>${esc(K[d.kind] || d.kind)}</span></div>
      <div class="doc-info">
        <b title="${esc(d.name)}">${esc(d.name)}</b>
        <p><span>${d.n_chunks} 个分块</span>
          ${d.pages ? `<span>${d.pages} 页</span>` : ""}
          ${d.url ? `<span style="max-width:220px;overflow:hidden;text-overflow:ellipsis;display:inline-block;vertical-align:bottom">${esc(d.url)}</span>` : ""}
          <span>${ts}</span></p>
      </div>
      <div class="doc-acts">
        <button class="btn ghost sm" onclick="viewDocChunks(${d.id})">查看分块</button>
        <button class="btn ghost sm" title="打开原文" onclick="openOrigin(${d.id})">打开原文</button>
        <button class="btn ghost sm" style="color:var(--red)" onclick="delDoc(${d.id})">删除</button>
      </div></div>`;
  }).join("");
}

/* ---- 分块查看 ---- */
async function viewDocChunks(docId) {
  try {
    const data = await api(`/docs/${docId}`);
    const d = data.doc, chunks = data.chunks;
    S._chunks = chunks;
    openModal(`分块详情: 《${d.name}》(${chunks.length} 块)`, `
      <div class="doc-viewer-head">
        <div style="flex:1;min-width:0">
          <b style="font-size:14.5px">${esc(d.name)}</b>
          <p style="font-size:12px;color:var(--mut);margin-top:2px">
            ${d.kind === "pdf" ? "✅ PDF 分块保留了真实页码 —— 引用标注将精确到页" :
              "⚠ 该格式没有真实页码概念,引用时只标注文档名"}</p>
        </div>
        <button class="btn outline sm" onclick="openOrigin(${d.id})">打开原文</button>
      </div>
      <div class="mini-input">
        <input id="chunkSearch" placeholder="搜索块内关键词(如: 数据安全)">
        <button class="btn primary sm" onclick="filterChunks()">搜索</button>
      </div>
      <div id="chunkRows">${chunkRowsHtml(chunks, "")}</div>`);
    $("chunkSearch").onkeydown = (e) => { if (e.key === "Enter") filterChunks(); };
  } catch (e) { toast(e.message, "err"); }
}
function chunkRowsHtml(chunks, q) {
  if (!chunks.length) return `<div class="empty"><b>${q ? "无匹配块" : "暂无分块"}</b></div>`;
  return chunks.map((c) => `
    <div class="chunk-row">
      <div class="chunk-head">
        <span class="c-seq">块 #${c.seq + 1}</span>
        <span>字符 ${c.text.length}</span>
        <span class="c-page">${c.page ? "📄 第 " + esc(c.page) + " 页" : "页码:—"}</span>
      </div>
      <div class="chunk-body">${esc(c.text)}</div>
    </div>`).join("") +
    (q ? `<div class="hint" style="padding:4px 2px">「${esc(q)}」命中 ${chunks.length} 块</div>` : "");
}
function filterChunks() {
  const q = ($("chunkSearch").value || "").trim();
  const all = S._chunks || [];
  $("chunkRows").innerHTML = chunkRowsHtml(q ? all.filter((c) => c.text.includes(q)) : all, q);
}

async function openOrigin(docId) {
  try { await api(`/docs/${docId}/open`, { method: "POST" }); }
  catch (e) { toast(e.message, "err"); }
}
function delDoc(docId) {
  const d = S.docs.find((x) => x.id === docId);
  if (!d) return;
  confirmBox("删除文档",
    `确定从知识库中彻底删除《${d.name}》吗?其 ${d.n_chunks} 个分块与向量将一并移除,不可恢复。`,
    async () => {
      try {
        await api(`/docs/${docId}`, { method: "DELETE" });
        toast("已删除");
        await reloadState();
      } catch (e) { toast(e.message, "err"); }
    }, "删除");
}

/* ---- 任务轮询 ---- */
function appendTask(t, areaId = "taskArea") {
  if (S.tasks[t.id]) return;
  S.tasks[t.id] = t;
  const el = document.createElement("div");
  el.className = "task-card";
  el.id = "task-" + t.id;
  el.dataset.state = t.state;
  el.innerHTML = taskHtml(t);
  if (areaId === "modelTaskArea") el.style.margin = "0 18px 8px";
  const box = $(areaId);
  box.insertBefore(el, box.firstChild);
}
function taskHtml(t) {
  const line = `<div class="task-line"><span class="spin"></span><span>${esc(t.title)}</span>
      <span class="task-stage">${esc(t.stage || "排队中…")}</span></div>
    <div class="task-detail" id="td-${t.id}">${esc(t.detail || "")}</div>
    <div class="task-bar"><i style="width:${Math.max(2, (t.progress || 0) * 100)}%"></i></div>`;
  return line;
}
function updateTaskEl(t) {
  const el = document.getElementById("task-" + t.id);
  if (!el) return;
  const det = el.querySelector(".task-detail");
  if (det) det.textContent = (t.stage ? t.stage + " · " : "") + (t.detail || "");
  const bar = el.querySelector(".task-bar i");
  if (bar) bar.style.width = Math.max(2, t.progress * 100) + "%";
  const spin = el.querySelector(".task-line .spin");
  const stage = el.querySelector(".task-line .task-stage");
  if (t.state === "done") {
    el.dataset.state = "done";
    spin.className = "spin done";
    if (stage) { stage.textContent = "完成 ✓"; stage.style.color = "var(--g-d)"; }
  } else if (t.state === "error") {
    el.dataset.state = "err";
    spin.className = "spin err";
    if (stage) { stage.textContent = "失败"; stage.style.color = "var(--red)"; }
    if (det) { det.style.color = "var(--red)"; det.style.whiteSpace = "pre-wrap"; }
  }
}
function startTaskPoller() {
  setInterval(async () => {
    let data;
    try { data = await api("/tasks"); }
    catch (e) { return; }
    const list = data.tasks || [];
    for (const t of list) {
      const prev = S.tasks[t.id];
      if (!prev) {
        appendTask(t, t.kind === "model" ? "modelTaskArea"
          : (t.kind === "gguf" ? "ggufTaskArea" : "taskArea"));
        if (t.state === "done") onTaskDone(t);
        else if (t.state === "error") onTaskFail(t);
        continue;
      }
      S.tasks[t.id] = t;
      if (t.state === prev.state) { updateTaskEl(t); continue; }
      updateTaskEl(t);
      if (t.state === "done") onTaskDone(t);
      else if (t.state === "error") onTaskFail(t);
    }
    for (const id of Object.keys(S.tasks)) {
      const el = document.getElementById("task-" + id);
      if (el && !list.some((t) => t.id === id)) { el.remove(); delete S.tasks[id]; }
    }
  }, 1000);
}
function onTaskDone(t) {
  toast("完成: " + t.title);
  if (t.kind === "model") { refreshModelStatus(); refreshEnvSilent(true); }
  if (t.kind === "gguf") { refreshGgufInfo(); reloadState(); fetchHw(); }
  if (t.kind === "upload" || t.kind === "reindex") {
    const res = t.result || {};
    (res.errors || []).forEach((er) =>
      setTimeout(() => toast(`《${er.file}》处理失败: ${er.error}`, "err", 9000), 300));
    reloadState();
    refreshDocs();
  }
}
function onTaskFail(t) {
  toast("任务失败: " + t.title + " — " + (t.error || t.detail || "").slice(0, 400), "err", 12000);
  const el = document.getElementById("task-" + t.id);
  if (el) { const d = el.querySelector(".task-detail"); if (d) d.textContent = t.error || t.detail || ""; }
}

/* ============================================================
   智能问答
   ============================================================ */
function bindChatEvents() {
  const ta = $("chatInput");
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(150, ta.scrollHeight) + "px"; };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
  });
  $("btnSend").onclick = sendChat;
  $("kbOnlyToggle").onchange = async (e) => {
    try {
      await api("/config", { method: "POST", json: { retrieval: { kb_only: e.target.checked } } });
      if (S.state.config) S.state.config.retrieval.kb_only = e.target.checked;
      toast(e.target.checked ? "已开启「仅依据知识库回答」" : "已关闭: 模型可结合自身知识补充(会标注区分)");
    } catch (err) { toast(err.message, "err"); }
  };
}

function renderChatMeta() {
  const st = S.state;
  if (!st || !$("chatEmpty")) return;
  const box = $("suggChips");
  box.innerHTML = "";
  if (st.stats.docs > 0) {
    S.docs.slice(0, 3).forEach((d) => {
      const c = document.createElement("button");
      c.className = "c-chip";
      c.textContent = `《${trunc(d.name, 16)}》讲了什么?`;
      c.onclick = () => { $("chatInput").value = c.textContent; sendChat(); };
      box.appendChild(c);
    });
    const c2 = document.createElement("button");
    c2.className = "c-chip";
    c2.textContent = "总结知识库的核心要点";
    c2.onclick = () => { $("chatInput").value = c2.textContent; sendChat(); };
    box.appendChild(c2);
  } else {
    box.innerHTML = '<span class="hint">上传文档后,这里会出现示例问题</span>';
  }
  const t = $("kbOnlyToggle");
  if (st.config && st.config.retrieval) t.checked = !!st.config.retrieval.kb_only;
}

async function sendChat() {
  const ta = $("chatInput");
  const q = ta.value.trim();
  if (!q || S.chatBusy) return;
  const st = S.state;
  if (!st || st.stats.docs === 0) {
    toast("知识库还是空的 —— 先到「知识库」页上传文档", "err");
    location.hash = "#kb";
    return;
  }
  if (!st.llm.usable) {
    toast((st.llm.hint || "大模型不可用") + " —— 正在为你打开修复页面…", "err", 5000);
    location.hash = "#" + (st.llm.fix_view || "settings");
    return;
  }
  ta.value = ""; ta.style.height = "auto";
  S.chatBusy = true;
  S.chatAbort = new AbortController();
  const uid = "m" + (++S.msgSeq);
  $("chatShell").classList.add("chat-busy");
  $("btnSend").disabled = true;
  $("chatEmpty")?.remove();

  // 用户气泡
  const uMsg = el(`<div class="msg user"><div class="msg-ava">我</div><div class="msg-body">
    <div class="msg-bubble">${esc(q)}</div><div class="msg-foot"></div></div>`);
  $("chatLog").appendChild(uMsg);

  // AI 占位气泡
  const aMsg = el(`<div class="msg ai"><div class="msg-ava">
      <svg viewBox="0 0 24 24"><path d="M12 3 5 5.6v5.3c0 4.5 3 8.4 7 10 4-1.6 7-5.5 7-10V5.6L12 3Z"/></svg></div>
    <div class="msg-body">
      <div class="msg-bubble"><div class="md"></div><span class="cursor"></span></div>
      <div class="msg-foot"><a class="stop-link" style="cursor:pointer">■ 停止</a></div></div>`);
  $("chatLog").appendChild(aMsg);
  scrollLog();
  aMsg.querySelector(".stop-link").onclick = () => { try { S.chatAbort.abort(); } catch (e) { } };

  const mdBox = aMsg.querySelector(".md");
  let text = "";
  let retData = null;

  const upd = () => { mdBox.innerHTML = renderMarkdown(text, uid); scrollLog(); };
  const endMark = () => { const c = mdBox.querySelector(".cursor"); if (c) c.remove(); };

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: lastHistory() }),
      signal: S.chatAbort.signal,
    });
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.message || "HTTP " + resp.status);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const ln of lines) {
        let ev; try { ev = JSON.parse(ln); } catch (e) { continue; }
        if (ev.type === "retrieval") { retData = ev; addRetrievalPanel(aMsg, ev, uid); }
        else if (ev.type === "delta") { text += ev.text; upd(); }
        else if (ev.type === "sources") { text = text; addSources(aMsg, ev.sources || [], uid); }
        else if (ev.type === "error") { throw new Error(ev.message); }
      }
    }
    S.chatHistory.push({ role: "user", content: q });
    S.chatHistory.push({ role: "assistant", content: text });
    endMark(); upd();
    markGuide("chat");
  } catch (err) {
    endMark();
    const isAbort = err.name === "AbortError";
    if (!isAbort) {
      mdBox.innerHTML += `<p style="color:var(--red);margin-top:8px">⚠ ${esc(err.message)}</p>`;
      S.chatHistory.push({ role: "user", content: q });
    } else {
      mdBox.innerHTML = renderMarkdown(text + "\n\n_已停止生成_", uid);
      S.chatHistory.push({ role: "user", content: q });
      S.chatHistory.push({ role: "assistant", content: text });
    }
    if (!retData) {
      const foot = aMsg.querySelector(".msg-foot");
      if (foot) foot.innerHTML = "";
    }
  } finally {
    S.chatBusy = false;
    $("chatShell").classList.remove("chat-busy");
    $("btnSend").disabled = false;
    scrollLog();
  }
}

function lastHistory() {
  const turns = Math.max(0, parseInt(S.state?.config?.llm?.history_turns) || 2);
  if (!turns) return [];
  return S.chatHistory.slice(-turns * 2).map((h) => ({
    role: h.role, content: String(h.content).slice(0, 4000),
  }));
}

function el(html) { const d = document.createElement("div"); d.innerHTML = html; return d.firstElementChild; }
function scrollLog() { const lg = $("chatLog"); lg.scrollTop = lg.scrollHeight; }

/* ---- 检索详情面板 ---- */
function addRetrievalPanel(aMsg, ev, uid) {
  const { results, detail, ms } = ev;
  if (!results || !results.length) return;
  const wrap = el(`<div class="retrieval-panel" data-uid="${uid}">
    <div class="ret-head"><span>🔍 查看检索过程</span>
      <span class="hint">共 ${ms}ms · 向量 ${detail.dense_ms}ms + 关键词 ${detail.bm25_ms}ms + 融合 ${detail.rrf_ms}ms</span></div>
    <div class="ret-body" style="display:none"></div></div>`);
  aMsg.querySelector(".msg-body").appendChild(wrap);
  const body = wrap.querySelector(".ret-body");
  body.innerHTML = `
    <div class="ret-meta">
      <span>问题分词: ${(detail.query_tokens || []).slice(0, 14).map((t) => "<b>" + esc(t) + "</b>").join(" ")}</span>
      <span>库内分块: <b>${detail.n_chunks ?? "—"}</b></span>
      <span>最佳余弦: <b>${detail.best_cosine ?? "—"}</b></span>
      <span>命中判定: <b style="color:${detail.covered ? "var(--g-d)" : "var(--amber)"}">
        ${detail.covered ? "有相关内容 ✓" : "相似度过低 → 模型回答『未找到』"}</b></span>
    </div>
    ${results.map((r, i) => `
      <div class="ret-item" data-n="${i + 1}">
        <div class="ret-top">
          <span class="ret-rank">第${i + 1}名</span>
          <span class="ret-loc" title="${esc(r.doc_name)}">📄 ${esc(trunc(r.doc_name, 30))}${r.page ? " · 第" + esc(r.page) + "页" : ""}</span>
          <span class="ret-badges">
            <span class="rb dense" title="向量检索名次">向量#${r.dense_rank != null ? r.dense_rank + 1 : "—"}</span>
            <span class="rb bm25" title="BM25 关键词名次">关键词#${r.bm25_rank != null ? r.bm25_rank + 1 : "—"}</span>
            <span class="rb rrf" title="RRF 融合得分">RRF ${r.rrf}</span>
            <span class="rb ${r.dense_score != null ? "dense" : "none"}">cos ${r.dense_score ?? "—"}</span>
          </span>
        </div>
        <div class="ret-txt">${esc(r.text)}</div>
        ${(r.hit_tokens || []).length ? `<div class="ret-hits">命中关键词: ${r.hit_tokens.map((t) => "<i>" + esc(t) + "</i>").join("")}</div>` : ""}
      </div>`).join("")}
    <div class="hint" style="padding:9px 15px;background:var(--g-soft2);border-top:1px solid var(--line)">
      💡 名次为 — 表示该路检索没召回它。RRF 融合: 两路都靠前的块总分才高 —— 这就是「混合检索」的意义。
    </div>`;
  wrap.querySelector(".ret-head").onclick = () => {
    body.style.display = body.style.display === "none" ? "block" : "none";
  };
}
function showRetPanel(uid, n) {
  const panel = document.querySelector(`.retrieval-panel[data-uid="${uid}"]`);
  if (!panel) return;
  const b = panel.querySelector(".ret-body");
  b.style.display = "block";
  if (n) {
    const item = panel.querySelector(`.ret-item[data-n="${n}"]`);
    if (item) {
      item.scrollIntoView({ behavior: "smooth", block: "center" });
      item.style.background = "var(--g-soft)";
      setTimeout(() => { item.style.background = ""; }, 1700);
    }
  }
}
/* ---- 引用来源 chips ---- */
function addSources(aMsg, sources, uid) {
  const foot = aMsg.querySelector(".msg-foot");
  if (!foot || !sources.length) return;
  foot.innerHTML = "";
  const wrap = el(`<div class="sources-wrap"><b>答案引用的来源(点击定位到检索列表)</b>
    <div class="sources">${sources.map((s) => `
      <div class="src-chip" data-uid="${uid}" data-n="${s.n}" title="点击查看对应检索片段">
        <span class="n">${s.n}</span>
        <span class="doc">${esc(trunc(s.doc, 24))}</span>
        ${s.page ? `<span class="pg">第${esc(s.page)}页</span>` : ""}
      </div>`).join("")}</div></div>`);
  foot.appendChild(wrap);
}
/* 全局委托: 引用角标 / 来源 chips 点击 */
document.addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (cite) { showRetPanel(cite.dataset.uid, parseInt(cite.dataset.n, 10)); return; }
  const chip = e.target.closest(".src-chip");
  if (chip) { showRetPanel(chip.dataset.uid, parseInt(chip.dataset.n, 10)); return; }
});

/* ============================================================
   Markdown 渲染(简化安全版 + [n] 引用角标)
   ============================================================ */
function renderMarkdown(src, uid) {
  src = String(src || "");
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  let html = "";
  let inCode = false, codeBuf = [], inP = false, inUl = false, inOl = false;

  const closeP = () => { if (inP) { html += "</p>"; inP = false; } };
  const flushCode = () => {
    if (inCode) { html += `<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`; inCode = false; codeBuf = []; }
  };
  const inline = (t) => {
    t = esc(t);
    t = t.replace(/\[(\d{1,2})\]/g, (_, n) => `<sup class="cite" data-uid="${uid}" data-n="${n}">${n}</sup>`);
    t = t.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>");
    t = t.replace(/`([^`]+)`/g, '<code class="inl">$1</code>');
    return t;
  };

  for (const raw of lines) {
    const line = raw;
    const fm = line.match(/^```(\w*)/);
    if (fm) {
      if (!inCode) { closeP(); flushCode(); inCode = true; codeBuf = []; }
      else flushCode();
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    if (!line.trim()) { closeP(); inUl = inOl = false; continue; }
    if (/^#{1,4}\s/.test(line)) {
      closeP();
      const lv = line.match(/^(#{1,4})\s/)[1].length;
      html += `<h${lv}>${inline(line.replace(/^#{1,4}\s/, ""))}</h${lv}>`;
    } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeP(); html += "<hr>"; }
    else if (/^>\s?/.test(line)) {
      closeP(); html += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`;
    } else if (/^[-*]\s+/.test(line)) {
      closeP();
      if (!inUl) { html += "<ul>"; inUl = true; }
      html += `<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`;
    } else if (/^\d+[.、)]\s+/.test(line)) {
      closeP();
      if (!inOl) { html += "<ol>"; inOl = true; }
      html += `<li>${inline(line.replace(/^\d+[.、)]\s+/, ""))}</li>`;
    } else {
      if (inUl) { html += "</ul>"; inUl = false; }
      if (inOl) { html += "</ol>"; inOl = false; }
      if (!inP) { html += "<p>"; inP = true; }
      html += (inP ? (html.endsWith("<p>") ? "" : "<br>") : "") + inline(line);
    }
  }
  closeP(); flushCode();
  if (inUl) html += "</ul>";
  if (inOl) html += "</ol>";
  return html;
}

/* ============================================================
   环境检查
   ============================================================ */
let envBusy = false;
async function refreshEnv() {
  if (envBusy) return;
  envBusy = true;
  try {
    const d = await api("/envcheck");
    S.env = { summary: d.summary, items: d.items };
    renderEnv();
    renderChips();
  } catch (err) { toast("环境检测失败: " + err.message, "err"); }
  finally { envBusy = false; }
}
async function refreshEnvSilent(force = false) {
  if (S.env && !force) return;
  try {
    const d = await api("/envcheck");
    S.env = { summary: d.summary, items: d.items };
    if (S.view === "env") renderEnv();
    if (S.view === "home") renderHome();   // 环境结果到达后刷新工作台统计
    renderChips();
  } catch (e) { }
}
function renderEnv() {
  const s = S.env.summary;
  const pct = s.req_total ? Math.round((s.req_ok / s.req_total) * 100) : 0;
  const R = 38, C = 2 * Math.PI * R;
  $("envHead").innerHTML = `
    <div class="env-ring">
      <svg width="86" height="86">
        <circle cx="43" cy="43" r="${R}" fill="none" stroke="#e6edda" stroke-width="7"/>
        <circle cx="43" cy="43" r="${R}" fill="none"
          stroke="${s.ready ? "#76B900" : "#d99a12"}" stroke-width="7" stroke-linecap="round"
          stroke-dasharray="${(pct / 100) * C} ${C}" transform="rotate(-90 43 43)"/>
      </svg>
      <div class="er-txt"><b style="color:${s.ready ? "var(--g-d)" : "#b8860b"}">${pct}%</b><span>必备项</span></div>
    </div>
    <div class="env-sum-txt">
      <b>${s.ready ? "🎉 环境全部就绪,放心使用!" : `还差 ${s.req_total - s.req_ok} 项必备环境`}</b>
      <p>共 ${s.total} 项 · 必备 ${s.req_total}(✓ ${s.req_ok}) · 可选 ${s.total - s.req_total}
         — 每个红色条目右侧都有「国内镜像链接 / 一键复制命令」。</p>
    </div>
    <div class="env-actions"><button class="btn primary" onclick="refreshEnv()">↻ 重新检测</button></div>`;
  const cats = ["基础环境", "系统组件", "Python 依赖", "模型资源", "数据目录"];
  $("envGroups").innerHTML = cats.map((cat) => {
    const items = S.env.items.filter((i) => i.cat === cat);
    if (!items.length) return "";
    const okN = items.filter((i) => i.ok).length;
    return `<div class="env-group">
      <div class="env-group-title"><span class="gl"></span>${cat}
        <span class="env-group-count">${okN}/${items.length} 就绪</span></div>
      <div class="env-items">${items.map(envItemHtml).join("")}</div></div>`;
  }).join("");
}
function envItemHtml(i) {
  const icon = i.ok ? '<div class="env-icon g">✓</div>'
    : (i.required ? '<div class="env-icon r">✗</div>' : '<div class="env-icon a">⚠</div>');
  const req = !i.ok && i.required ? '<span class="req">必须补齐</span>' : "";
  const acts = (i.actions || []).map((a) =>
    a.url
      ? `<a class="act-btn ${a.primary ? "primary" : ""}" data-url="${esc(a.url)}" target="_blank" rel="noreferrer">↗ ${esc(a.label)}</a>`
      : `<button class="act-btn ${a.primary ? "primary" : ""}" data-copy="${esc(a.copy)}" data-act="${esc(a.label)}">📋 ${esc(a.label)}</button>`
  ).join("");
  return `<div class="env-item ${i.ok ? "ok" : "bad"}">
    ${icon}
    <div class="env-mid">
      <b>${esc(i.name)} ${req}</b>
      <p>${esc(i.desc)}</p>
      ${i.detail ? `<div class="detail">${esc(i.detail)}</div>` : ""}
    </div>
    ${acts ? `<div class="env-acts">${acts}</div>` : ""}</div>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  try {
    await navigator.clipboard.writeText(b.dataset.copy);
    const old = b.textContent;
    b.textContent = "✓ 已复制";
    b.classList.add("env-copy-ok");
    setTimeout(() => { b.textContent = old; b.classList.remove("env-copy-ok"); }, 1700);
  } catch (err) {
    toast("复制失败(浏览器限制),请手动复制", "err");
  }
});

/* ============================================================
   设置
   ============================================================ */
function fillPresets() {
  const sel = $("sllmPreset");
  sel.innerHTML = '<option value="">— 手动填写(或用演示模式)—</option>' +
    S.presets.map((p) =>
      `<option value="${esc(p.base_url)}|${esc(p.model)}">${esc(p.label)} — ${esc(p.model)}</option>`).join("");
  sel.onchange = () => {
    const v = sel.value;
    if (!v) return;
    const [base, model] = v.split("|");
    if (!S.state.config) return;
    S.state.config.llm.provider = "openai_compat";
    S.state.config.llm.base_url = base;
    S.state.config.llm.model = model;
    fillSettings();
    toast("预设已填充: 填入 API Key 后点「测试连接」验证");
    $("sllmKey").focus();
  };
}

function fillSettings() {
  const cfg = S.state?.config;
  if (!cfg) return;
  const l = cfg.llm || {}, e = cfg.embedding || {}, r = cfg.retrieval || {},
        a = cfg.advanced || {};
  setRadio("llmProvider", l.provider === "mock" ? "mock"
    : (l.provider === "gguf" ? "gguf" : "openai_compat"));
  syncProviderBoxes();
  $("sllmBase").value = l.base_url || "";
  $("sllmKey").value = "";
  $("sllmKey").placeholder = l.api_key_set
    ? `已保存密钥 ${l.api_key_masked} · 输入新值可替换` : "sk-…(可选,演示模式不需要)";
  $("sllmModel").value = l.model || "";
  setRange("sllmTemp", l.temperature ?? 0.3, "sllmTempV", (v) => Number(v).toFixed(2));
  $("sllmMaxTk").value = l.max_tokens ?? 1200;
  $("sllmHist").value = l.history_turns ?? 2;
  $("sllmCustom").value = l.custom_system || "";

  const gg = cfg.gguf || {};
  $("sggufDir").value = gg.dir || "";
  $("sggufCtx").value = gg.n_ctx ?? 4096;
  setRange("sggufLayers", gg.layers ?? 0, "sggufLayersV");
  refreshGgufInfo();

  setRadio("embMode", e.mode === "api" ? "api" : "local");
  $("embLocalBox").hidden = e.mode !== "local";
  $("embApiBox").hidden = e.mode !== "api";
  $("sembModel").value = e.model || "BAAI/bge-small-zh-v1.5";
  $("sembApiBase").value = e.api_base || "";
  $("sembApiKey").value = "";
  $("sembApiKey").placeholder = e.api_key_set ? `已保存密钥 ${e.api_key_masked}` : "";
  $("sembApiModel").value = e.api_model || "";

  setRange("srChunkSize", r.chunk_size ?? 400, "srChunkSizeV");
  setRange("srOverlap", r.chunk_overlap ?? 80, "srOverlapV");
  setRange("srTopk", r.top_k ?? 6, "srTopkV");
  $("srBm25Top").value = r.bm25_top ?? 100;
  $("srDenseTop").value = r.dense_top ?? 100;
  $("srRrfk").value = r.rrf_k ?? 60;
  $("srMinCos").value = r.min_cosine ?? 0.32;
  $("sadvMirror").checked = a.hf_mirror !== false;
  $("sDataDir").textContent = S.state.data_dir || "";
  renderChips();
  refreshModelStatus();
}

function syncProviderBoxes() {
  const p = qr("input[name=llmProvider]:checked")?.value || "mock";
  $("sllmApiBox").hidden = p !== "openai_compat";
  $("ggufBox").hidden = p !== "gguf";
}

function refreshGgufInfo() {
  api("/gguf/info").then((d) => {
    S.gguf = d;
    renderGgufPanel();
  }).catch(() => { });
}

function renderGgufPanel() {
  const g = S.gguf;
  if (!g) return;
  // 模型下拉
  const sel = $("sggufModel");
  const cur = (S.state?.config?.gguf?.model) || "";
  const opts = (g.model ? [g.model] : []).concat(g.models || []);
  const seen = new Set();
  sel.innerHTML = opts.map((m) => {
    const key = m.path;
    if (seen.has(key)) return "";
    seen.add(key);
    return `<option value="${esc(m.path)}">${esc(m.name)} (${m.size_mb}MB)${m.path === cur ? " ✓当前" : ""}</option>`;
  }).join("") || '<option value="">— 没有找到模型,请先下载 —</option>';
  sel.value = cur || "";
  // 引擎状态行
  const box = $("ggufEngineStatus");
  const srv = g.server, run = g.running_detail;
  let html = "";
  if (srv) {
    html = `<span class="ms-dot" style="background:var(--g)"></span>
      <b>引擎就绪: llama-server</b><span class="hint">${esc(srv.path)} · ${esc(srv.version || "")}</span>`;
  } else {
    html = `<span class="ms-dot" style="background:var(--amber)"></span>
      <b style="color:#b8860b">引擎未安装(免费,免编译)</b>
      <span class="hint">到「环境检查」页 GGUF 引擎条目下载 llama.cpp 官方 Windows 版:
        有 NVIDIA 显卡选 CUDA 版,否则 CPU 版;解压后把 llama-server.exe 放进 models/llama</span>`;
  }
  if (run) {
    html += `<br><span style="margin-top:6px">▶ 运行中: ${esc(run.model)} · GPU 卸载 ${run.layers} 层 · 上下文 ${run.ctx} · 端口 ${run.port}</span>`;
  }
  box.innerHTML = html;
  // GPU 行
  const gu = g.gpu || {};
  const hasN = (gu.nvidia || []).length > 0;
  $("sggufGpuLbl").innerHTML = "GPU 检测<br><span class='hint'>显卡</span>";
  $("sggufGpuLbl").firstChild.textContent = "GPU 检测";
  $("sggufGpuLbl").querySelector(".hint").textContent = gu.summary || "";
  const layersInput = $("sggufLayers");
  const note = $("sggufLayersNote");
  if (hasN) {
    const names = (gu.nvidia || []).map((x) => x.name).join("、");
    layersInput.disabled = false;
    note.textContent = `本机: ${names}${gu.total_vram_gb ? " · 显存约 " + gu.total_vram_gb + "GB" : ""}` +
      " —— 卸载层数 = 把多少层权重放进显存: 0=纯CPU(最慢); 小模型设满=全部进显存; 大模型边调边看显存占用";
  } else {
    layersInput.disabled = true;
    layersInput.value = 0;
    $("sggufLayersV").textContent = "0";
    note.textContent = gu.summary || "未检测到 NVIDIA GPU —— 只能 CPU 推理(卸载层数固定为 0)";
  }
}

function setRadio(name, val) {
  document.querySelectorAll(`input[name="${name}"]`).forEach((el) => { el.checked = el.value === val; });
}
function setRange(id, v, vid, fmt) { $(id).value = v; $(vid).textContent = fmt ? fmt(v) : v; }

function collectConfig() {
  return {
    llm: {
      provider: qr("input[name=llmProvider]:checked")?.value || "mock",
      base_url: $("sllmBase").value.trim(),
      api_key: $("sllmKey").value.trim(),          // 空 / 占位符 → 服务端保留旧值
      model: $("sllmModel").value.trim(),
      temperature: parseFloat($("sllmTemp").value),
      max_tokens: parseInt($("sllmMaxTk").value) || 1200,
      history_turns: parseInt($("sllmHist").value) || 0,
      custom_system: $("sllmCustom").value,
    },
    embedding: {
      mode: qr("input[name=embMode]:checked")?.value || "local",
      model: $("sembModel").value.trim(),
      api_base: $("sembApiBase").value.trim(),
      api_key: $("sembApiKey").value.trim(),
      api_model: $("sembApiModel").value.trim(),
    },
    gguf: {
      model: $("sggufModel").value || "",
      dir: $("sggufDir").value.trim(),
      layers: parseInt($("sggufLayers").value) || 0,
      n_ctx: parseInt($("sggufCtx").value) || 4096,
    },
    retrieval: {
      chunk_size: parseInt($("srChunkSize").value) || 400,
      chunk_overlap: parseInt($("srOverlap").value) || 80,
      top_k: parseInt($("srTopk").value) || 6,
      bm25_top: parseInt($("srBm25Top").value) || 100,
      dense_top: parseInt($("srDenseTop").value) || 100,
      rrf_k: parseInt($("srRrfk").value) || 60,
      min_cosine: parseFloat($("srMinCos").value) || 0.3,
    },
    advanced: { hf_mirror: $("sadvMirror").checked },
  };
}

function bindSettingsEvents() {
  let saveTimer = null;
  const flash = () => {
    const f = $("saveFloat");
    f.hidden = false;
    clearTimeout(flash._t);
    flash._t = setTimeout(() => { f.hidden = true; }, 1500);
  };
  const save = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const d = await api("/config", { method: "POST", json: collectConfig() });
        if (S.state) { S.state.config = d.config; }
        const st = await api("/state");
        S.state = st;
        flash();
        if (st.index_stale && S.view !== "kb" && S.view !== "settings") {
          setTimeout(() => toast("分块参数已修改: 请到「知识库」页点「重建全部索引」", "err", 7000), 500);
        }
        renderChips();
      } catch (err) { toast("保存失败: " + err.message, "err"); }
    }, 400);
  };
  const evt = (id) => {
    const el = $(id);
    if (el) el.addEventListener(el.type === "range" ? "input" : "change", save);
  };
  ["sllmBase", "sllmModel", "sllmMaxTk", "sllmHist", "sllmCustom",
   "sembModel", "sembApiBase", "sembApiKey", "sembApiModel",
   "srBm25Top", "srDenseTop", "srRrfk", "srMinCos"].forEach(evt);
  ["sllmBase", "sllmModel", "sllmMaxTk", "sllmHist", "sllmCustom",
   "sembModel", "sembApiBase", "sembApiKey", "sembApiModel",
   "srBm25Top", "srDenseTop", "srRrfk", "srMinCos",
   "sggufDir", "sggufCtx"].forEach(evt);
  ["sllmBase", "sllmModel", "sllmMaxTk", "sllmHist", "sllmCustom",
   "sembModel", "sembApiBase", "sembApiKey", "sembApiModel",
   "sggufDir", "sggufCtx"].forEach((id) => {
    $(id).addEventListener("input", save);
  });
  $("sllmKey").addEventListener("input", save);
  $("sembApiKey").addEventListener("input", save);
  $("sadvMirror").addEventListener("change", save);
  $("sggufModel").addEventListener("change", save);
  $("sggufLayers").addEventListener("change", save);
  document.querySelectorAll("input[name=llmProvider]").forEach((el) =>
    el.addEventListener("change", () => {
      syncProviderBoxes();
      if (qr("input[name=llmProvider]:checked")?.value === "gguf") refreshGgufInfo();
      save();
    }));
  $("btnGgufScan").onclick = () => { refreshGgufInfo(); toast("已刷新模型列表"); };
  $("btnGgufOpenDir").onclick = () => api("/gguf/open-dir", { method: "POST" })
    .catch((e) => toast(e.message, "err"));
  $("btnGgufHow").onclick = () => location.hash = "#env";
  $("btnGgufStart").onclick = startGguf;
  $("btnGgufStop").onclick = async () => {
    try {
      const d = await api("/gguf/stop", { method: "POST" });
      toast(d.stopped ? "已停止本地模型,显存/内存已释放" : "模型本来就没有在运行");
      refreshGgufInfo();
      reloadState();
    } catch (e) { toast(e.message, "err"); }
  };
  document.querySelectorAll("input[name=embMode]").forEach((el) =>
    el.addEventListener("change", () => {
      const apiM = qr("input[name=embMode]:checked")?.value === "api";
      $("embLocalBox").hidden = !apiM;
      $("embApiBox").hidden = apiM;
      save();
    }));
  const showVal = (rid, vid, fmt) => $(rid).addEventListener("input", () => {
    $(vid).textContent = fmt ? fmt($(rid).value) : $(rid).value;
  });
  showVal("sllmTemp", "sllmTempV", (v) => Number(v).toFixed(2));
  showVal("srChunkSize", "srChunkSizeV");
  showVal("srOverlap", "srOverlapV");
  showVal("srTopk", "srTopkV");

  // 大模型测试
  $("btnLlmTest").onclick = async () => {
    const box = $("llmTestResult");
    box.textContent = "连接中…"; box.className = "test-result";
    try {
      const full = collectConfig();
      await api("/config", { method: "POST", json: full }).catch(() => { });
      const d = await api("/llm/test", { method: "POST", json: { llm: full.llm } });
      box.textContent = "✓ " + (d.text || "连接成功"); box.className = "test-result ok";
    } catch (e) { box.textContent = "✗ " + e.message; box.className = "test-result err"; }
  };
  // 嵌入测试
  const embTest = async (boxId) => {
    const box = $(boxId);
    box.innerHTML = '<span class="hint">正在转换示例句子…(首次加载本地模型需 10~60 秒)</span>';
    try {
      await api("/config", { method: "POST", json: { embedding: collectConfig().embedding } });
      const d = await api("/embed/test", { method: "POST", json: {} });
      if (d.ok) box.innerHTML = `<span style="color:var(--g-d);font-weight:700">✓ 嵌入正常</span>
        <span class="hint">维度 ${d.dim} · ${d.ms}ms · 向量前3位 [${(d.preview || []).join(", ")}]</span>`;
    } catch (e) { box.innerHTML = `<span style="color:var(--red)">✗ ${esc(e.message)}</span>`; }
  };
  $("btnModelTest").onclick = () => embTest("embTestResult");
  $("btnEmbApiTest").onclick = () => embTest("embTestResult");
  $("btnModelDownload").onclick = () => {
    const repo = $("sembModel").value.trim() || "BAAI/bge-small-zh-v1.5";
    api("/model/download", { method: "POST", json: { repo } }).then((d) => {
      appendTask(d.task, "modelTaskArea");
      toast(`开始下载嵌入模型 ${repo}(约 95MB,自动走国内镜像)`);
    }).catch((e) => toast(e.message, "err"));
  };
  $("btnModelDelete").onclick = () => confirmBox("删除本地模型",
    "将删除已下载的本地嵌入模型文件(约 95MB)。之后需要向量化文档必须先重新下载。确定删除?",
    async () => {
      try {
        await api("/model/delete", {
          method: "POST",
          json: { repo: $("sembModel").value.trim() || "BAAI/bge-small-zh-v1.5" },
        });
        toast("已删除本地模型");
        refreshModelStatus();
        refreshEnvSilent(true);
      } catch (e) { toast(e.message, "err"); }
    }, "删除");
}

async function refreshModelStatus() {
  const box = $("embModelStatus");
  if (!box) return;
  try {
    const d = await api("/model/status");
    const ready = d.ready;
    const mods = (d.models || []).map((m) =>
      `${m.repo} · ${(m.size / 1024 / 1024).toFixed(0)}MB`).join(", ") || "无";
    box.innerHTML = `<span class="ms-dot" style="background:${ready ? "var(--g)" : "var(--amber)"}"></span>
      <b style="color:${ready ? "var(--g-d)" : "#b8860b"}">${ready ? "本地嵌入模型已就绪" : "本地嵌入模型未下载"}</b>
      <span class="hint">${esc(d.repo)} · 已安装: ${esc(mods)}</span>`;
  } catch (e) { /* 忽略 */ }
}

async function startGguf() {
  try {
    const d = await api("/gguf/start", { method: "POST" });
    appendTask(d.task, "ggufTaskArea");
    toast("正在启动本地 GGUF 引擎(首次加载模型需要等待),请留意任务进度");
  } catch (e) { toast(e.message, "err"); }
}

/* 供 HTML 内联 onclick 使用的全局函数 */
window.viewDocChunks = viewDocChunks;
window.openOrigin = openOrigin;
window.delDoc = delDoc;
window.filterChunks = filterChunks;
window.refreshEnv = refreshEnv;
window.startModelDownload = () => { };
window.gotoTut = (anchor) => {
  location.hash = "#tutorial";
  setTimeout(() => {
    try { $("tutFrame").contentWindow.postMessage({ anchor }, "*"); } catch (e) { }
  }, 900);
};

/* ============================================================
   GPU 灵动岛: 顶栏半透明实时状态(2.5s 本地轮询)
   ============================================================ */
let hwTimer = null;

function shortGpuName(n) {
  return String(n || "")
    .replace(/^NVIDIA\s*/i, "").replace(/^GeForce\s*/i, "")
    .replace(/ Laptop GPU$/i, "").replace(/ GPU$/i, "").trim();
}

async function fetchHw() {
  try {
    const d = await api("/hardware/status");
    S.hw = d;
    renderIsland();
    return d;
  } catch (e) { return null; }
}

function startHwPoll() {
  clearInterval(hwTimer);
  hwTimer = setInterval(() => {
    if (document.hidden) return;          // 窗口不可见时不打扰
    fetchHw();
  }, 2500);
  setTimeout(fetchHw, 1200);              // 首帧尽快
}

function renderIsland() {
  const d = S.hw;
  const pill = $("giPill"), main = $("giMain"), dot = $("giDot");
  if (!d || !pill) return;
  const gpus = d.gpus || [];
  const ll = d.llama || {};
  const running = !!(ll.running && ll.detail);
  const g0 = gpus[0];
  pill.classList.remove("busy");
  if (gpus.length && g0) {
    const u = g0.util_pct != null ? g0.util_pct : 0;
    const t = g0.temp_c;
    const short = shortGpuName(g0.name);
    let txt = short;
    if (g0.vram_used_gb != null && g0.vram_total_gb) {
      txt += ` · ${g0.vram_used_gb}/${g0.vram_total_gb}GB`;
    }
    if (t != null) txt += ` · ${t}°C`;
    main.textContent = txt;
    pill.title = g0.name + " 使用率 " + fmtGpuPct(u) + " · 显存 "
      + (g0.vram_used_gb ?? "-") + "/" + (g0.vram_total_gb ?? "-") + "GB"
      + (t != null ? " · " + t + "°C" : "")
      + (running ? " 本地 GGUF 引擎运行中(" + ll.detail.model + ", 卸载 " + ll.detail.layers + " 层)" : "")
      + " 点击展开详情";
    dot.className = "gi-dot" + (t != null && t >= 78 ? " hot" : (u >= 90 ? " warn" : " ok"));
    if (running || u >= 5) pill.classList.add("busy");
  } else if (d.has_nvidia === false && d.summary) {
    // 有显卡信息但没有实时遥测(非 NVIDIA)
    const m = d.summary.match(/检测到显卡[：:]([^。]*)/) || d.summary.match(/检测到 NVIDIA GPU: ([^。]*)/);
    main.textContent = (m ? shortGpuName(m[1]) : "GPU") + " · CPU 推理";
    dot.className = "gi-dot warn";
    pill.title = d.summary;
  } else {
    main.textContent = "CPU 模式 · 未检测到独立 GPU";
    dot.className = "gi-dot warn";
    pill.title = d.summary || "未检测到可用 GPU";
  }
  renderIslandPanel();
}

function fmtGpuPct(v) { return v == null ? "—" : v + "%"; }

function renderIslandPanel() {
  const d = S.hw;
  const body = $("giBody");
  if (!d || !body) return;
  const gpus = d.gpus || [];
  const ll = d.llama || {};
  const running = !!(ll.running && ll.detail);
  $("giSub").textContent = d.source === "nvidia-smi" ? "nvidia-smi · 实时" : "静态检测";

  let rows = "";
  if (gpus.length) {
    rows = gpus.map((g) => {
      const u = g.util_pct != null ? g.util_pct : 0;
      const memPct = (g.vram_used_gb != null && g.vram_total_gb)
        ? Math.min(100, Math.round(g.vram_used_gb / g.vram_total_gb * 100)) : null;
      const hot = g.temp_c != null && g.temp_c >= 78;
      const bar = (pct) => `<div class="gi-bar"><i class="${hot ? "hot" : ""}" style="width:${Math.max(2, pct)}%"></i></div>`;
      return `
      <div class="gi-row"><span class="k">显卡</span><span class="v" title="${esc(g.name)}">${esc(g.name)}</span></div>
      <div class="gi-row"><span class="k">使用率</span>${bar(u)}<span class="v" style="flex:0 0 46px;text-align:right">${fmtGpuPct(g.util_pct)}</span></div>
      ${g.vram_used_gb != null ? `<div class="gi-row"><span class="k">显存</span>${bar(memPct || 0)}<span class="v" style="flex:0 0 88px;text-align:right">${g.vram_used_gb} / ${g.vram_total_gb} GB</span></div>` : ""}
      ${g.temp_c != null ? `<div class="gi-row"><span class="k">温度</span><span class="v" style="${hot ? "color:var(--red)" : ""}">${g.temp_c}°C${hot ? " (偏高)" : ""}</span></div>` : ""}
      ${g.driver ? `<div class="gi-row"><span class="k">驱动</span><span class="v">${esc(g.driver)}</span></div>` : ""}`;
    }).join("");
  } else {
    rows = `<div class="gi-empty">${esc(d.summary || "未检测到可用 GPU")}<br>
      <span class="hint">本地 GGUF 推理将自动使用 CPU;若安装了 NVIDIA 驱动后仍不显示,请重启应用。</span></div>`;
  }
  const llRow = `<div class="gi-llm">
    ${running
      ? `<span class="dot ok" style="width:7px;height:7px;border-radius:50%;background:var(--g);display:inline-block"></span>
         <b style="color:var(--g-d)">本地 GGUF 引擎运行中</b>
         <span class="hint">${esc(ll.detail.model)} · 卸载 ${ll.detail.layers} 层 · 端口 ${ll.detail.port}</span>`
      : `<span style="width:7px;height:7px;border-radius:50%;background:#c9d0c0;display:inline-block"></span>
         <span>本地 GGUF 引擎未运行</span>
         <span class="hint">提问时会自动启动,或在设置页手动启动</span>`}
  </div>`;
  body.innerHTML = rows + llRow;
}

function bindIslandEvents() {
  $("giPill").onclick = () => {
    const p = $("giPanel");
    p.hidden = !p.hidden;
    if (!p.hidden) fetchHw();
  };
  $("giRefresh").onclick = (e) => { e.stopPropagation(); fetchHw(); };
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".gpu-island")) $("giPanel").hidden = true;
  });
  // 引擎启停后立刻刷新岛屿
  const orig = S.refreshAfterGguf;
  S.refreshAfterGguf = () => { fetchHw(); };
}

/* 教程 iframe → 主界面跳转 */
window.addEventListener("message", (ev) => {
  if (ev.data && ev.data.grNavigate) location.hash = "#" + ev.data.grNavigate;
});
