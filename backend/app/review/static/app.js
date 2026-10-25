"use strict";
// Paper Trail local review UI. All document-derived text is inserted with textContent.

const state = {
  token: "", docs: [], detail: null, page: 1, active: null, editing: false,
  imageUrl: null, graph: null, selectedNode: null, graphRecord: null,
  graphRecordFor: null, graphDetailError: null, graphDetailRequest: 0,
};

const HEADER_FIELDS = [
  ["invoice_number", "Invoice number"], ["invoice_date", "Invoice date"],
  ["currency", "Currency"], ["bank_account_iban", "IBAN"],
  ["subtotal", "Subtotal"], ["tax_amount", "Tax amount"], ["total_amount", "Total amount"],
];
const PARTY_FIELDS = {
  vendor: [["name", "Name"], ["address", "Address"], ["tax_id", "Tax ID"]],
  customer: [["name", "Name"], ["address", "Address"], ["tax_id", "Tax ID"], ["phone", "Phone"], ["email", "Email"]],
};
const ITEM_FIELDS = [
  ["description", "Description"], ["quantity", "Qty"], ["unit_price", "Unit price"],
  ["net_amount", "Net"], ["tax_rate_percent", "Tax %"], ["vat_amount", "Tax amount"], ["gross_amount", "Gross"],
];
const STATUS_TEXT = {
  needs_review: "Needs review", approved: "Approved", corrected: "Corrected",
  valid_reconciled: "Checks pass", arithmetic_mismatch: "Amounts disagree",
  incomplete_extraction: "Incomplete", ambiguous_review: "Needs a closer look",
  queued: "Queued", processing: "Processing", complete: "Processed", failed: "Failed",
  deleted: "File deleted", removed: "Removed", superseded: "Superseded", active: "Current",
};

// ---------- small helpers ----------
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function svg(tag, attrs, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  children.flat().forEach((child) => child && node.append(child));
  return node;
}
const $ = (id) => document.getElementById(id);
// replaceChildren() would render null as the text "null"; drop empty children first.
function fill(node, ...children) { node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false)); return node; }
const statusPill = (value) => value ? el("span", { class: `status ${value}`, text: STATUS_TEXT[value] || value }) : null;
const show = (value) => (value === null || value === undefined || value === "") ? "—" : String(value);

function storageGet(key) { try { return window.localStorage.getItem(key) || ""; } catch (e) { return ""; } }
function storageSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* optional */ } }

function notify(message, isError) {
  const notice = $("notice");
  notice.textContent = message;
  notice.classList.toggle("error", Boolean(isError));
  notice.hidden = !message;
}

