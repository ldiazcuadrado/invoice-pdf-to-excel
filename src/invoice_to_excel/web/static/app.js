"use strict";

const state = { files: [], result: { invoices: [], file_issues: [] }, report: null, config: null };
const $ = (id) => document.getElementById(id);

const dropzone = $("dropzone");
const fileInput = $("file-input");

function money(value) {
  if (value === null || value === undefined) return "";
  return Number(value).toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showNotice(message, isError) {
  const box = $("notice");
  box.textContent = message;
  box.className = isError ? "notice error" : "notice";
  box.hidden = !message;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  state.config = await response.json();
  $("limits").textContent =
    `PDF only, up to ${state.config.max_upload_mb} MB per file, ${state.config.max_files} files per batch.`;
  if (state.config.sends_data_to_third_party) {
    showNotice(
      "Privacy: this server is configured to send invoice text to the Anthropic API " +
      "when the built-in rules are not enough."
    );
  }
}

function addFiles(list) {
  const pdfs = Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (pdfs.length < list.length) showNotice("Only PDF files were added.", false);
  if (!pdfs.length) return;
  state.files.push(...pdfs);
  processQueue();
}

let busy = false;

async function processQueue() {
  if (busy) return;
  busy = true;
  $("progress").hidden = false;
  const total = state.files.length;
  let done = 0;
  const batch = state.files.splice(0, state.files.length);
  for (const file of batch) {
    $("progress-text").textContent = `Processing ${file.name} (${done + 1}/${total})`;
    $("progress-bar").max = total;
    $("progress-bar").value = done;
    try {
      const body = new FormData();
      body.append("files", file, file.name);
      const response = await fetch("/api/process", { method: "POST", body });
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.detail || `HTTP ${response.status}`);
      }
      const part = await response.json();
      state.result.invoices.push(...part.invoices);
      state.result.file_issues.push(...part.file_issues);
    } catch (error) {
      state.result.file_issues.push({ filename: file.name, reason: String(error.message || error) });
    }
    done += 1;
  }
  $("progress-bar").value = total;
  $("progress-text").textContent = `Done: ${total} file(s)`;
  await refreshReport();
  busy = false;
}

async function refreshReport() {
  const response = await fetch("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.result),
  });
  if (!response.ok) {
    showNotice("Could not build the report.", true);
    return;
  }
  state.report = await response.json();
  state.result = { invoices: state.report.invoices, file_issues: state.report.file_issues };
  render();
}

function fillTable(table, headers, rows) {
  table.replaceChildren();
  const head = el("tr");
  headers.forEach((h) => head.appendChild(el("th", h)));
  table.appendChild(head);
  rows.forEach((row) => table.appendChild(row));
}

function cell(row, text, className) {
  const td = el("td", text, className);
  row.appendChild(td);
  return td;
}

function rates(invoice) {
  const seen = [];
  invoice.tax_lines.forEach((line) => {
    const label = line.rate === null ? "?" : `${Number(line.rate)}%`;
    if (!seen.includes(label)) seen.push(label);
  });
  return seen.join(" + ");
}

function render() {
  const threshold = state.report.confidence_threshold;
  $("results").hidden = false;

  const fields = [
    ["Supplier", (i) => i.supplier, "supplier"],
    ["Tax ID", (i) => i.tax_id, "tax_id"],
    ["Invoice no.", (i) => i.number, "number"],
    ["Date", (i) => i.invoice_date, "invoice_date"],
    ["Net", (i) => money(i.net_amount), "net_amount", true],
    ["VAT %", rates, "vat_amount"],
    ["VAT", (i) => money(i.vat_amount), "vat_amount", true],
    ["IRPF", (i) => money(i.irpf), null, true],
    ["Total", (i) => money(i.total), "total", true],
    ["Conf.", (i) => `${Math.round(i.overall_confidence * 100)}%`, null, true],
    ["File", (i) => i.filename, null],
    ["Notes", (i) => i.review_reasons.join("; "), null, false, "notes"],
  ];
  const rows = state.report.invoices.map((invoice) => {
    const tr = el("tr", null, invoice.is_ok ? "" : "review");
    fields.forEach(([, get, key, numeric, extra]) => {
      const classes = [numeric ? "num" : "", extra || ""];
      if (key && (invoice.confidence[key] ?? 0) < threshold) classes.push("low");
      cell(tr, get(invoice) ?? "", classes.join(" ").trim());
    });
    return tr;
  });
  fillTable($("invoices-table"), fields.map((f) => f[0]), rows);

  $("issues-block").hidden = state.report.file_issues.length === 0;
  fillTable(
    $("issues-table"),
    ["File", "Reason"],
    state.report.file_issues.map((issue) => {
      const tr = el("tr");
      cell(tr, issue.filename);
      cell(tr, issue.reason, "notes");
      return tr;
    }),
  );

  fillTable(
    $("vat-table"),
    ["Quarter", "VAT %", "Invoices", "To review", "Net", "VAT", "Surcharge"],
    state.report.vat_quarterly.map((row) => {
      const tr = el("tr");
      cell(tr, row.period);
      cell(tr, row.rate === null ? "?" : `${Number(row.rate)}%`);
      cell(tr, row.invoices, "num");
      cell(tr, row.to_review, "num");
      cell(tr, money(row.base), "num");
      cell(tr, money(row.vat), "num");
      cell(tr, money(row.surcharge), "num");
      return tr;
    }),
  );

  fillTable(
    $("suppliers-table"),
    ["Year", "Supplier", "Tax ID", "Invoices", "Total incl. VAT", "Over 3,005.06"],
    state.report.suppliers_347.map((row) => {
      const tr = el("tr", null, row.over_threshold ? "flag" : "");
      cell(tr, row.year ?? "No date");
      cell(tr, row.supplier);
      cell(tr, row.tax_id ?? "");
      cell(tr, row.invoices, "num");
      cell(tr, money(row.gross), "num");
      cell(tr, row.over_threshold ? "YES" : "");
      return tr;
    }),
  );
}

async function download(format) {
  const response = await fetch(`/api/export/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.result),
  });
  if (!response.ok) {
    showNotice("Export failed.", true);
    return;
  }
  const url = URL.createObjectURL(await response.blob());
  const link = el("a");
  link.href = url;
  link.download = `invoices_report.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});
fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});
["dragenter", "dragover"].forEach((name) =>
  dropzone.addEventListener(name, (e) => {
    e.preventDefault();
    dropzone.classList.add("active");
  }),
);
["dragleave", "drop"].forEach((name) =>
  dropzone.addEventListener(name, (e) => {
    e.preventDefault();
    dropzone.classList.remove("active");
  }),
);
dropzone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
$("download-xlsx").addEventListener("click", () => download("xlsx"));
$("download-csv").addEventListener("click", () => download("csv"));
$("reset").addEventListener("click", () => {
  state.result = { invoices: [], file_issues: [] };
  state.report = null;
  $("results").hidden = true;
  $("progress").hidden = true;
});

loadConfig();
