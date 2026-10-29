"use strict";
// Paper Trail local review UI. All document-derived text is inserted with textContent.

const state = {
  token: "", docs: [], detail: null, page: 1, active: null, editing: false,
  imageUrl: null, graph: null, selectedNode: null, graphPage: 0, graphPageSize: 8,
  docSearch: "",
  graphSource: "Neo4j",
  graphZoom: 1, graphPanX: 0, graphPanY: 0, graphDrag: null,
  graphRotationX: -.18, graphRotationY: .35, graph3d: false,
  graphBaseLayout: null, graphLayoutNodes: null, graphLayoutEdges: null, graphLayoutSignature: "",
  graphViewWidth: 1400, graphViewHeight: 820,
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
  const query = state.docSearch.trim().toLocaleLowerCase();
  const visible = state.docs.filter((doc) => !query ||
    `${doc.file || ""} ${doc.validation_status || ""} ${doc.review_status || ""}`.toLocaleLowerCase().includes(query));
  $("doc-count").textContent = query ? `${visible.length}/${state.docs.length}` : state.docs.length;
  fill(list, ...(visible.length ? visible.map((doc) => el("li", {},
    el("button", {
      type: "button", "aria-current": state.detail && state.detail.document_id === doc.document_id ? "true" : "false",
      onclick: () => selectDocument(doc.document_id),
    },
    el("span", { class: "doc-file", text: doc.file || doc.document_id }),
    el("span", { class: "doc-meta" },
      statusPill(doc.review_status || doc.file_status), " ",
      doc.validation_status ? STATUS_TEXT[doc.validation_status] || doc.validation_status : (doc.last_error ? "Extraction failed" : "Not extracted yet")))))
    : [el("li", { class: "empty", text: query ? "No matching documents." : "No documents available." })]));
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
  body.append(el("tr", { class: "group invoice-group" }, el("td", { colspan: "4", text: "Invoice" })));
  HEADER_FIELDS.forEach(([key, label]) => body.append(fieldRow(key, label, record, editable)));
  for (const [role, fields] of Object.entries(PARTY_FIELDS)) {
    body.append(el("tr", { class: `group ${role}-group` }, el("td", { colspan: "4", text: role === "vendor" ? "Vendor (issued the invoice)" : "Customer (billed)" })));
    fields.forEach(([key, label]) => body.append(fieldRow(`${role}.${key}`, label, record, editable)));
  }
  const nodes = [el("table", { class: "grid" }, head, body)];
  if (editable) {
    nodes.push(itemsEditor(record));
  } else {
    (record.items || []).forEach((item, index) => {
      body.append(el("tr", { class: "group line-group" }, el("td", { colspan: "4", text: `Line ${index + 1}${item.page ? ` (page ${item.page})` : ""}` })));
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
    let neoGraph = null;
    let neoError = null;
    try { neoGraph = await (await api(`/api/neo4j/graph?reviewed_only=${reviewed}`)).json(); }
    catch (error) { neoError = error; }
    if (!neoGraph || !graphHasInvoiceLinks(neoGraph)) {
      const localGraph = await (await api(`/api/graph?reviewed_only=${reviewed}`)).json();
      if (graphHasInvoiceLinks(localGraph) || !neoGraph) {
        state.graph = localGraph;
        state.graphSource = "SQLite fallback";
        notify(neoError
          ? `Neo4j is unavailable (${neoError.message}). Showing the local SQLite graph; use Sync Neo4j to rebuild the projection.`
          : "Neo4j has no invoice-to-invoice links, but SQLite does. Showing SQLite links; use Sync Neo4j to rebuild Neo4j.", true);
      } else {
        state.graph = neoGraph;
        state.graphSource = "Neo4j";
      }
    } else {
      state.graph = neoGraph;
      state.graphSource = "Neo4j";
    }
    const { nodes, invoiceHubs } = invoiceHubIndex(state.graph);
    if (!nodes[state.selectedNode]) {
      const invoices = state.graph.nodes.filter((node) => node.type === "Invoice").sort((a, b) => {
        const degree = (invoiceHubs.get(b.id)?.size || 0) - (invoiceHubs.get(a.id)?.size || 0);
        return degree || String(a.props.invoice_number || a.id).localeCompare(String(b.props.invoice_number || b.id));
      });
      state.selectedNode = invoices[0]?.id || null;
    }
    renderGraph();
  } catch (error) {
    state.graph = null;
    $("graph-counts").textContent = "Neo4j and SQLite graphs are unavailable.";
    notify(`Could not load the invoice graph: ${error.message}. Check the review server and graph database.`, true);
  }
}

async function syncNeo4j() {
  const button = $("sync-neo4j"); button.disabled = true;
  try {
    const response = await api("/api/neo4j/sync", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewed_only: $("reviewed-only").checked }),
    });
    const result = await response.json();
    state.graphPage = 0;
    const counts = result.relationships_by_type || {};
    const directLinks = ["SAME_VENDOR_AS", "SAME_CUSTOMER_AS", "SAME_ITEM_DESCRIPTION_AS"]
      .reduce((sum, type) => sum + (counts[type] || 0), 0);
    notify(`Synced ${result.nodes} nodes and ${result.edges} relationships to Neo4j, including ${directLinks} direct invoice links.`);
    await loadGraph();
  } catch (error) {
    notify(`Could not sync the Neo4j graph: ${error.message}`, true);
  } finally { button.disabled = false; }
}

function nodeStatus(node) {
  return node.support.length && node.support.every((s) => s.review_status === "approved" || s.review_status === "corrected") ? "reviewed" : "needs_review";
}

