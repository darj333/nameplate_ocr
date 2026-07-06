/* ── Nameplate OCR Export — frontend ─────────────────────────────────────── */

const API = "";          // same origin
let currentPage = 1;
const PAGE_SIZE  = 20;
let pollingTimers = {};  // nameplate_id -> setInterval handle
let openNameplateId = null;

// ── Toast ──────────────────────────────────────────────────────────────────
const toastContainer = document.createElement("div");
toastContainer.id = "toast-container";
document.body.appendChild(toastContainer);

function toast(msg, type = "info", duration = 3500) {
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  toastContainer.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── API helpers ────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const res = await fetch(API + path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res;
}

async function apiJSON(path, options = {}) {
  const res = await apiFetch(path, options);
  return res.json();
}

// ── Upload ─────────────────────────────────────────────────────────────────
const dropZone   = document.getElementById("drop-zone");
const fileInput  = document.getElementById("file-input");
const progressEl = document.getElementById("upload-progress");
const fillEl     = document.getElementById("progress-fill");
const statusText = document.getElementById("upload-status-text");

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => fileInput.files[0] && uploadFile(fileInput.files[0]));

dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

async function uploadFile(file) {
  progressEl.classList.remove("hidden");
  fillEl.style.width = "30%";
  statusText.textContent = "Uploading…";

  const fd = new FormData();
  fd.append("file", file);

  try {
    const data = await apiJSON("/nameplates/upload", { method: "POST", body: fd });
    fillEl.style.width = "100%";
    statusText.textContent = `Accepted (ID ${data.id}). Extracting…`;
    toast(`Upload OK — ID ${data.id}. OCR running…`, "success");
    fileInput.value = "";
    setTimeout(() => { progressEl.classList.add("hidden"); fillEl.style.width = "0%"; }, 2000);
    startPolling(data.id);
    await loadTable();
  } catch (err) {
    statusText.textContent = `Error: ${err.message}`;
    toast(`Upload failed: ${err.message}`, "error");
    fillEl.style.width = "0%";
  }
}

// ── Polling ────────────────────────────────────────────────────────────────
function startPolling(id) {
  if (pollingTimers[id]) return;
  pollingTimers[id] = setInterval(async () => {
    try {
      const data = await apiJSON(`/nameplates/${id}`);
      if (data.status !== "pending") {
        clearInterval(pollingTimers[id]);
        delete pollingTimers[id];
        if (data.status === "processed") toast(`Nameplate #${id} extracted successfully.`, "success");
        else toast(`Nameplate #${id} extraction failed: ${data.error_message}`, "error", 6000);
        await loadTable();
        if (openNameplateId === id) openDetail(id);
      }
    } catch (_) {}
  }, 3000);
}

// ── Table ──────────────────────────────────────────────────────────────────
async function loadTable(page = currentPage) {
  currentPage = page;
  try {
    const data = await apiJSON(`/nameplates?page=${page}&page_size=${PAGE_SIZE}`);
    renderTable(data);
  } catch (err) {
    toast(`Failed to load records: ${err.message}`, "error");
  }
}

function renderTable({ items, total, page, page_size }) {
  const tbody = document.getElementById("nameplates-tbody");
  const empty = document.getElementById("empty-state");

  tbody.innerHTML = "";
  if (!items.length) {
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
    items.forEach(np => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="checkbox" class="row-check" data-id="${np.id}" /></td>
        <td>${np.id}</td>
        <td class="filename-cell" title="${escHtml(np.filename)}">${escHtml(truncate(np.filename, 32))}</td>
        <td>${fmtDate(np.uploaded_at)}</td>
        <td>${statusBadge(np.status)}</td>
        <td>
          <button class="btn btn-sm btn-outline" data-action="open" data-id="${np.id}">View / Edit</button>
        </td>`;
      tbody.appendChild(tr);
    });
  }

  // Selection tracking
  document.querySelectorAll(".row-check").forEach(cb => {
    cb.addEventListener("change", updateSelectionBar);
  });
  document.getElementById("select-all").addEventListener("change", e => {
    document.querySelectorAll(".row-check").forEach(cb => { cb.checked = e.target.checked; });
    updateSelectionBar();
  });

  // View/edit buttons
  tbody.querySelectorAll("[data-action='open']").forEach(btn => {
    btn.addEventListener("click", () => openDetail(Number(btn.dataset.id)));
  });

  // Pagination
  renderPagination(total, page, page_size);

  // Resume polling for any pending rows
  items.forEach(np => { if (np.status === "pending") startPolling(np.id); });
}

function renderPagination(total, page, page_size) {
  const pages = Math.max(1, Math.ceil(total / page_size));
  const pg = document.getElementById("pagination");
  pg.innerHTML = "";
  for (let p = 1; p <= pages; p++) {
    const btn = document.createElement("button");
    btn.textContent = p;
    if (p === page) btn.classList.add("active");
    btn.addEventListener("click", () => loadTable(p));
    pg.appendChild(btn);
  }
}

function updateSelectionBar() {
  const checked = document.querySelectorAll(".row-check:checked");
  const btn = document.getElementById("delete-selected-btn");
  btn.style.display = checked.length ? "inline-flex" : "none";
}

document.getElementById("delete-selected-btn").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll(".row-check:checked")].map(cb => Number(cb.dataset.id));
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} record(s)? This cannot be undone.`)) return;
  for (const id of ids) {
    try { await apiFetch(`/nameplates/${id}`, { method: "DELETE" }); }
    catch (err) { toast(`Failed to delete #${id}: ${err.message}`, "error"); }
  }
  toast(`Deleted ${ids.length} record(s).`, "success");
  await loadTable();
});