async function api(path, options) {
  const response = await fetch(path, {
    ...(options || {}),
    headers: { "X-Review-Token": state.token, ...((options && options.headers) || {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).error || message; } catch (e) { /* not JSON */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response;
}

// ---------- documents ----------
async function loadDocuments() {
  try {
    state.docs = await (await api("/api/documents")).json();
    renderDocList();
    if (!state.detail && state.docs.length) selectDocument(state.docs[0].document_id);
    if (!state.docs.length) notify("No documents yet. Start the watcher with an extraction provider and add files to the watched folder.");
  } catch (error) {
    notify(error.status === 401 ? "This page needs the review link printed by the server (it contains the token)." : `Could not load documents: ${error.message}`, true);
  }
}

function renderDocList() {
  const list = $("doc-list");
  fill(list, ...state.docs.map((doc) => el("li", {},
    el("button", {
      type: "button", "aria-current": state.detail && state.detail.document_id === doc.document_id ? "true" : "false",
      onclick: () => selectDocument(doc.document_id),
    },
    el("span", { class: "doc-file", text: doc.file || doc.document_id }),
    el("span", { class: "doc-meta" },
      statusPill(doc.review_status || doc.file_status), " ",
      doc.validation_status ? STATUS_TEXT[doc.validation_status] || doc.validation_status : (doc.last_error ? "Extraction failed" : "Not extracted yet"))))));
}

async function selectDocument(documentId) {
  state.editing = false;
  state.active = null;
  state.page = 1;
  try {
    state.detail = await (await api(`/api/documents/${encodeURIComponent(documentId)}`)).json();
  } catch (error) {
    notify(`Could not open document: ${error.message}`, true);
    return;
  }
  notify("");
  renderDocList();
  renderDetail();
  loadPage();
}

function extraction() { return state.detail && state.detail.extraction; }
function effective() { const ex = extraction(); return ex ? ex.effective_record : null; }

function pageInfo(page) {
  const record = effective();
  const prov = (record && record.provenance) || {};
  const docPages = (prov.document_pages && prov.document_pages.pages) || [];
  const parsed = (prov.parser && prov.parser.pages) || [];
  const entry = docPages.find((p) => p.page === page) || parsed.find((p) => p.page === page) || {};
  return { width: entry.width, height: entry.height, count: Math.max(docPages.length, parsed.length, 1) };
}

async function loadPage() {
  const img = $("page-img");
  const ex = extraction();
  fill($("boxes"), );
  if (state.imageUrl) { URL.revokeObjectURL(state.imageUrl); state.imageUrl = null; }
  img.removeAttribute("src");
  $("sheet").hidden = true;
  $("page-empty").hidden = false;
  if (!ex) { $("page-title").textContent = "No extraction for this document"; return; }
  const info = pageInfo(state.page);
  $("page-title").textContent = ex.record.provenance.source_file || state.detail.document_id;
  $("page-label").textContent = `Page ${state.page} of ${info.count}`;
  $("prev-page").disabled = state.page <= 1;
  $("next-page").disabled = state.page >= info.count;
  if (state.detail.file_state !== "current") {
    $("page-empty").textContent = state.detail.file_state === "changed"
      ? "The file changed after this extraction, so its page is not shown. Review the newer version once it is processed."
      : "The source file is not available on disk.";
    return;
  }
  try {
    const blob = await (await api(`/api/documents/${encodeURIComponent(state.detail.document_id)}/pages/${state.page}.png`)).blob();
    state.imageUrl = URL.createObjectURL(blob);
    img.onload = () => { $("sheet").hidden = false; $("page-empty").hidden = true; drawBoxes(); };
    img.src = state.imageUrl;
    img.alt = `Page ${state.page} of the source document`;
  } catch (error) {
    $("page-empty").textContent = `Source page unavailable: ${error.message}`;
  }
}

function flaggedPaths() {
  const ex = extraction();
  const findings = ((effective() || {}).findings || (ex && ex.record.findings) || []);
  const paths = new Set();
  findings.filter((f) => f.effect !== "none").forEach((f) => {
    if (f.field) paths.add(f.field);
    if (f.row && !f.field) paths.add(`items[${f.row - 1}]`);
  });
  return paths;
}

function drawBoxes() {
  const record = effective();
  const layer = $("boxes");
  fill(layer, );
  if (!record) return;
  const img = $("page-img");
  const info = pageInfo(state.page);
  const width = info.width || img.naturalWidth;
  const height = info.height || img.naturalHeight;
  if (!width || !height) return;
  const flagged = flaggedPaths();
  for (const [path, ev] of Object.entries(record.field_evidence || {})) {
    if (!ev || !Array.isArray(ev.bbox) || ev.page !== state.page) continue;
    let box = ev.bbox;
    if (Array.isArray(box[0])) {  // polygon -> bounding box
      const xs = box.map((p) => p[0]); const ys = box.map((p) => p[1]);
      box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    const unit = box.every((v) => v <= 1);
    const [x0, y0, x1, y1] = unit ? box : [box[0] / width, box[1] / height, box[2] / width, box[3] / height];
    const node = el("div", {
      class: `box${isFlagged(path, flagged) ? " flagged" : ""}${state.active === path ? " active" : ""}`,
      title: path, "data-path": path, onclick: () => setActive(path, true),
    });
    node.style.left = `${x0 * 100}%`; node.style.top = `${y0 * 100}%`;
    node.style.width = `${(x1 - x0) * 100}%`; node.style.height = `${(y1 - y0) * 100}%`;
    layer.append(node);
  }
}

function isFlagged(path, flagged) {
  if (flagged.has(path)) return true;
  const row = path.match(/^items\[(\d+)\]/);
  return Boolean(row && flagged.has(`items[${row[1]}]`));
}

function setActive(path, scrollRow) {
  state.active = path;
  const ev = ((effective() || {}).field_evidence || {})[path];
  if (ev && ev.page && ev.page !== state.page) { state.page = ev.page; loadPage(); }
  document.querySelectorAll(".box").forEach((b) => b.classList.toggle("active", b.dataset.path === path));
  document.querySelectorAll("tr.field").forEach((r) => r.classList.toggle("active", r.dataset.path === path));
  const target = document.querySelector(`.box[data-path="${CSS.escape(path)}"]`);
  if (target && !scrollRow) target.scrollIntoView({ block: "nearest", inline: "nearest" });
  const row = document.querySelector(`tr.field[data-path="${CSS.escape(path)}"]`);
  if (row && scrollRow) row.scrollIntoView({ block: "nearest" });
}

// ---------- detail panel ----------
function renderDetail() {
  const ex = extraction();
  const record = effective();
  const summary = $("summary");
  $("actions").hidden = !ex;
  if (!ex) {
    fill(summary, el("p", { class: "empty", text: "This document has no active extraction (it was removed, failed, or is still queued)." }));
    fill($("findings"), ); fill($("field-table"), ); renderHistory(); return;
  }
  const prov = record.provenance || {};
  const usage = (prov.generation_diagnostics || {}).usage;
  fill(summary,
    el("div", { class: "title", text: record.invoice_number ? `Invoice ${record.invoice_number}` : "Invoice number not read" }),
    el("div", { class: "line" }, statusPill(ex.review_status), statusPill(record.validation_status || ex.validation_status),
      `Version ${show(ex.version)}`, state.detail.file_state === "changed" ? statusPill("changed") : null),
    el("div", { class: "line", text: `Read by ${show(prov.provider)} (${show(prov.extraction_method)})` +
      (prov.parser && prov.parser.engine ? `, text from ${prov.parser.engine}` : "") +
      (usage && usage.input_tokens ? `, ${usage.input_tokens} in / ${usage.output_tokens} out tokens` : "") }),
  );
  renderFindings(record.findings || ex.record.findings || []);
  renderFields(record);
  renderHistory();
  $("approve").hidden = state.editing; $("edit").hidden = state.editing;
  $("save").hidden = !state.editing; $("cancel").hidden = !state.editing;
}

function renderFindings(findings) {
  const important = findings.filter((f) => f.effect !== "none");
  const info = findings.filter((f) => f.effect === "none");
  const button = (f) => el("button", {
    type: "button", class: `finding ${f.effect}`, onclick: () => f.field && setActive(f.field, true),
    text: f.message,
  });
  fill($("findings"),
    important.length ? el("h2", { text: `To check (${important.length})` }) : el("p", { class: "line", text: "No checks failed. A person still has to approve it." }),
    ...important.map(button),
    info.length ? el("details", { class: "findings-info" }, el("summary", { text: `Notes (${info.length})` }), ...info.map(button)) : null,
  );
}

function fieldRow(path, label, record, editable) {
  const ev = (record.field_evidence || {})[path] || {};
  const value = valueAt(record, path);
  const flagged = isFlagged(path, flaggedPaths());
  const match = ev.match && ev.match.source ? ev.match.source.status : null;
  const row = el("tr", {
    class: `field${flagged ? " flagged" : ""}${state.active === path ? " active" : ""}`,
    "data-path": path, tabindex: "0",
    onmouseenter: () => setActive(path, false), onfocus: () => setActive(path, false),
  });
  const valueCell = editable
    ? el("td", { colspan: "2" }, el("input", { "data-edit": path, "aria-label": label, value: editValue(ev, value) }))
    : [el("td", { class: "raw", text: ev.raw_text === null || ev.raw_text === undefined ? "—" : ev.raw_text,
                  title: ev.raw_text ? "Text as read from the document" : "No raw text (typed value from the provider)" }),
       el("td", { class: "parsed", text: show(value) })];
  row.append(el("td", { text: label }), ...[valueCell].flat(),
    el("td", { class: `match ${match || ""}`, text: matchText(match, ev) }));
  return row;
}

function matchText(match, ev) {
  const labels = { exact: "found", normalized: "found", ocr_folded: "found (OCR variant)", fuzzy: "close match",
    lines: "found by line", not_found: "not in OCR text", too_short: "too short to check" };
  if (ev.method === "human") return "entered by reviewer";
  if (!ev.quote) return ev.page ? `page ${ev.page}` : "no evidence";
  return `${labels[match] || "quoted"}${ev.page ? `, p.${ev.page}` : ""}`;
}

function valueAt(record, path) {
  const item = path.match(/^items\[(\d+)\]\.(\w+)$/);
  if (item) return ((record.items || [])[Number(item[1])] || {})[item[2]];
  const parts = path.split(".");
  return parts.length === 2 ? (record[parts[0]] || {})[parts[1]] : record[path];
}

function editValue(ev, value) {
  if (ev && typeof ev.raw_text === "string" && ev.method !== "human") return ev.raw_text;
  return value === null || value === undefined ? "" : String(value);
}

function renderFields(record) {
  const editable = state.editing;
  const head = el("thead", {}, el("tr", {}, el("th", { text: "Field" }),
    ...(editable ? [el("th", { colspan: "2", text: "Corrected value (as printed)" })]
      : [el("th", { text: "As read" }), el("th", { text: "Parsed" })]), el("th", { text: "Evidence" })));
  const body = el("tbody");
  body.append(el("tr", { class: "group" }, el("td", { colspan: "4", text: "Invoice" })));
  HEADER_FIELDS.forEach(([key, label]) => body.append(fieldRow(key, label, record, editable)));
  for (const [role, fields] of Object.entries(PARTY_FIELDS)) {
    body.append(el("tr", { class: "group" }, el("td", { colspan: "4", text: role === "vendor" ? "Vendor (issued the invoice)" : "Customer (billed)" })));
    fields.forEach(([key, label]) => body.append(fieldRow(`${role}.${key}`, label, record, editable)));
  }
  const nodes = [el("table", { class: "grid" }, head, body)];
  if (editable) {
    nodes.push(itemsEditor(record));
  } else {
    (record.items || []).forEach((item, index) => {
      body.append(el("tr", { class: "group" }, el("td", { colspan: "4", text: `Line ${index + 1}${item.page ? ` (page ${item.page})` : ""}` })));
      ITEM_FIELDS.forEach(([key, label]) => body.append(fieldRow(`items[${index}].${key}`, label, record, false)));
    });
    if (!(record.items || []).length) body.append(el("tr", {}, el("td", { colspan: "4", class: "empty", text: "No line items were read." })));
  }
  fill($("field-table"), ...nodes);
}

function itemsEditor(record) {
  const tbody = el("tbody");
  const addRow = (item, ev, index) => {
    const tr = el("tr", { class: "item-edit" });
    ITEM_FIELDS.forEach(([key, label]) => {
      const path = `items[${index}].${key}`;
      tr.append(el("td", {}, el(key === "description" ? "input" : "input", {
        "data-item": key, "aria-label": `Line ${index + 1} ${label}`, value: editValue(ev[path], (item || {})[key]),
      })));
    });
    tr.append(el("td", {}, el("button", { type: "button", class: "quiet", "aria-label": "Remove line", text: "Remove",
      onclick: () => tr.remove() })));
    tbody.append(tr);
  };
  (record.items || []).forEach((item, index) => addRow(item, record.field_evidence || {}, index));
  return el("div", { class: "items-edit" },
    el("h2", { text: "Line items" }),
    el("table", { class: "grid" },
      el("thead", {}, el("tr", {}, ...ITEM_FIELDS.map(([, label]) => el("th", { text: label })), el("th"))),
      tbody),
    el("button", { type: "button", text: "Add line", onclick: () => addRow({}, {}, tbody.children.length) }));
}

function collectCorrection() {
  const record = effective();
  const value = (path) => {
    const input = document.querySelector(`input[data-edit="${CSS.escape(path)}"]`);
    const text = input ? input.value.trim() : "";
    return text === "" ? null : text;
  };
  const corrected = { doc_type: record.doc_type || null, vendor: {}, customer: {}, items: [] };
  HEADER_FIELDS.forEach(([key]) => { corrected[key] = value(key); });
  for (const [role, fields] of Object.entries(PARTY_FIELDS)) fields.forEach(([key]) => { corrected[role][key] = value(`${role}.${key}`); });
  document.querySelectorAll("tr.item-edit").forEach((tr) => {
    const item = {};
    tr.querySelectorAll("input[data-item]").forEach((input) => { item[input.dataset.item] = input.value.trim() || null; });
    if (Object.values(item).some((v) => v !== null)) corrected.items.push(item);
  });
  return corrected;
}

async function submitReview(decision) {
  const ex = extraction();
  if (!ex) return;
  const reviewer = $("reviewer").value.trim();
  if (!reviewer) { notify("Add your name in the Reviewer box so the review history says who decided.", true); $("reviewer").focus(); return; }
  storageSet("papertrail.reviewer", reviewer);
  const payload = { decision, reviewer, note: $("note").value, expected_extraction_id: ex.extraction_id };
  if (decision === "corrected") payload.corrected_record = collectCorrection();
  try {
    const result = await (await api(`/api/documents/${encodeURIComponent(state.detail.document_id)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    })).json();
    state.detail = result.document;
    state.editing = false;
    $("note").value = "";
    const changed = ["nodes_added", "nodes_updated", "nodes_removed"].reduce((n, k) => n + result.change[k].length, 0);
    notify(`${decision === "approved" ? "Approved" : "Correction saved"}. ${changed} graph node(s) changed.`);
    renderDetail(); drawBoxes(); loadDocuments();
  } catch (error) {
    notify(error.status === 409 ? `${error.message}.` : `Review not saved: ${error.message}`, true);
  }
}

function renderHistory() {
  const detail = state.detail;
  if (!detail || !detail.versions.length) { fill($("history"), ); return; }
  fill($("history"), el("div", { class: "history" },
    el("h2", { text: "History" }),
    el("ul", {}, ...detail.versions.slice().reverse().map((v) => el("li", {},
      `Version ${show(v.version)} by ${show(v.provider)}: `, statusPill(v.state), " ", statusPill(v.review_status),
      v.reviews.length ? el("ul", {}, ...v.reviews.map((r) => el("li", {
        text: `${STATUS_TEXT[r.decision] || r.decision} by ${r.reviewer || "unknown"} on ${String(r.created_at).slice(0, 16).replace("T", " ")}` +
          `${r.state === "superseded" ? " (later replaced)" : ""}${r.note ? ` — ${r.note}` : ""}`,
      }))) : null)))));
}

// ---------- graph ----------
async function loadGraph() {
  try {
    const reviewed = $("reviewed-only").checked ? "1" : "0";
    state.graph = await (await api(`/api/graph?reviewed_only=${reviewed}`)).json();
    if (state.selectedNode && state.graph.nodes.some((node) => node.id === state.selectedNode)) {
      selectNode(state.selectedNode); // refresh details against the current support filter
    } else {
      state.selectedNode = null;
      state.graphRecord = null;
      state.graphRecordFor = null;
      renderGraph();
    }
  } catch (error) {
    notify(`Could not load the graph: ${error.message}`, true);
  }
}

function nodeStatus(node) {
  const support = node.support || [];
  return support.length && support.every((s) => s.review_status === "approved" || s.review_status === "corrected")
    ? "reviewed" : "needs_review";
}

const GRAPH_COLUMNS = [
  { type: "Document", title: "Documents", x: 24 },
  { type: "Vendor", title: "Vendors", x: 294 },
  { type: "Invoice", title: "Invoices", x: 564 },
  { type: "Customer", title: "Customers", x: 834 },
  { type: "LineItem", title: "Line items", x: 1104 },
];
const GRAPH_NODE_TYPES = new Set(GRAPH_COLUMNS.map((column) => column.type));
const GRAPH_EDGE_TYPES = new Set(["SUPPORTS", "ISSUED", "BILLED_TO", "CONTAINS"]);

function graphNodeLabel(node, itemCounts, documentsByInvoice) {
  const props = node.props || {};
  if (node.type === "Document") {
    return [props.filename || props.document_id || node.id, `Version ${show(props.version)} · ${show(props.doc_type || "document")}`];
  }
  if (node.type === "Invoice") {
    const doc = documentsByInvoice[node.id];
    const total = props.total_amount == null ? "Total unavailable" : `Total ${props.total_amount}`;
    return [props.invoice_number || "(no invoice number)", `${total} · ${itemCounts[node.id] || 0} line(s)${doc ? ` · ${doc.props.filename || ""}` : ""}`];
  }
  if (node.type === "LineItem") {
    const description = props.description || "(description unavailable)";
    const gross = props.gross_amount == null ? "Gross unavailable" : `Gross ${props.gross_amount}`;
    return [description, `Row ${(Number(props.row_index) || 0) + 1} · ${gross}`];
  }
  return [props.name || `(${node.type.toLowerCase()} name unavailable)`,
    props.tax_id ? `Tax ID ${props.tax_id}` : "No tax ID"];
}

function renderGraph() {
  const graph = state.graph;
  if (!graph) return;
  const nodes = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const columns = Object.fromEntries(GRAPH_COLUMNS.map(({ type }) => [type, []]));
  graph.nodes.forEach((node) => { if (GRAPH_NODE_TYPES.has(node.type)) columns[node.type].push(node); });
  const itemCounts = {}, documentsByInvoice = {};
  graph.edges.filter((edge) => edge.type === "CONTAINS").forEach((edge) => {
    itemCounts[edge.source] = (itemCounts[edge.source] || 0) + 1;
  });
  graph.edges.filter((edge) => edge.type === "SUPPORTS").forEach((edge) => {
    if (nodes[edge.source] && nodes[edge.target]) documentsByInvoice[edge.target] = nodes[edge.source];
  });
  const label = (node) => graphNodeLabel(node, itemCounts, documentsByInvoice);
  const W = 228, H = 58, GAP = 18, position = {};
  GRAPH_COLUMNS.forEach(({ type, x }) => {
    columns[type].sort((a, b) => label(a)[0].localeCompare(label(b)[0]));
    columns[type].forEach((node, i) => { position[node.id] = { x, y: 52 + i * (H + GAP) }; });
  });
  const rows = Math.max(1, ...Object.values(columns).map((list) => list.length));
  const root = $("graph-svg"), width = GRAPH_COLUMNS.at(-1).x + W + 24;
  const height = 52 + rows * (H + GAP) + 16;
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  root.setAttribute("width", width);
  root.setAttribute("height", height);
  const selected = state.selectedNode;
  const edges = graph.edges.filter((edge) => GRAPH_EDGE_TYPES.has(edge.type)
    && position[edge.source] && position[edge.target]);
  const children = [
    svg("defs", {}, svg("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" },
    svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrow-head" }))),
    ...GRAPH_COLUMNS.map(({ type, title, x }) => svg("text", {
      x, y: 30, class: "col-title", text: `${title} (${columns[type].length})`,
    })),
    ...edges.map((edge) => {
      const a = position[edge.source], b = position[edge.target];
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
      const mid = (x1 + x2) / 2, midY = (y1 + y2) / 2;
      const on = selected && (edge.source === selected || edge.target === selected);
      return svg("g", { class: `edge-group${on ? " on" : ""}` },
        svg("path", { class: "edge", d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`,
          "marker-end": "url(#graph-arrow)" }),
        svg("text", { class: "edge-label", x: mid, y: midY - 5, "text-anchor": "middle", text: edge.type }));
    }),
    ...Object.entries(position).map(([id, point]) => {
      const node = nodes[id], [line1, line2] = label(node);
      return svg("g", {
        class: `node node-${node.type.toLowerCase()} ${nodeStatus(node)}${selected === id ? " on" : ""}`,
        transform: `translate(${point.x},${point.y})`, tabindex: "0", role: "button",
        "aria-label": `${node.type} ${line1}, ${nodeStatus(node) === "reviewed" ? "reviewed" : "needs review"}`,
        "aria-pressed": String(selected === id),
        onclick: () => selectNode(id),
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectNode(id); } },
      }, svg("title", { text: `${node.type}: ${line1}` }), svg("rect", { width: W, height: H }),
      svg("text", { x: 10, y: 22, text: truncate(line1, 30) }),
      svg("text", { x: 10, y: 43, class: "node-subtitle", text: truncate(line2, 30) }));
    }),
  ];
  fill(root, ...children);
  $("graph-counts").textContent = `${graph.nodes.length} nodes, ${graph.edges.length} edges` +
    (graph.nodes.length ? " · Dashed outline: needs review; solid green: reviewed." : " · Nothing to show yet.");
  renderSuggestions(graph);
  if (selected && nodes[selected]) renderNodeDetail(nodes[selected]);
  else fill($("node-detail"), el("p", { class: "empty", text: "Select a node to see its properties, evidence, and supporting documents." }));
}