function invoiceHubIndex(graph) {
  const nodes = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const lineItemInvoice = new Map();
  const invoiceHubs = new Map();
  const hubInvoices = new Map();
  const add = (invoiceId, hubId) => {
    if (!nodes[invoiceId] || !nodes[hubId]) return;
    if (!invoiceHubs.has(invoiceId)) invoiceHubs.set(invoiceId, new Set());
    if (!hubInvoices.has(hubId)) hubInvoices.set(hubId, new Set());
    invoiceHubs.get(invoiceId).add(hubId);
    hubInvoices.get(hubId).add(invoiceId);
  };
  graph.edges.filter((e) => e.type === "CONTAINS").forEach((e) => lineItemInvoice.set(e.target, e.source));
  graph.edges.forEach((e) => {
    if (e.type === "ISSUED" && nodes[e.source]?.type === "Vendor") add(e.target, e.source);
    if (e.type === "BILLED_TO" && nodes[e.target]?.type === "Customer") add(e.source, e.target);
    if (e.type === "MATCHES_DESCRIPTION") add(lineItemInvoice.get(e.source), e.target);
  });
  // Only show shared hubs; one-off facts do not create cross-invoice links.
  for (const [hubId, invoices] of hubInvoices) {
    if (invoices.size < 2) {
      hubInvoices.delete(hubId);
      for (const invoiceId of invoices) invoiceHubs.get(invoiceId)?.delete(hubId);
    }
  }
  return { nodes, invoiceHubs, hubInvoices };
}

function graphHasInvoiceLinks(graph) {
  if ((graph.edges || []).some((edge) => ["SAME_VENDOR_AS", "SAME_CUSTOMER_AS", "SAME_ITEM_DESCRIPTION_AS"].includes(edge.type))) return true;
  return [...invoiceHubIndex(graph).hubInvoices.values()].some((invoiceIds) => invoiceIds.size > 1);
}

function invoiceOverviewEdges(graph, invoiceNodes, hubInvoices, nodes) {
  const invoiceIds = new Set(invoiceNodes.map((node) => node.id));
  const types = new Set(["SAME_VENDOR_AS", "SAME_CUSTOMER_AS", "SAME_ITEM_DESCRIPTION_AS"]);
  const direct = (graph.edges || []).filter((edge) => types.has(edge.type) &&
    invoiceIds.has(edge.source) && invoiceIds.has(edge.target));
  if (direct.length) return direct;
  const hubTypes = { Vendor: "SAME_VENDOR_AS", Customer: "SAME_CUSTOMER_AS", ItemDescription: "SAME_ITEM_DESCRIPTION_AS" };
  const derived = [];
  for (const [hubId, linkedInvoices] of hubInvoices) {
    const hub = nodes[hubId], type = hubTypes[hub?.type];
    if (!type) continue;
    const ids = [...linkedInvoices].sort();
    for (let i = 0; i < ids.length; i += 1) for (let j = i + 1; j < ids.length; j += 1) {
      const shared = hub.props.normalized_description || hub.props.name || "shared fact";
      derived.push({ source: ids[i], target: ids[j], type,
        props: type === "SAME_ITEM_DESCRIPTION_AS"
          ? { shared_description: shared } : { shared_name: shared } });
    }
  }
  return derived;
}

function overviewLayout(nodes, width, height) {
  const cellWidth = 180, cellHeight = 82;
  const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length * (height / width) * (cellWidth / cellHeight))));
  const rows = Math.max(1, Math.ceil(nodes.length / columns));
  const viewWidth = columns * cellWidth + 60, viewHeight = rows * cellHeight + 60;
  const positions = Object.fromEntries(nodes.map((node, index) => {
    const column = index % columns, row = Math.floor(index / columns);
    return [node.id, { id: node.id, type: node.type,
      x: 30 + column * cellWidth + cellWidth / 2,
      y: 30 + row * cellHeight + cellHeight / 2, z: 0 }];
  }));
  return { positions, width: viewWidth, height: viewHeight };
}