// ── Export ─────────────────────────────────────────────────────────────────
function selectedIds() {
  return [...document.querySelectorAll(".row-check:checked")].map(cb => cb.dataset.id).join(",");
}

document.getElementById("export-csv-btn").addEventListener("click", () => {
  const ids = selectedIds();
  window.location.href = `/export/csv${ids ? "?ids=" + ids : ""}`;
});
document.getElementById("export-xlsx-btn").addEventListener("click", () => {
  const ids = selectedIds();
  window.location.href = `/export/xlsx${ids ? "?ids=" + ids : ""}`;
});

// ── Detail panel ───────────────────────────────────────────────────────────
const overlay = document.getElementById("detail-overlay");
document.getElementById("close-detail").addEventListener("click", closeDetail);
overlay.addEventListener("click", e => { if (e.target === overlay) closeDetail(); });

function closeDetail() {
  overlay.classList.add("hidden");
  openNameplateId = null;
}

async function openDetail(id) {
  openNameplateId = id;
  overlay.classList.remove("hidden");

  document.getElementById("detail-title").textContent = `Nameplate #${id}`;
  document.getElementById("detail-status-bar").textContent = "Loading…";
  document.getElementById("attributes-container").innerHTML = "";
  document.getElementById("raw-ocr-pre").textContent = "";

  try {
    const np = await apiJSON(`/nameplates/${id}`);
    renderDetailPanel(np);
  } catch (err) {
    document.getElementById("detail-status-bar").textContent = `Error: ${err.message}`;
  }
}

function renderDetailPanel(np) {
  document.getElementById("detail-title").textContent = `Nameplate #${np.id}`;
  document.getElementById("detail-filename").textContent = np.filename;
  document.getElementById("detail-uploaded").textContent = fmtDate(np.uploaded_at);
  document.getElementById("detail-status-bar").innerHTML = `Status: ${statusBadge(np.status)}` +
    (np.error_message ? `<br><span style="color:var(--danger);font-size:.8rem">${escHtml(np.error_message)}</span>` : "");
  document.getElementById("raw-ocr-pre").textContent = np.ocr_raw_text || "(none)";

  const container = document.getElementById("attributes-container");
  container.innerHTML = "";
  (np.attributes || []).forEach(attr => addAttrRow(container, attr));
}

function addAttrRow(container, attr = {}) {
  const row = document.createElement("div");
  row.className = "attr-row";
  row.dataset.id = attr.id || "";
  row.innerHTML = `
    <input type="text"  class="attr-name"  placeholder="Attribute" value="${escHtml(attr.attribute_name || "")}" />
    <input type="text"  class="attr-value" placeholder="Value"     value="${escHtml(attr.attribute_value || "")}" />
    <button class="btn-icon remove-attr-btn" title="Remove">✕</button>`;
  row.querySelector(".remove-attr-btn").addEventListener("click", () => row.remove());
  container.appendChild(row);
}

document.getElementById("add-attr-btn").addEventListener("click", () => {
  addAttrRow(document.getElementById("attributes-container"));
});

document.getElementById("save-attrs-btn").addEventListener("click", async () => {
  if (!openNameplateId) return;
  const rows = document.querySelectorAll("#attributes-container .attr-row");
  const updates = [];
  rows.forEach(row => {
    const name  = row.querySelector(".attr-name").value.trim();
    const value = row.querySelector(".attr-value").value.trim();
    if (!name) return;
    const entry = { attribute_name: name, attribute_value: value || null };
    if (row.dataset.id) entry.id = Number(row.dataset.id);
    updates.push(entry);
  });

  try {
    await apiJSON(`/nameplates/${openNameplateId}/attributes`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    toast("Attributes saved.", "success");
    await loadTable();
    openDetail(openNameplateId);
  } catch (err) {
    toast(`Save failed: ${err.message}`, "error");
  }
});

document.getElementById("reprocess-btn").addEventListener("click", async () => {
  if (!openNameplateId) return;
  try {
    await apiJSON(`/nameplates/${openNameplateId}/reprocess`, { method: "POST" });
    toast(`Reprocessing #${openNameplateId}…`, "info");
    startPolling(openNameplateId);
    await loadTable();
    openDetail(openNameplateId);
  } catch (err) {
    toast(`Reprocess failed: ${err.message}`, "error");
  }
});

// ── Helpers ────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + "…" : s; }
function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}
function statusBadge(status) {
  const cls = { pending: "badge-pending", processed: "badge-processed", failed: "badge-failed" }[status] || "badge-pending";
  return `<span class="badge ${cls}">${status}</span>`;
}

// ── Init ───────────────────────────────────────────────────────────────────
loadTable();
