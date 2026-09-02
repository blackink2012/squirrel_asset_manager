/* ═══════════════════════════════════════════════════════════
   Squirrel 资产浏览器 — 前端逻辑（原生 JS，无依赖）
   ═══════════════════════════════════════════════════════════ */
"use strict";

/* ── 常量 ── */
const LIB_ICONS = {
  materials: "🎨", models: "🧊", lights: "💡", textures: "🧵",
  scenes: "🏞️", hdr: "🌐", ani: "🎬",
};
const PAGE_SIZE = 120;
const PLACEHOLDER_BY_LIB = { materials: "🎨", models: "🧊", lights: "💡", textures: "🧵", scenes: "🏞️", hdr: "🌐", ani: "🎬" };
const CAT_DISPLAY = {};   // "lib||cat" → 中文名（由状态接口填充）
let SUB_LIB_NAMES = {};   // lib id → 中文名

/* ── 状态 ── */
const state = {
  lib: "",          // 子库 id（"" = 全部）
  cat: "",          // 分类 id（"" = 不限）
  tags: [],         // 已选标签
  q: "",            // 搜索词
  sort: "recent",
  page: 1,
  total: 0,
  items: [],
  cols: localStorage.getItem("sq-cols") || "4",
};

let snapshot = null;          // /api/state 结果
let gridAssets = [];          // 当前已渲染资产
let detailPreviews = [];      // 当前详情资产的预览图列表
let detailPreviewIndex = 0;   // 当前显示的第几张预览图

/* ── 工具 ── */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function toast(msg, ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

/* ══════════ 初始化 ══════════ */

async function boot() {
  restoreState();
  bindEvents();
  await refreshState();
}

function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem("sq-view-state") || "{}");
    if (saved.lib !== undefined) state.lib = saved.lib || "";
    if (saved.cat !== undefined) state.cat = saved.cat || "";
    if (Array.isArray(saved.tags)) state.tags = saved.tags;
    if (saved.sort) state.sort = saved.sort;
    if (saved.q) { state.q = saved.q; $("#searchInput").value = saved.q; }
  } catch (e) { /* ignore */ }
  $("#sortSelect").value = state.sort;
  $("#grid").dataset.cols = state.cols;
  $$(".dens-btn").forEach((b) => b.classList.toggle("active", b.dataset.cols === state.cols));
}

function saveState() {
  localStorage.setItem("sq-view-state", JSON.stringify({
    lib: state.lib, cat: state.cat, tags: state.tags, sort: state.sort, q: state.q,
  }));
  localStorage.setItem("sq-cols", state.cols);
}

async function refreshState() {
  try {
    snapshot = await api("/api/state");
  } catch (e) {
    toast("无法连接服务器");
    return;
  }

  SUB_LIB_NAMES = snapshot.all_sub_libraries || snapshot.config_sub_libraries || {};
  updateLibBadge();

  if (!snapshot.ready) {
    $("#setupOverlay").hidden = false;
    return;
  }
  $("#setupOverlay").hidden = true;

  renderSidebar();
  renderTagCloud();
  loadAssets(true);
}

function updateLibBadge() {
  const p = snapshot?.library_path || "";
  $("#libName").textContent = p ? p.split(/[\\/]/).filter(Boolean).pop() || p : "未设置";
  $("#libBadge").title = p || "点击设置资产库路径";
  const st = $("#settingsStatus");
  if (snapshot?.ready) {
    st.innerHTML = `资产总数 <b>${snapshot.total}</b> · 分类 <b>${snapshot.sub_libraries.length}</b> 个子库 · 扫描耗时 ${snapshot.scan_ms} ms<br><span style="color:var(--text-3)">${esc(snapshot.library_path)}</span>`;
  } else {
    st.textContent = snapshot?.error || "未加载";
  }
}

/* ══════════ 侧栏渲染 ══════════ */