function renderGraph() {
  const graph = state.graph;
  if (!graph) return;
  const { nodes, invoiceHubs, hubInvoices } = invoiceHubIndex(graph);
  const invoiceNodes = graph.nodes.filter((n) => n.type === "Invoice").sort((a, b) =>
    String(a.props.invoice_number || a.id).localeCompare(String(b.props.invoice_number || b.id)));
  const fullNetwork = $("full-network").checked;
  const pageCount = Math.max(1, Math.ceil(invoiceNodes.length / state.graphPageSize));
  state.graphPage = Math.min(state.graphPage, pageCount - 1);
  const pageInvoices = invoiceNodes.slice(state.graphPage * state.graphPageSize,
    (state.graphPage + 1) * state.graphPageSize);
  const selected = state.selectedNode;
  const selectedNode = nodes[selected];
  const showingConnections = !fullNetwork && ["Invoice", "ItemDescription", "Vendor", "Customer"].includes(selectedNode?.type);
  let selectedHubs = new Set();
  if (selectedNode?.type === "Invoice") selectedHubs = invoiceHubs.get(selected) || new Set();
  else if (["ItemDescription", "Vendor", "Customer"].includes(selectedNode?.type)) selectedHubs = new Set([selected]);
  selectedHubs = new Set([...selectedHubs].filter((hubId) => {
    const type = nodes[hubId]?.type;
    return (type === "ItemDescription" && $("filter-items").checked) ||
      (type === "Vendor" && $("filter-vendors").checked) ||
      (type === "Customer" && $("filter-customers").checked);
  }));

  let visibleInvoiceIds = new Set((fullNetwork ? invoiceNodes : pageInvoices).map((n) => n.id));
  if (showingConnections) {
    visibleInvoiceIds = new Set([selectedNode.type === "Invoice" ? selected : null].filter(Boolean));
    for (const hubId of selectedHubs) {
      for (const invoiceId of hubInvoices.get(hubId) || []) visibleInvoiceIds.add(invoiceId);
    }
  }
  const allConnectedInvoiceCount = visibleInvoiceIds.size;
  const MAX_INVOICES = showingConnections ? 28 : state.graphPageSize;
  const invoiceCandidates = invoiceNodes.filter((n) => visibleInvoiceIds.has(n.id));
  const selectedInvoiceFirst = selectedNode?.type === "Invoice"
    ? invoiceCandidates.find((n) => n.id === selected) : null;
  const visibleInvoices = fullNetwork ? invoiceCandidates : selectedInvoiceFirst
    ? [selectedInvoiceFirst, ...invoiceCandidates.filter((n) => n.id !== selected).slice(0, MAX_INVOICES - 1)]
    : invoiceCandidates.slice(0, MAX_INVOICES);
  const invoiceIds = new Set(visibleInvoices.map((n) => n.id));
  const visibleHubIds = new Set();
  if (!fullNetwork && selectedHubs.size) {
    for (const hubId of selectedHubs) {
      if ([...(hubInvoices.get(hubId) || [])].filter((id) => invoiceIds.has(id)).length > 1) {
        visibleHubIds.add(hubId);
      }
    }
  }
  const visibleHubs = [...visibleHubIds].map((id) => nodes[id]).filter(Boolean);
  const edges = [];
  for (const hub of visibleHubs) {
    for (const invoiceId of hubInvoices.get(hub.id) || []) {
      if (invoiceIds.has(invoiceId)) edges.push({ source: invoiceId, target: hub.id,
        type: `SHARED_${hub.type.toUpperCase()}` });
    }
  }
  const directTypes = new Set(["SAME_VENDOR_AS", "SAME_CUSTOMER_AS", "SAME_ITEM_DESCRIPTION_AS"]);
  const directTypeAllowed = (type) => (type === "SAME_ITEM_DESCRIPTION_AS" && $("filter-items").checked) ||
    (type === "SAME_VENDOR_AS" && $("filter-vendors").checked) ||
    (type === "SAME_CUSTOMER_AS" && $("filter-customers").checked);
  const allInvoiceEdges = fullNetwork ? invoiceOverviewEdges(graph, invoiceNodes, hubInvoices, nodes) : [];
  const directEdges = fullNetwork
    ? allInvoiceEdges.filter((e) => directTypeAllowed(e.type))
    : $("show-direct-links").checked
      ? graph.edges.filter((e) => directTypes.has(e.type) && directTypeAllowed(e.type) && invoiceIds.has(e.source) && invoiceIds.has(e.target))
      : [];
  edges.push(...(fullNetwork ? directEdges : directEdges.slice(0, 160)));
  const itemCount = {};
  graph.edges.filter((e) => e.type === "CONTAINS").forEach((e) => { itemCount[e.source] = (itemCount[e.source] || 0) + 1; });
  const docOf = {};
  graph.edges.filter((e) => e.type === "SUPPORTS").forEach((e) => { docOf[e.target] = nodes[e.source]; });
  const label = (n) => {
    if (n.type === "Invoice") return [n.props.invoice_number || "(no number)",
      `${n.props.invoice_date || "date unknown"}  ·  ${itemCount[n.id] || 0} line items`];
    if (n.type === "ItemDescription") return [n.props.normalized_description || "(description)",
      `Shared item description  ·  ${hubInvoices.get(n.id)?.size || 0} invoices`];
    return [n.props.name || n.id, `${n.type}  ·  ${hubInvoices.get(n.id)?.size || 0} invoices`];
  };
  const viewNodes = [...visibleInvoices, ...visibleHubs];
  const overview = fullNetwork ? overviewLayout(viewNodes, 1400, 820) : null;
  const viewWidth = overview?.width || 1400, viewHeight = overview?.height || 820;
  state.graphViewWidth = viewWidth; state.graphViewHeight = viewHeight;
  const layoutSignature = JSON.stringify([fullNetwork ? "overview" : selected, viewNodes.map((node) => node.id),
    edges.map((edge) => [edge.source, edge.target, edge.type])]);
  const layout = fullNetwork ? overview.positions : layoutSignature === state.graphLayoutSignature && state.graphBaseLayout
    ? state.graphBaseLayout : forceLayout(viewNodes, edges, nodes, selected, viewWidth, viewHeight);
  state.graphLayoutSignature = layoutSignature;
  state.graphBaseLayout = layout;
  state.graphLayoutNodes = viewNodes;
  state.graphLayoutEdges = edges.filter((edge) => layout[edge.source] && layout[edge.target]);
  const position = state.graph3d && !fullNetwork ? projectGraph3d(layout, viewWidth, viewHeight) : layout;
  const root = $("graph-svg");
  root.setAttribute("viewBox", `0 0 ${viewWidth} ${viewHeight}`);
  root.setAttribute("preserveAspectRatio", "xMidYMid meet");
  root.setAttribute("width", String(viewWidth));
  root.setAttribute("height", String(viewHeight));
  root.style.width = fullNetwork ? `${viewWidth}px` : "100%";
  root.style.height = fullNetwork ? `${viewHeight}px` : "100%";
  const visibleEdges = state.graphLayoutEdges;
  const relationTypes = ["SHARED_VENDOR", "SHARED_CUSTOMER", "SHARED_ITEMDESCRIPTION",
    "SAME_VENDOR_AS", "SAME_CUSTOMER_AS", "SAME_ITEM_DESCRIPTION_AS"];
  const children = [
    svg("defs", {}, ...relationTypes.map((type) => svg("marker", {
      id: `arrow-${type}`, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6",
      markerHeight: "6", orient: "auto-start-reverse",
    }, svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: `arrow ${type.toLowerCase()}` })))),
    ...visibleEdges.map((e, edgeIndex) => {
      const a = position[e.source], b = position[e.target];
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy));
      const nx = -dy / distance, ny = dx / distance;
      const bend = e.type === "SAME_VENDOR_AS" ? 30 : e.type === "SAME_CUSTOMER_AS" ? -30
        : e.type === "SAME_ITEM_DESCRIPTION_AS" ? 44 : 0;
      const cx = (a.x + b.x) / 2 + nx * bend, cy = (a.y + b.y) / 2 + ny * bend;
      const on = selected && (e.source === selected || e.target === selected);
      const relation = e.type.toLowerCase();
      const endpoint = (id) => {
        const node = nodes[id];
        return node?.type === "Invoice" ? `Invoice ${node.props.invoice_number || id}`
          : node?.props?.name || node?.props?.normalized_description || node?.type || id;
      };
      const fromNumber = endpoint(e.source), toNumber = endpoint(e.target);
      const shared = e.props?.shared_description || e.props?.shared_name || e.props?.name;
      const reason = e.type === "SAME_VENDOR_AS" ? "same vendor"
        : e.type === "SAME_CUSTOMER_AS" ? "same customer"
          : e.type === "SAME_ITEM_DESCRIPTION_AS" ? "same item description"
            : e.type.replace(/_/g, " ").toLowerCase();
      return svg("path", { class: `edge ${relation}${on ? " on" : ""}`,
        "aria-label": `${fromNumber} linked to ${toNumber} by ${reason}${shared ? `: ${shared}` : ""}`,
        title: `${fromNumber} linked to ${toNumber} by ${reason}${shared ? `: ${shared}` : ""}`,
        "data-graph-edge": edgeIndex,
        ...(!e.type.startsWith("SAME_") ? { "marker-end": `url(#arrow-${e.type})` } : {}),
        opacity: state.graph3d ? Math.max(.38, Math.min(1, ((a.scale || 1) + (b.scale || 1)) / 2)) : null,
        d: `M${a.x},${a.y} Q${cx},${cy} ${b.x},${b.y}` },
      svg("title", { text: `${e.type.replace(/_/g, " ").toLowerCase()}${e.props?.shared_description ? ` · ${e.props.shared_description}` : ""}` }));
    }),
    ...viewNodes.slice().sort((a, b) => (position[a.id].depth || 0) - (position[b.id].depth || 0)).map((n) => {
      const p = position[n.id]; const [line1, line2] = label(n);
      const width = n.type === "Invoice" ? (fullNetwork ? 160 : 224) : 205;
      const height = n.type === "Invoice" ? (fullNetwork ? 50 : 68) : 62;
      const shape = n.type === "Invoice"
        ? svg("rect", { width, height, rx: 10 })
        : n.type === "ItemDescription"
          ? svg("polygon", { points: `18,0 ${width - 18},0 ${width},${height / 2} ${width - 18},${height} 18,${height} 0,${height / 2}` })
          : n.type === "Vendor"
            ? svg("polygon", { points: `12,0 ${width - 12},0 ${width},${height / 2} ${width - 12},${height} 12,${height} 0,${height / 2}` })
            : svg("ellipse", { cx: width / 2, cy: height / 2, rx: width / 2, ry: height / 2 });
      return svg("g", {
        class: `node ${n.type.toLowerCase()} ${nodeStatus(n)}${selected === n.id ? " selected" : ""}`,
        "data-graph-node": n.id,
        transform: `translate(${p.x} ${p.y}) scale(${p.scale || 1}) translate(${-width / 2} ${-height / 2})`,
        opacity: state.graph3d ? Math.max(.62, Math.min(1, .72 + (p.scale || 1) * .28)) : 1,
        tabindex: "0", role: "button", "aria-label": `${n.type} ${line1}`,
        onclick: () => selectNode(n.id), onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectNode(n.id); } },
      }, svg("title", { text: `${line1} · ${line2}` }),
      shape,
      svg("text", { x: 10, y: fullNetwork ? 20 : 25, class: "node-title", text: truncate(line1, n.type === "Invoice" ? 24 : 28) }),
      svg("text", { x: 10, y: fullNetwork ? 38 : 47, class: "node-subtitle", text: truncate(line2, 30) }));
    }),
  ];
  fill(root, svg("g", { id: "graph-content", transform: graphTransform() }, ...children));
  const directCount = directEdges.length;
  $("show-direct-links").closest("label").hidden = fullNetwork;
  $("graph-3d").closest("label").hidden = fullNetwork;
  if (fullNetwork) { state.graph3d = false; $("graph-3d").checked = false; }
  const gestureHelp = $("graph-gesture-help");
  if (gestureHelp) gestureHelp.textContent = fullNetwork
    ? "Full network: scroll to explore every invoice number; line colors show shared vendor, customer, or item links. Select an invoice for details, or turn this off to see shared entity nodes."
    : state.graph3d
      ? "Drag the background to orbit · scroll to zoom · select a node to inspect its neighborhood."
      : "Drag the background to pan · scroll to zoom · select a node to inspect its neighborhood.";
  $("graph-page-label").parentElement.hidden = fullNetwork;
  $("graph-counts").textContent = fullNetwork
    ? `${state.graphSource} · all ${visibleInvoices.length} invoices · ${directCount} invoice relation paths`
    : showingConnections
    ? `${state.graphSource} · ${visibleInvoices.length}${allConnectedInvoiceCount > visibleInvoices.length ? ` of ${allConnectedInvoiceCount}` : ""} invoices · ${visibleHubs.length} shared hubs${$("show-direct-links").checked ? ` · ${directCount} direct links${directEdges.length > 160 ? " (first 160 shown)" : ""}` : ""}`
    : `${state.graphSource} · showing ${visibleInvoices.length} invoices from this page`;
  const sharedHubCount = [...hubInvoices.values()].filter((ids) => ids.size > 1).length;
  const directLinkCount = graph.edges.filter((e) => directTypes.has(e.type)).length;
  const pendingCount = invoiceNodes.filter((n) => !["approved", "corrected"].includes(n.props.review_status)).length;
  fill($("graph-metrics"), metricCard(invoiceNodes.length, "Invoices"),
    metricCard(sharedHubCount, "Shared hubs"), metricCard(directLinkCount, "Invoice links"),
    metricCard(pendingCount, "Need review"));
  $("graph-page-label").textContent = invoiceNodes.length
    ? `Invoices ${state.graphPage * state.graphPageSize + 1}–${Math.min((state.graphPage + 1) * state.graphPageSize, invoiceNodes.length)} of ${invoiceNodes.length} · `
      + `page ${state.graphPage + 1}/${pageCount}` : "No invoices";
  $("graph-prev").disabled = state.graphPage <= 0;
  $("graph-next").disabled = state.graphPage >= pageCount - 1;
  renderSuggestions(graph);
  if (selected && nodes[selected]) renderNodeDetail(nodes[selected], docOf);
}