const truncate = (text, n) => (String(text).length > n ? `${String(text).slice(0, n - 1)}…` : String(text));

function selectNode(id) {
  state.selectedNode = id;
  state.graphRecord = null;
  state.graphRecordFor = null;
  state.graphDetailError = null;
  const request = ++state.graphDetailRequest;
  renderGraph();
  const node = state.graph && state.graph.nodes.find((candidate) => candidate.id === id);
  const documentId = node && node.support && node.support[0] && node.support[0].document_id;
  if (!documentId) return;
  api(`/api/documents/${encodeURIComponent(documentId)}`).then((response) => response.json())
    .then((detail) => {
      if (request !== state.graphDetailRequest || state.selectedNode !== id) return;
      state.graphRecord = detail.extraction && detail.extraction.effective_record;
      state.graphRecordFor = documentId;
      renderNodeDetail(node);
    })
    .catch((error) => {
      if (request !== state.graphDetailRequest || state.selectedNode !== id) return;
      state.graphDetailError = error.message;
      renderNodeDetail(node);
    });
}

function graphEvidencePaths(node) {
  const evidence = (state.graphRecord || {}).field_evidence || {};
  if (node.type === "Invoice") {
    return new Set(["invoice_number", "invoice_date", "subtotal", "tax_amount", "total_amount",
      "currency", "bank_account_iban"]);
  }
  if (node.type === "Vendor") return new Set(Object.keys(evidence).filter((path) => path.startsWith("vendor.")));
  if (node.type === "Customer") return new Set(Object.keys(evidence).filter((path) => path.startsWith("customer.")));
  if (node.type === "LineItem") {
    const index = Number((node.props || {}).row_index ?? (node.props || {}).position);
    return Number.isInteger(index) && index >= 0
      ? new Set(Object.keys(evidence).filter((path) => path.startsWith(`items[${index}].`) || path.startsWith(`items.${index}.`)))
      : new Set();
  }
  return new Set();
}