function renderSidebar() {
  const nav = $("#libNav");
  nav.innerHTML = "";

  // 全部资产
  const all = document.createElement("div");
  all.className = "nav-item all" + (!state.lib && !state.cat ? " active" : "");
  all.id = "navAll";
  all.innerHTML = `<span class="nav-icon">🗂️</span><span class="nav-label">全部资产</span><span class="nav-count">${snapshot.total}</span>`;
  all.onclick = () => selectNav("", "");
  nav.appendChild(all);

  for (const lib of snapshot.sub_libraries) {
    for (const cat of lib.categories) {
      CAT_DISPLAY[`${lib.id}||${cat.id}`] = cat.name;
    }
    const group = document.createElement("div");
    group.className = "lib-group";

    const header = document.createElement("div");
    header.className = "lib-group-header";
    const isLibActive = state.lib === lib.id && !state.cat;
    const headItem = document.createElement("div");
    headItem.className = "nav-item" + (isLibActive ? " active" : "");
    headItem.innerHTML = `
      <span class="nav-icon">${LIB_ICONS[lib.id] || "📁"}</span>
      <span class="nav-label">${esc(lib.name)}</span>
      <span class="nav-count">${lib.count}</span>`;
    headItem.onclick = () => selectNav(lib.id, "");
    header.appendChild(headItem);
    group.appendChild(header);

    if (lib.categories.length && (state.lib === lib.id || lib.count <= 14)) {
      const children = document.createElement("div");
      children.className = "lib-children";
      for (const cat of lib.categories) {
        const ci = document.createElement("div");
        ci.className = "nav-item cat-item" + (state.lib === lib.id && state.cat === cat.id ? " active" : "");
        ci.innerHTML = `<span class="nav-icon"></span><span class="nav-label">${esc(cat.name)}</span><span class="nav-count">${cat.count}</span>`;
        ci.onclick = () => selectNav(lib.id, cat.id);
        children.appendChild(ci);
      }
      group.appendChild(children);
    }
    nav.appendChild(group);
  }
}

function selectNav(lib, cat) {
  state.lib = lib;
  state.cat = cat;
  saveState();
  renderSidebar();
  renderTagCloud();
  loadAssets(true);
}

/* ══════════ 标签云 ══════════ */

function renderTagCloud() {
  const box = $("#tagCloud");
  box.innerHTML = "";
  if (!snapshot?.ready) return;

  let tags = [];
  if (state.lib) tags = snapshot.tag_cloud[state.lib] || [];
  else {
    // 全部库：合并计数
    const merged = {};
    for (const libs of Object.values(snapshot.tag_cloud)) {
      for (const t of libs) merged[t.tag] = (merged[t.tag] || 0) + t.count;
    }
    tags = Object.entries(merged).map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count);
  }
  tags = tags.slice(0, 36);

  if (!tags.length) {
    box.innerHTML = `<span class="tag-hint">当前范围内暂无标签</span>`;
    return;
  }
  for (const t of tags) {
    const chip = document.createElement("span");
    chip.className = "tag-chip" + (state.tags.includes(t.tag) ? " active" : "");
    chip.innerHTML = `${esc(t.tag)}<span class="tc-n">${t.count}</span>`;
    chip.onclick = () => toggleTag(t.tag);
    box.appendChild(chip);
  }
}

function toggleTag(tag) {
  const i = state.tags.indexOf(tag);
  if (i >= 0) state.tags.splice(i, 1);
  else state.tags.push(tag);
  saveState();
  renderTagCloud();
  loadAssets(true);
}

/* ══════════ 资产列表 ══════════ */

async function loadAssets(reset) {
  if (reset) {
    state.page = 1;
    gridAssets = [];
    $("#grid").innerHTML = "";   // 清空旧卡片，避免筛选/搜索后新旧卡片混排
  }
  const params = new URLSearchParams({
    lib: state.lib, category: state.cat, q: state.q,
    tags: state.tags.join(","), sort: state.sort,
    page: state.page, page_size: PAGE_SIZE,
  });
  let data;
  try {
    data = await api("/api/assets?" + params);
  } catch (e) {
    toast("加载资产列表失败");
    return;
  }

  state.total = data.total;
  gridAssets = gridAssets.concat(data.items);
  renderGrid();
  renderMeta();
  renderChips();
  renderBreadcrumb();
}