function setGraphZoom(value) {
  state.graphZoom = Math.max(.55, Math.min(2.6, value));
  const content = $("graph-content");
  if (content) content.setAttribute("transform", graphTransform());
}

function setupGraphInteractions() {
  const canvas = $("graph-svg");
  canvas.addEventListener("wheel", (event) => {
    if ($("full-network").checked) return;
    event.preventDefault();
    setGraphZoom(state.graphZoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });
  canvas.addEventListener("pointerdown", (event) => {
    if (event.target !== canvas) return;
    state.graphDrag = { x: event.clientX, y: event.clientY,
      panX: state.graphPanX, panY: state.graphPanY,
      rotationX: state.graphRotationX, rotationY: state.graphRotationY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.graphDrag) return;
    const box = canvas.getBoundingClientRect();
    const dx = event.clientX - state.graphDrag.x, dy = event.clientY - state.graphDrag.y;
    if (state.graph3d) {
      state.graphRotationY = state.graphDrag.rotationY + dx * .008;
      state.graphRotationX = Math.max(-1.15, Math.min(1.15, state.graphDrag.rotationX + dy * .008));
      updateGraphProjection();
    } else {
      state.graphPanX = state.graphDrag.panX + dx * state.graphViewWidth / Math.max(1, box.width);
      state.graphPanY = state.graphDrag.panY + dy * state.graphViewHeight / Math.max(1, box.height);
      const content = $("graph-content");
      if (content) content.setAttribute("transform", graphTransform());
    }
  });
  const stopDrag = () => { state.graphDrag = null; };
  canvas.addEventListener("pointerup", stopDrag);
  canvas.addEventListener("pointercancel", stopDrag);
}

function updateGraphProjection() {
  if (!state.graphBaseLayout) return;
  const root = $("graph-svg");
  const positions = state.graph3d
    ? projectGraph3d(state.graphBaseLayout, 1400, 820) : state.graphBaseLayout;
  for (const element of root.querySelectorAll("[data-graph-node]")) {
    const node = state.graphLayoutNodes.find((item) => item.id === element.getAttribute("data-graph-node"));
    const point = positions[node?.id];
    if (!node || !point) continue;
    const width = node.type === "Invoice" ? 224 : 205;
    const height = node.type === "Invoice" ? 68 : 62;
    element.setAttribute("transform", `translate(${point.x} ${point.y}) scale(${point.scale || 1}) translate(${-width / 2} ${-height / 2})`);
    element.setAttribute("opacity", state.graph3d ? Math.max(.62, Math.min(1, .72 + (point.scale || 1) * .28)) : 1);
  }
  for (const element of root.querySelectorAll("[data-graph-edge]")) {
    const edge = state.graphLayoutEdges[Number(element.getAttribute("data-graph-edge"))];
    const a = positions[edge?.source], b = positions[edge?.target];
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy));
    const normalX = -dy / distance, normalY = dx / distance;
    const bend = edge.type === "SAME_VENDOR_AS" ? 30 : edge.type === "SAME_CUSTOMER_AS" ? -30
      : edge.type === "SAME_ITEM_DESCRIPTION_AS" ? 44 : 0;
    const cx = (a.x + b.x) / 2 + normalX * bend;
    const cy = (a.y + b.y) / 2 + normalY * bend;
    element.setAttribute("d", `M${a.x},${a.y} Q${cx},${cy} ${b.x},${b.y}`);
    element.setAttribute("opacity", state.graph3d ? Math.max(.38, Math.min(1, ((a.scale || 1) + (b.scale || 1)) / 2)) : 1);
  }
}