function renderNodeDetail(node) {
  const props = Object.entries(node.props || {}).filter(([key, value]) => key !== "variants"
    && key !== "match_key" && value !== null && typeof value !== "object");
  const panel = $("node-detail"), evidence = (state.graphRecord || {}).field_evidence || {};
  const paths = graphEvidencePaths(node), provenance = (state.graphRecord || {}).provenance || {};
  const sources = node.support || [];
  const evidenceRows = [...paths].filter((path) => evidence[path]).map((path) => {
    const entry = evidence[path];
    return el("li", {}, el("strong", { text: path }),
      ` · ${entry.method || "source"}${entry.page ? ` · page ${entry.page}` : ""}`,
      entry.quote || entry.raw_text ? el("blockquote", { text: entry.quote || entry.raw_text }) : null,
      entry.match && entry.match.source ? el("span", { class: "match", text: `Source match: ${entry.match.source.status}` }) : null);
  });
  const provenanceRows = ["provider", "model_revision", "prompt_version", "extraction_method"]
    .filter((key) => provenance[key]).map((key) => [key.replace(/_/g, " "), String(provenance[key])]);
  fill(panel,
    el("h2", { text: `${node.type} · ${nodeStatus(node) === "reviewed" ? "Reviewed" : "Needs review"}` }),
    el("dl", { class: "props" }, ...props.flatMap(([key, value]) => [
      el("dt", { text: key.replace(/_/g, " ") }), el("dd", { text: String(value) }),
    ])),
    node.props && node.props.variants ? el("p", { text: `Spelled ${node.props.variants.length} ways across documents; exact match keys agree.` }) : null,
    provenanceRows.length ? el("h3", { text: "Extraction provenance" },
      el("dl", { class: "props" }, ...provenanceRows.flatMap(([key, value]) => [
        el("dt", { text: key }), el("dd", { text: value }),
      ]))) : null,
    paths.size ? el("section", {}, el("h3", { text: `Source evidence${state.graphRecordFor ? ` · ${documentName(state.graphRecordFor)}` : ""}` }),
      evidenceRows.length ? el("ul", { class: "graph-evidence" }, ...evidenceRows)
        : el("p", { class: "empty", text: state.graphDetailError
          ? `Could not load source evidence: ${state.graphDetailError}`
          : state.graphRecord ? "No field evidence was recorded for this node." : "Loading source evidence…" })) : null,
    el("h3", { text: `Supported by ${sources.length} document(s)` }),
    sources.length ? el("ul", { class: "support" }, ...sources.map((source) => el("li", {},
      el("span", {}, `${documentName(source.document_id)} v${show(source.version)} `, statusPill(source.review_status)),
      el("button", { type: "button", class: "quiet", text: "Open in review", onclick: () => {
        switchView("review"); selectDocument(source.document_id);
      } })))) : el("p", { class: "empty", text: "No active document support is attached." }),
  );
}