function renderMeta() {
  const el = $("#resultMeta");
  el.textContent = state.total
    ? `共 ${state.total} 个资产${state.total > gridAssets.length ? ` · 已显示 ${gridAssets.length}` : ""}`
    : "";
  $("#emptyState").hidden = state.total !== 0 || !snapshot?.ready;
  $("#loadMoreBtn").hidden = gridAssets.length >= state.total;
}

function renderChips() {
  const box = $("#activeChips");
  box.innerHTML = "";
  const add = (label, onRemove) => {
    const chip = document.createElement("span");
    chip.className = "a-chip";
    chip.innerHTML = `${esc(label)}<span class="x">✕</span>`;
    chip.onclick = onRemove;
    box.appendChild(chip);
  };
  if (state.q) add(`搜索: ${state.q}`, () => {
    state.q = ""; $("#searchInput").value = ""; saveState(); loadAssets(true);
  });
  state.tags.forEach((t) => add(`标签: ${t}`, () => toggleTag(t)));
}

function renderBreadcrumb() {
  const bc = $("#breadcrumb");
  const parts = [];
  if (!state.lib && !state.cat) {
    bc.innerHTML = `<span class="crumb-item current">全部资产</span>`;
    return;
  }
  let html = `<span class="crumb-item clickable" data-lib="">全部资产</span><span class="crumb-sep">/</span>`;
  if (state.lib) {
    const isActive = !state.cat;
    html += `<span class="crumb-item ${isActive ? "current" : "clickable"}" data-lib="${esc(state.lib)}" data-cat="">${esc(SUB_LIB_NAMES[state.lib] || state.lib)}</span>`;
    if (state.cat) {
      const disp = CAT_DISPLAY[`${state.lib}||${state.cat}`] || state.cat;
      html += `<span class="crumb-sep">/</span><span class="crumb-item current">${esc(disp)}</span>`;
    }
  }
  bc.innerHTML = html;
  bc.querySelectorAll(".clickable").forEach((el) => {
    el.onclick = () => selectNav(el.dataset.lib, el.dataset.cat || "");
  });
}

/* ── 网格渲染 + 懒加载 ── */

const imgObserver = new IntersectionObserver((entries) => {
  for (const en of entries) {
    if (en.isIntersecting) {
      const img = en.target;
      img.src = img.dataset.src;
      img.onload = () => img.classList.add("loaded");
      imgObserver.unobserve(img);
    }
  }
}, { rootMargin: "400px" });

function renderGrid() {
  const grid = $("#grid");
  grid.dataset.cols = state.cols;
  // 只重建新增部分（start 以 DOM 现有卡片数为准，防御数据/渲染不同步）
  const start = Math.min(grid.querySelectorAll(".card").length, gridAssets.length);
  const frag = document.createDocumentFragment();

  for (let i = start; i < gridAssets.length; i++) {
    const a = gridAssets[i];
    frag.appendChild(buildCard(a, i));
  }
  grid.appendChild(frag);
}