function metricCard(value, label) {
  return el("div", { class: "metric-card" }, el("strong", { text: Number(value).toLocaleString() }),
    el("span", { text: label }));
}

function graphTransform() {
  const x = state.graphViewWidth / 2, y = state.graphViewHeight / 2;
  return `translate(${state.graphPanX} ${state.graphPanY}) translate(${x} ${y}) scale(${state.graphZoom}) translate(${-x} ${-y})`;
}

function projectGraph3d(layout, width, height) {
  const cosX = Math.cos(state.graphRotationX), sinX = Math.sin(state.graphRotationX);
  const cosY = Math.cos(state.graphRotationY), sinY = Math.sin(state.graphRotationY);
  const focalLength = 900;
  return Object.fromEntries(Object.entries(layout).map(([id, point]) => {
    const x = point.x - width / 2;
    const y = point.y - height / 2;
    const z = point.z || 0;
    const rotatedX = x * cosY + z * sinY;
    const rotatedZ = -x * sinY + z * cosY;
    const rotatedY = y * cosX - rotatedZ * sinX;
    const depth = y * sinX + rotatedZ * cosX;
    const scale = focalLength / Math.max(360, focalLength - depth) * .84;
    return [id, { ...point, x: width / 2 + rotatedX * scale,
      y: height / 2 + rotatedY * scale, scale, depth }];
  }));
}