function documentName(id) { const doc = state.docs.find((d) => d.document_id === id); return doc ? doc.file : id.slice(0, 8); }

function renderSuggestions(graph) {
  const items = [
    ...graph.suggestions.map((s) => el("li", { text: `${s.reason === "same_name_different_tax_id" ? "Same name, different tax ID" : "Same tax ID, different name"}: ${s.node_ids.length} ${s.type.toLowerCase()} nodes kept separate.` })),
    ...graph.duplicates.map((d) => el("li", { text: `Identical files: ${d.documents.map((x) => x.source_file).join(", ")}` })),
  ];
  fill($("suggestions"), ...(items.length ? [el("h2", { text: "Possible duplicates to check" }), el("ul", {}, ...items)] : []));
}

// ---------- changes ----------
async function loadChanges() {
  try {
    const rows = await (await api("/api/changes")).json();
    fill($("change-rows"), ...rows.slice().reverse().map((c) => el("tr", {},
      el("td", { text: String(c.created_at).slice(0, 19).replace("T", " ") }), el("td", { text: c.action }),
      el("td", { text: c.document_id ? documentName(c.document_id) : "—" }), el("td", { text: show(c.version) }),
      el("td", { text: show(c.provider) }),
      el("td", { text: `${c.nodes_added.length} / ${c.nodes_updated.length} / ${c.nodes_removed.length}` }),
      el("td", { text: `${c.edges_added.length} / ${c.edges_updated.length} / ${c.edges_removed.length}` }),
      el("td", { text: show(c.run_id) }))));
    if (!rows.length) fill($("change-rows"), el("tr", {}, el("td", { colspan: "8", class: "empty", text: "No graph changes recorded yet." })));
  } catch (error) {
    notify(`Could not load changes: ${error.message}`, true);
  }
}