function buildCard(a, index) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.index = index;

  const libName = SUB_LIB_NAMES[a.sub_library] || a.sub_library;
  const libColor = (snapshot?.sub_libraries || []).find((l) => l.id === a.sub_library)?.color || "#8a8f98";
  const catName = CAT_DISPLAY[`${a.sub_library}||${a.category}`] || a.category;

  const badges = [];
  if (a.node_type) badges.push(`<span class="badge"><span class="dot" style="background:${libColor}"></span>${esc(a.node_type)}</span>`);
  if (a.resolution) badges.push(`<span class="badge">${esc(a.resolution)}</span>`);

  const tagsHtml = (a.tags || []).slice(0, 4)
    .map((t) => `<span class="mini-tag">${esc(t)}</span>`).join("");

  // 仅 GIF 动图（无 sicon）时请求 aicon；否则用静态 sicon 更省流量
  const thumbUrl = a.has_aicon && !a.has_sicon
    ? `/api/thumb/${encodeURIComponent(a.id)}?animated=1`
    : `/api/thumb/${encodeURIComponent(a.id)}`;

  card.innerHTML = `
    <div class="card-thumb">
      <div class="thumb-placeholder">${PLACEHOLDER_BY_LIB[a.sub_library] || "📦"}</div>
      ${a.has_sicon || a.has_aicon ? `<img alt="" loading="lazy" data-src="${thumbUrl}">` : ""}
      <div class="card-badges">${badges.join("")}</div>
      ${a.has_aicon || a.has_mp4 ? `<span class="card-anim-flag">▶ 动图</span>` : ""}
    </div>
    <div class="card-info">
      <div class="card-title" title="${esc(a.name_cn)}">${esc(a.name_cn || a.name)}</div>
      <div class="card-meta">
        <span>${esc(libName)}</span><span class="sep">·</span><span>${esc(catName)}</span>
        ${a.renderer ? `<span class="sep">·</span><span>${esc(a.renderer)}</span>` : ""}
        ${a.create_date ? `<span class="sep">·</span><span>${esc(a.create_date)}</span>` : ""}
      </div>
      ${tagsHtml ? `<div class="card-tags">${tagsHtml}</div>` : ""}
    </div>`;

  const img = card.querySelector("img");
  if (img) imgObserver.observe(img);

  card.onclick = () => openDetail(a.id);
  return card;
}

/* ══════════ 详情弹窗 ══════════ */

async function openDetail(assetId) {
  let a;
  try {
    a = await api(`/api/asset/${encodeURIComponent(assetId)}`);
  } catch (e) {
    toast("加载详情失败");
    return;
  }
  renderDetail(a);
  $("#detailModal").hidden = false;
  document.body.style.overflow = "hidden";
}