function forceLayout(nodes, edges, nodeMap, focusId, width, height) {
  const center = { x: width / 2, y: height / 2 };
  const entries = nodes.map((node, index) => {
    const hash = [...node.id].reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) >>> 0, 7);
    const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2 + (hash % 1000) / 1000;
    const radius = node.type === "Invoice" ? 250 + hash % 100 : 135 + hash % 60;
    return { id: node.id, type: node.type, x: node.id === focusId ? center.x : center.x + Math.cos(angle) * radius,
      y: node.id === focusId ? center.y : center.y + Math.sin(angle) * radius,
      z: node.id === focusId ? 0 : node.type === "Invoice" ? (hash % 300) - 150 : 115 + (hash % 100),
      pinned: node.id === focusId };
  });
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const springs = edges.map((edge) => ({ a: byId.get(edge.source), b: byId.get(edge.target),
    ai: entries.findIndex((entry) => entry.id === edge.source), bi: entries.findIndex((entry) => entry.id === edge.target),
    length: edge.type.startsWith("SAME_") ? 230 : 205 })).filter((edge) => edge.a && edge.b);
  for (let step = 0; step < 72; step += 1) {
    const fx = new Array(entries.length).fill(0), fy = new Array(entries.length).fill(0);
    for (let i = 0; i < entries.length; i += 1) {
      for (let j = i + 1; j < entries.length; j += 1) {
        const dx = entries[j].x - entries[i].x, dy = entries[j].y - entries[i].y;
        const d2 = Math.max(100, dx * dx + dy * dy), force = Math.min(10, 4200 / d2);
        const inv = 1 / Math.sqrt(d2), x = dx * inv * force, y = dy * inv * force;
        fx[i] -= x; fy[i] -= y; fx[j] += x; fy[j] += y;
      }
    }
    for (const edge of springs) {
      const dx = edge.b.x - edge.a.x, dy = edge.b.y - edge.a.y;
      const distance = Math.max(1, Math.hypot(dx, dy)), force = (distance - edge.length) * .014;
      const x = dx / distance * force, y = dy / distance * force;
      const { ai, bi } = edge;
      fx[ai] += x; fy[ai] += y; fx[bi] -= x; fy[bi] -= y;
    }
    const cooling = 1 - step / 90;
    entries.forEach((entry, index) => {
      if (entry.pinned) return;
      fx[index] += (center.x - entry.x) * .002;
      fy[index] += (center.y - entry.y) * .002;
      entry.x = Math.max(115, Math.min(width - 115, entry.x + Math.max(-13, Math.min(13, fx[index])) * cooling));
      entry.y = Math.max(50, Math.min(height - 50, entry.y + Math.max(-13, Math.min(13, fy[index])) * cooling));
    });
  }
  return Object.fromEntries(entries.map((entry) => [entry.id, entry]));
}

const truncate = (text, n) => (String(text).length > n ? `${String(text).slice(0, n - 1)}…` : String(text));

function focusGraphNode(id) {
  const { nodes } = invoiceHubIndex(state.graph);
  const selectedNode = nodes[id];
  let invoiceId = selectedNode?.type === "Invoice" ? id : null;
  if (selectedNode?.type === "LineItem") {
    invoiceId = state.graph.edges.find((edge) => edge.type === "CONTAINS" && edge.target === id)?.source || null;
  }
  state.selectedNode = invoiceId || id;
  const invoiceNodes = state.graph.nodes.filter((n) => n.type === "Invoice").sort((a, b) =>
    String(a.props.invoice_number || a.id).localeCompare(String(b.props.invoice_number || b.id)));
  const invoiceIndex = invoiceNodes.findIndex((n) => n.id === invoiceId);
  if (invoiceIndex >= 0) state.graphPage = Math.floor(invoiceIndex / state.graphPageSize);
  renderGraph();
}

function selectNode(id) { focusGraphNode(id); }

function changeGraphPage(delta) {
  state.graphPage = Math.max(0, state.graphPage + delta);
  state.selectedNode = null;
  renderGraph();
}