// ---------- shell ----------
function switchView(view) {
  document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  ["review", "graph", "changes"].forEach((v) => { $(`view-${v}`).hidden = v !== view; });
  if (view === "graph") loadGraph();
  if (view === "changes") loadChanges();
}

function init() {
  const match = window.location.hash.match(/token=([\w-]+)/);
  if (match) {
    state.token = match[1];
    try { window.sessionStorage.setItem("papertrail.token", state.token); } catch (e) { /* optional */ }
    history.replaceState(null, "", window.location.pathname);
  } else {
    try { state.token = window.sessionStorage.getItem("papertrail.token") || ""; } catch (e) { state.token = ""; }
  }
  $("reviewer").value = storageGet("papertrail.reviewer");
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $("refresh").addEventListener("click", loadDocuments);
  $("approve").addEventListener("click", () => submitReview("approved"));
  $("edit").addEventListener("click", () => { state.editing = true; renderDetail(); });
  $("cancel").addEventListener("click", () => { state.editing = false; renderDetail(); });
  $("save").addEventListener("click", () => submitReview("corrected"));
  $("prev-page").addEventListener("click", () => { state.page -= 1; loadPage(); });
  $("next-page").addEventListener("click", () => { state.page += 1; loadPage(); });
  $("reviewed-only").addEventListener("change", loadGraph);
  window.addEventListener("resize", drawBoxes);
  if (!state.token) { notify("Open the review link printed by the server; it carries the access token.", true); return; }
  loadDocuments();
}

document.addEventListener("DOMContentLoaded", init);