function renderDetail(a) {
  // ── 预览图：大图 + 下方缩略图条（资产有多张预览图时显示）──
  detailPreviews = (a.previews && a.previews.length) ? a.previews : buildPreviewFallback(a);
  detailPreviewIndex = 0;
  const prev = $("#detailPreview");
  const thumbsBox = $("#detailThumbs");
  thumbsBox.innerHTML = "";

  const renderPreview = (i) => {
    detailPreviewIndex = i;
    const p = detailPreviews[i];
    if (!p) {
      prev.innerHTML = `<div class="thumb-placeholder" style="font-size:60px">${PLACEHOLDER_BY_LIB[a.sub_library] || "📦"}</div>`;
      prev.onclick = null;
      return;
    }
    // 多张预览图时：显示左右切换箭头
    const multi = detailPreviews.length > 1;
    prev.innerHTML = (p.type === "video"
        ? `<video src="${p.url}" autoplay loop muted controls></video>`
        : `<img src="${p.url}" alt="">`)
      + (multi ? `<span class="pv-arrow pv-left" title="上一张">‹</span><span class="pv-arrow pv-right" title="下一张">›</span>` : "");

    if (multi) {
      // 箭头：独立切换，不冒泡到背景点击
      prev.querySelector(".pv-left").onclick = (e) => { e.stopPropagation(); switchPreview(-1); };
      prev.querySelector(".pv-right").onclick = (e) => { e.stopPropagation(); switchPreview(1); };
    }
    if (multi && p.type !== "video") {
      // 图片：点击右半区 = 下一张，左半区 = 上一张（视频用箭头切换，避免误触播放控件）
      prev.style.cursor = "pointer";
      prev.onclick = (e) => {
        const rect = prev.getBoundingClientRect();
        const x = e.clientX - rect.left;
        switchPreview(x > rect.width / 2 ? 1 : -1);
      };
    } else {
      prev.style.cursor = "default";
      prev.onclick = null;
    }
    [...thumbsBox.children].forEach((el, idx) => el.classList.toggle("active", idx === i));
  };

  if (detailPreviews.length > 1) {
    thumbsBox.hidden = false;
    detailPreviews.forEach((p, i) => {
      const item = document.createElement("div");
      item.className = "dt-item" + (i === 0 ? " active" : "");
      item.innerHTML = p.type === "video"
        ? `<video src="${p.url}" muted preload="metadata"></video>`
        : `<img src="${p.url}" alt="" loading="lazy">`;
      item.title = p.name;
      item.onclick = () => renderPreview(i);
      thumbsBox.appendChild(item);
    });
  } else {
    thumbsBox.hidden = true;
  }
  renderPreview(0);

  // 标题 / 子标题
  $("#detailTitle").textContent = a.name_cn || a.name;
  const subParts = [a.name];
  const libName = SUB_LIB_NAMES[a.sub_library] || a.sub_library;
  const catName = a.category_chain.map((c) => CAT_DISPLAY[`${a.sub_library}||${c}`] || c).join(" / ");
  subParts.push(`${libName} · ${catName}`);
  $("#detailSub").innerHTML = subParts.filter(Boolean).map(esc).join("<br>");

  // 标签
  $("#detailTags").innerHTML = (a.tags || [])
    .map((t) => `<span class="mini-tag" data-tag="${esc(t)}">${esc(t)}</span>`).join("");
  $("#detailTags").querySelectorAll(".mini-tag").forEach((el) => {
    el.onclick = () => { toggleTag(el.dataset.tag); closeDetail(); };
  });

  // 信息表
  const rows = [];
  const libColor = (snapshot?.sub_libraries || []).find((l) => l.id === a.sub_library)?.color;
  rows.push(["类型", `<span style="color:${libColor || "var(--text)"}">${esc(libName)}</span>`]);
  rows.push(["分类", esc(catName)]);
  if (a.node_type) rows.push(["节点类型", `<span class="fmt-badge">${esc(a.node_type)}</span>`]);
  if (a.renderer) rows.push(["渲染器", esc(a.renderer)]);
  if (a.software) rows.push(["软件", esc(a.software)]);
  if (a.color_space) rows.push(["色彩空间", esc(a.color_space)]);
  if (a.resolution) rows.push(["分辨率", esc(a.resolution)]);
  if (a.create_date) rows.push(["创建日期", esc(a.create_date)]);
  if (a.file_mtime) rows.push(["修改时间", esc(fmtDate(a.file_mtime))]);
  if (a.formats?.length) rows.push(["格式", a.formats.map((f) => `<span class="fmt-badge">${esc(f)}</span>`).join("")]);
  if (a.texture_count) rows.push(["贴图数量", String(a.texture_count)]);
  if (a.has_variants) rows.push(["变体", "含 LOD / 版本变体"]);
  if (a.notes) rows.push(["备注", esc(a.notes)]);
  $("#detailInfo").innerHTML = rows
    .map(([k, v]) => `<div class="info-k">${k}</div><div class="info-v">${v}</div>`).join("");

  // 贴图清单
  const texSec = $("#texSection");
  if (a.textures?.length) {
    texSec.hidden = false;
    $("#texCount").textContent = `(${a.textures.length})`;
    $("#texList").innerHTML = "";
    for (const t of a.textures) {
      const item = document.createElement("div");
      item.className = "tex-item";
      const ext = (t.name.split(".").pop() || "").toLowerCase();
      const imgExts = ["png", "jpg", "jpeg", "gif", "webp", "bmp"];
      const isImg = imgExts.includes(ext);
      item.innerHTML = `
        <div class="tex-thumb" style="${isImg ? `background-image:url('/api/file/${encodeURIComponent(a.id)}?rel=${encodeURIComponent(t.rel)}')` : ""}">${isImg ? "" : "📄"}</div>
        <span class="tex-name" title="${esc(t.rel)}">${esc(t.name)}</span>
        <span class="tex-size">${fmtSize(t.size)}</span>`;
      if (isImg) {
        item.onclick = () => {
          window.open(`/api/file/${encodeURIComponent(a.id)}?rel=${encodeURIComponent(t.rel)}`, "_blank");
        };
      }
      $("#texList").appendChild(item);
    }
  } else {
    texSec.hidden = true;
  }

  // 包含文件
  const filesSec = $("#filesSection");
  const showEntries = (a.entries || []).filter((e) => !e.startsWith("thumb.") && e !== "meta.json");
  if (showEntries.length) {
    filesSec.hidden = false;
    $("#entryChips").innerHTML = showEntries.map((e) => `<span class="entry-chip">${esc(e)}</span>`).join("");
  } else {
    filesSec.hidden = true;
  }

  // 路径
  $("#detailPath").innerHTML = `
    <span class="path">${esc(a.zasset_path || "")}</span>
    <button class="copy-btn" id="copyPathBtn">复制路径</button>`;
  $("#copyPathBtn").onclick = () => {
    navigator.clipboard.writeText(a.zasset_path || "").then(
      () => toast("路径已复制"), () => toast("复制失败"));
  };
}