function renderNodeDetail(node, docOf) {
  const props = Object.entries(node.props).filter(([k, v]) => k !== "variants" && k !== "match_key" && v !== null && typeof v !== "object");
  const panel = $("node-detail");
  const documentId = node.type === "Document"
    ? (node.props.document_id || node.id.slice("Document:".length))
    : (node.type === "Invoice" && docOf[node.id] ? docOf[node.id].props.document_id : null);
  const invoiceItems = node.type === "Invoice" ? state.graph.edges
    .filter((edge) => edge.type === "CONTAINS" && edge.source === node.id)
    .sort((a, b) => (a.props.row_index ?? 0) - (b.props.row_index ?? 0))
    .map((edge) => state.graph.nodes.find((item) => item.id === edge.target)).filter(Boolean) : [];
  const parties = node.type === "Invoice" ? state.graph.edges.flatMap((edge) => {
    if (edge.type === "ISSUED" && edge.target === node.id) {
      const party = state.graph.nodes.find((item) => item.id === edge.source);
      return party ? [{ relation: "Issued by", party }] : [];
    }
    if (edge.type === "BILLED_TO" && edge.source === node.id) {
      const party = state.graph.nodes.find((item) => item.id === edge.target);
      return party ? [{ relation: "Billed to", party }] : [];
    }
    return [];
  }) : [];
  const { nodes: graphNodes, invoiceHubs, hubInvoices } = invoiceHubIndex(state.graph);
  const sharedHubIds = node.type === "Invoice" ? [...(invoiceHubs.get(node.id) || [])]
    : ["ItemDescription", "Vendor", "Customer"].includes(node.type) ? [node.id] : [];
  const linked = new Map();
  sharedHubIds.forEach((hubId) => {
    for (const invoiceId of hubInvoices.get(hubId) || []) {
      if (invoiceId === node.id) continue;
      if (!linked.has(invoiceId)) linked.set(invoiceId, []);
      linked.get(invoiceId).push(graphNodes[hubId]);
    }
  });
  fill(panel,
    el("h2", { text: `${node.type}` }),
    el("dl", { class: "props" }, ...props.flatMap(([k, v]) => [el("dt", { text: k.replace(/_/g, " ") }), el("dd", { text: String(v) })])),
    parties.length ? el("section", { class: "invoice-relations" },
      el("h2", { text: "Parties and links" }),
      el("ul", {}, ...parties.map(({ relation, party }) => el("li", {},
        el("strong", { text: `${relation}: ${party.props.name || "Unknown"}` }),
        el("span", { text: [party.props.address, party.props.tax_id ? `Tax ID ${party.props.tax_id}` : null].filter(Boolean).join(" · ") || "Party details unavailable" }))))
    ) : null,
    linked.size ? el("section", { class: "invoice-relations" },
      el("h2", { text: `Connected invoices (${linked.size})` }),
      el("ul", {}, ...[...linked.entries()].map(([relatedId, hubs]) => {
        const related = graphNodes[relatedId];
        const title = related ? `Invoice ${related.props.invoice_number || related.id}` : relatedId;
        const via = hubs.map((hub) => hub.type === "ItemDescription"
          ? `item “${hub.props.normalized_description}”` : `${hub.type.toLowerCase()} ${hub.props.name || ""}`).join("; ");
        return el("li", {}, el("button", { type: "button", class: "quiet", text: title,
          onclick: () => focusGraphNode(relatedId) }), el("span", { text: `Via shared ${via}` }));
      }))
    ) : node.type === "Invoice" ? el("p", { class: "empty", text: "No shared vendor, customer, or exact item-description hub connects this invoice yet." }) : null,
    node.props.variants ? el("p", { text: `Spelled ${node.props.variants.length} ways across documents; exact match keys agree.` }) : null,
    el("h2", { text: `Supported by ${node.support.length} document(s)` }),
    el("ul", { class: "support" }, ...node.support.map((s) => el("li", {},
      `${documentName(s.document_id)} v${show(s.version)} `, statusPill(s.review_status)))),
    invoiceItems.length ? el("section", { class: "invoice-item-detail" },
      el("h2", { text: `Line items (${invoiceItems.length})` }),
      el("ul", { class: "invoice-items" }, ...invoiceItems.map((item) => el("li", {},
        el("strong", { text: `Row ${(item.props.row_index ?? 0) + 1}: ${item.props.description || "(no description)"}` }),
        el("span", { text: `Qty ${show(item.props.quantity)} | Net ${show(item.props.net_amount)} | Gross ${show(item.props.gross_amount)}` })))),
    ) : null,
    documentId ? el("button", { type: "button", text: "Open in review", onclick: () => { switchView("review"); selectDocument(documentId); } }) : null,
  );
}

function documentName(id) { const doc = state.docs.find((d) => d.document_id === id); return doc ? doc.file : id.slice(0, 8); }

function renderSuggestions(graph) {
  const items = [
    ...(graph.suggestions || []).map((s) => el("li", { text: `${s.reason === "same_name_different_tax_id" ? "Same name, different tax ID" : "Same tax ID, different name"}: ${s.node_ids.length} ${s.type.toLowerCase()} nodes kept separate.` })),
    ...(graph.duplicates || []).map((d) => el("li", { text: `Identical files: ${d.documents.map((x) => x.source_file).join(", ")}` })),
  ];
  fill($("suggestions"), ...(items.length ? [el("h2", { text: "Possible duplicates to check" }), el("ul", {}, ...items)] : []));
}