function switchPreview(delta) {
  // 键盘左右箭头：在多张预览图间切换
  if (detailPreviews.length < 2) return;
  const box = $("#detailThumbs");
  const n = box.children.length;
  const next = ((detailPreviewIndex + delta) % n + n) % n;
  const item = box.children[next];
  if (item) item.click();
}

function buildPreviewFallback(a) {
  // 后端 /api/asset 未返回 previews 时（旧服务），由前端数据兜底构造
  // 预览图来源 = thumb* 系列（thumb.sicon / thumb_2.sicon / thumb.aicon / thumb.mp4）
  const id = encodeURIComponent(a.id);
  const list = [];
  for (const tf of a.thumb_files || []) {
    const fname = encodeURIComponent(tf.name);
    if (tf.kind === "mp4") {
      list.push({ type: "video", name: tf.name, url: `/api/media/${id}?file=${fname}` });
    } else if (tf.kind === "aicon") {
      list.push({ type: "gif", name: tf.name, url: `/api/media/${id}?file=${fname}` });
    } else {
      list.push({ type: "image", name: tf.name, url: `/api/thumb/${id}?file=${fname}` });
    }
  }
  return list;
}

function closeDetail() {
  $("#detailModal").hidden = true;
  document.body.style.overflow = "";
}

/* ══════════ 设置 / 文件夹选择 ══════════ */

function openSettings() {
  $("#libPathInput").value = snapshot?.library_path || "";
  updateLibBadge();
  $("#settingsModal").hidden = false;
}
function closeSettings() { $("#settingsModal").hidden = true; }

async function saveLibrary() {
  const path = $("#libPathInput").value.trim();
  if (!path) { toast("请输入路径"); return; }
  const btn = $("#saveLibBtn");
  btn.disabled = true;
  btn.textContent = "扫描中…";
  try {
    const res = await api("/api/library", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      toast(res.error || "路径无效");
    } else {
      toast(`已加载 ${res.total} 个资产`);
      closeSettings();
      state.page = 1; gridAssets = [];
      await refreshState();
    }
  } catch (e) {
    toast("保存失败");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并重新扫描";
  }
}

/* ── 文件夹浏览器 ── */
let fsSelected = "";

async function openFsBrowser() {
  $("#fsModal").hidden = false;
  fsSelected = "";
  await browseTo($("#fsPathInput").value || snapshot?.library_path || "C:\\");
}

async function browseTo(path) {
  const list = $("#fsList");
  list.innerHTML = `<div class="fs-empty">加载中…</div>`;
  try {
    const data = await api("/api/fs?path=" + encodeURIComponent(path));
    $("#fsPathInput").value = data.path;
    list.innerHTML = "";
    if (data.error) {
      list.innerHTML = `<div class="fs-empty">${esc(data.error)}</div>`;
      return;
    }
    if (!data.dirs.length) {
      list.innerHTML = `<div class="fs-empty">（没有子文件夹）</div>`;
    }
    for (const d of data.dirs) {
      const item = document.createElement("div");
      item.className = "fs-item";
      item.innerHTML = `<span class="fi">📁</span><span>${esc(d.name)}</span>`;
      item.onclick = () => { browseTo(d.path); };
      item.ondblclick = () => { fsSelected = d.path; chooseFsFolder(); };
      list.appendChild(item);
    }
  } catch (e) {
    list.innerHTML = `<div class="fs-empty">无法读取该路径</div>`;
  }
}