function addChatMessage(role, text, sources = [], isError = false) {
  const container = $("chat-messages");
  const message = el("p", { class: `chat-message ${role}${isError ? " error" : ""}`, text });
  const sourceButtons = sources.map((source) => {
    const label = source.invoice_number ? `Invoice ${source.invoice_number}`
      : source.filename || `${source.type} ${source.id}`;
    return el("button", { type: "button", title: `${source.id} · ${source.review_status || "review status unavailable"}`,
      text: `${label} · ${source.review_status || "unreviewed"}`,
      onclick: () => focusGraphNode(source.id) });
  });
  const row = el("div", {}, message, sourceButtons.length ? el("div", { class: "chat-sources" }, ...sourceButtons) : null);
  container.append(row);
  while (container.children.length > 40) container.firstElementChild.remove();
  container.scrollTop = container.scrollHeight;
}

async function askGraph(event) {
  event.preventDefault();
  const input = $("chat-question"), question = input.value.trim();
  if (!question) return;
  input.value = "";
  addChatMessage("user", question);
  const button = $("chat-form").querySelector("button[type=submit]"); button.disabled = true;
  try {
    const response = await api("/api/graph-rag", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, generate: $("chat-use-llm").checked,
        include_unreviewed: !$("chat-reviewed-only").checked, limit: 8 }),
    });
    const result = await response.json();
    const answer = result.context_truncated
      ? `${result.answer}\n\nSome matching connections were omitted by the retrieval limit; treat lists or totals as partial.`
      : result.answer;
    const modeNote = result.answer_mode === "hosted_cached" ? "Reused a cached answer; no extra hosted call."
      : result.answer_mode === "local_budget_fallback" ? "Hosted call budget reached; answered from Neo4j facts locally."
        : result.answer_mode === "hosted" ? "Generated from retrieved graph facts with one hosted call." : "Answered from Neo4j facts locally.";
    addChatMessage("assistant", `${answer}\n\n${modeNote}`, result.sources || []);
    if (result.llm_call_limit !== undefined) {
      $("chat-call-status").textContent = `Hosted calls this hour: ${result.llm_call_limit - result.llm_calls_remaining}/${result.llm_call_limit} used; ${result.llm_calls_remaining} remaining. Repeated questions use cached answers.`;
    }
  } catch (error) {
    addChatMessage("assistant", error.message, [], true);
  } finally { button.disabled = false; input.focus(); }
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
  $("doc-search").addEventListener("input", (event) => {
    state.docSearch = event.target.value;
    renderDocList();
  });
  $("approve").addEventListener("click", () => submitReview("approved"));
  $("edit").addEventListener("click", () => { state.editing = true; renderDetail(); });
  $("cancel").addEventListener("click", () => { state.editing = false; renderDetail(); });
  $("save").addEventListener("click", () => submitReview("corrected"));
  $("prev-page").addEventListener("click", () => { state.page -= 1; loadPage(); });
  $("next-page").addEventListener("click", () => { state.page += 1; loadPage(); });
  $("reviewed-only").addEventListener("change", () => {
    state.graphPage = 0; state.selectedNode = null; loadGraph();
  });
  $("show-direct-links").addEventListener("change", renderGraph);
  $("full-network").addEventListener("change", renderGraph);
  ["filter-items", "filter-vendors", "filter-customers"].forEach((id) => $(id).addEventListener("change", renderGraph));
  $("reload-graph").addEventListener("click", loadGraph);
  $("zoom-in").addEventListener("click", () => setGraphZoom(state.graphZoom * 1.2));
  $("zoom-out").addEventListener("click", () => setGraphZoom(state.graphZoom / 1.2));
  $("fit-graph").addEventListener("click", () => {
    state.graphZoom = 1; state.graphPanX = 0; state.graphPanY = 0;
    state.graphRotationX = -.18; state.graphRotationY = .35; renderGraph();
  });
  $("graph-3d").addEventListener("change", (event) => {
    state.graph3d = event.target.checked;
    const help = $("graph-gesture-help");
    if (help) help.textContent = state.graph3d
      ? "Drag the background to orbit \u00b7 scroll to zoom \u00b7 select a node to inspect its neighborhood."
      : "Drag the background to pan \u00b7 scroll to zoom \u00b7 select a node to inspect its neighborhood.";
    renderGraph();
  });
  setupGraphInteractions();
  $("graph-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = $("graph-search").value.trim().toLocaleLowerCase();
    const graphNodes = state.graph?.nodes || [];
    const searchable = graphNodes.filter((node) => ["Invoice", "Vendor", "Customer", "ItemDescription"].includes(node.type));
    const valueFor = (node) => node.type === "Invoice"
      ? [node.props.invoice_number, node.props.invoice_date].join(" ")
      : node.type === "ItemDescription" ? node.props.normalized_description : node.props.name;
    const match = searchable.find((node) => node.type === "Invoice" &&
      String(node.props.invoice_number || "").toLocaleLowerCase() === query) ||
      searchable.find((node) => String(valueFor(node) || "").toLocaleLowerCase().includes(query));
    if (match) focusGraphNode(match.id);
    else notify(query ? "No invoice, party, or item matched: " + $("graph-search").value.trim() : "Enter an invoice number, party, or item to search.", true);
  });
  $("graph-prev").addEventListener("click", () => changeGraphPage(-1));
  $("graph-next").addEventListener("click", () => changeGraphPage(1));
  $("sync-neo4j").addEventListener("click", syncNeo4j);
  $("chat-form").addEventListener("submit", askGraph);
  document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
    $("chat-question").value = button.dataset.question;
    $("chat-question").focus();
  }));
  window.addEventListener("resize", drawBoxes);
  if (!state.token) { notify("Open the review link printed by the server; it carries the access token.", true); return; }
  loadDocuments();
}

document.addEventListener("DOMContentLoaded", init);