function chooseFsFolder() {
  const p = fsSelected || $("#fsPathInput").value.trim();
  if (!p) { toast("请先选择文件夹"); return; }
  $("#libPathInput").value = p;
  $("#fsModal").hidden = true;
}

/* ══════════ 事件绑定 ══════════ */

function bindEvents() {
  // 搜索（防抖）
  let searchTimer;
  $("#searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = e.target.value.trim();
      saveState();
      loadAssets(true);
    }, 260);
  });
  $("#searchClear").onclick = () => {
    $("#searchInput").value = "";
    state.q = "";
    saveState();
    loadAssets(true);
  };

  // 排序
  $("#sortSelect").onchange = (e) => {
    state.sort = e.target.value;
    saveState();
    loadAssets(true);
  };

  // 密度
  $$(".dens-btn").forEach((b) => {
    b.onclick = () => {
      state.cols = b.dataset.cols;
      $$(".dens-btn").forEach((x) => x.classList.toggle("active", x === b));
      saveState();
      $("#grid").dataset.cols = state.cols;
    };
  });

  // 刷新
  $("#refreshBtn").onclick = async (e) => {
    const btn = e.currentTarget;
    btn.classList.add("spin");
    try { await api("/api/refresh"); } catch (err) { /* ignore */ }
    await refreshState();
    btn.classList.remove("spin");
    toast("资产库已刷新");
  };

  // 设置
  $("#settingsBtn").onclick = openSettings;
  $("#libBadge").onclick = openSettings;
  $("#saveLibBtn").onclick = saveLibrary;
  $("#browseBtn").onclick = openFsBrowser;
  $("#fsGo").onclick = () => browseTo($("#fsPathInput").value.trim());
  $("#fsUp").onclick = () => {
    const cur = $("#fsPathInput").value.replace(/[\\/]+$/, "");
    const parent = cur.replace(/[\\/][^\\/]+$/, "");
    browseTo(parent && parent !== cur ? parent : cur);
  };
  $("#fsChooseBtn").onclick = chooseFsFolder;
  $("#fsPathInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") browseTo(e.target.value.trim());
  });

  // 引导页
  $("#setupChooseBtn").onclick = openSettings;

  // 弹窗关闭
  $("#detailClose").onclick = closeDetail;
  $$("[data-close]").forEach((b) => {
    b.onclick = () => { $("#" + b.dataset.close).hidden = true; };
  });
  $$(".modal-backdrop").forEach((bd) => {
    bd.addEventListener("mousedown", (e) => {
      if (e.target === bd) {
        bd.hidden = true;
        if (bd.id === "detailModal") document.body.style.overflow = "";
      }
    });
  });

  // 加载更多
  $("#loadMoreBtn").onclick = () => {
    state.page += 1;
    loadAssets(false);
  };

  // 键盘
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#detailModal").hidden) closeDetail();
      else {
        $("#settingsModal").hidden = true;
        $("#fsModal").hidden = true;
      }
      return;
    }
    if (e.key === "/" && document.activeElement.tagName !== "INPUT"
        && document.activeElement.tagName !== "SELECT") {
      e.preventDefault();
      $("#searchInput").focus();
    }
    // 详情弹窗内左右箭头切换预览图
    if (!$("#detailModal").hidden) {
      if (e.key === "ArrowLeft") switchPreview(-1);
      if (e.key === "ArrowRight") switchPreview(1);
    }
  });
}

/* ══════════ 启动 ══════════ */
boot();
