"use strict";

const elements = {
  form: document.querySelector("#solana-form"),
  wallet: document.querySelector("#solana-wallet"),
  endpoint: document.querySelector("#solana-endpoint"),
  pageLimit: document.querySelector("#solana-page-limit"),
  loadButton: document.querySelector("#solana-load"),
  status: document.querySelector("#solana-status"),
  state: document.querySelector("#solana-state"),
  metrics: document.querySelector("#solana-metrics"),
  rows: document.querySelector("#solana-rows"),
  issues: document.querySelector("#solana-issues"),
  exceptionSummary: document.querySelector("#solana-exception-summary"),
  reportMeta: document.querySelector("#solana-report-meta"),
  provenanceFacts: document.querySelector("#solana-provenance-facts"),
};

function createElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function humanize(value) {
  if (value === null || value === undefined || value === "") return "Unresolved";
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function setState(label, tone = "neutral") {
  elements.state.dataset.tone = tone;
  elements.state.querySelector("strong").textContent = label;
}

function setStatus(message, isError = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("is-error", isError);
}

function setBusy(busy) {
  elements.loadButton.disabled = busy;
  elements.loadButton.textContent = busy ? "Loading..." : "Run report";
  elements.loadButton.setAttribute("aria-busy", String(busy));
}

function formatSol(value) {
  if (value === null || value === undefined || value === "") return "Unresolved";
  try {
    const lamports = BigInt(String(value));
    const negative = lamports < 0n;
    const absolute = negative ? -lamports : lamports;
    const whole = absolute / 1000000000n;
    const fraction = String(absolute % 1000000000n).padStart(9, "0").replace(/0+$/, "");
    return `${negative ? "-" : ""}${whole.toLocaleString("en-US")}${fraction ? `.${fraction}` : ""} SOL`;
  } catch {
    return "Unresolved";
  }
}

function appendMetric(label, value, tone) {
  const metric = createElement("div", "metric");
  if (tone) metric.dataset.tone = tone;
  metric.append(createElement("span", "", label));
  metric.append(createElement("strong", "", value));
  elements.metrics.append(metric);
}

function appendCell(row, label, value, className = "") {
  const cell = createElement("td", className, value);
  cell.dataset.label = label;
  row.append(cell);
}

function appendEmptyRow(message) {
  const row = createElement("tr", "empty-row");
  const cell = createElement("td", "", message);
  cell.colSpan = 6;
  row.append(cell);
  elements.rows.append(row);
}

function movementFor(record) {
  const movements = (record.instructions || [])
    .map((instruction) => formatSol(instruction.quantityBaseUnits))
    .filter((value) => value !== "Unresolved");
  return movements.join(", ") || "Unresolved";
}

function renderIssues(issues) {
  elements.issues.replaceChildren();
  issues.forEach((issue) => {
    const item = createElement("article", "exception-card");
    const header = createElement("header", "exception-card-header");
    header.append(createElement("strong", "", humanize(issue.code)));
    header.append(createElement("span", "exception-severity", "Needs attention"));
    item.append(header);
    item.append(createElement("p", "exception-reason", issue.detail));
    if (issue.signature) item.append(createElement("code", "identifier", issue.signature));
    elements.issues.append(item);
  });
  if (!issues.length) elements.issues.append(createElement("p", "empty-message", "No exceptions."));
  elements.exceptionSummary.textContent = `Exceptions (${issues.length})`;
}

function addProvenanceFact(label, value) {
  const fact = createElement("div");
  fact.append(createElement("dt", "", label));
  fact.append(createElement("dd", "", value));
  elements.provenanceFacts.append(fact);
}

function renderProvenance(provenance) {
  elements.provenanceFacts.replaceChildren();
  addProvenanceFact("Wallet", provenance.public_wallet_address);
  addProvenanceFact("Endpoint", provenance.endpoint);
  addProvenanceFact("Ownership", humanize(provenance.ownership_status));
  addProvenanceFact("Pagination", provenance.pagination_complete ? "Complete" : "Partial");
  addProvenanceFact("History", provenance.history_complete ? "Complete" : "Incomplete");
  addProvenanceFact("Oldest signature", provenance.oldest_signature || "Unresolved");
}

function renderObservation(payload) {
  const provenance = payload.provenance;
  const records = payload.records || [];
  const issues = payload.issues || [];
  elements.metrics.replaceChildren();
  appendMetric("Transactions", records.length);
  appendMetric("Exceptions", issues.length, issues.length ? "warning" : "");
  appendMetric("Pages", provenance.pages_retrieved);
  appendMetric("Result", humanize(provenance.stopped_reason));

  elements.rows.replaceChildren();
  records.forEach((record) => {
    const row = createElement("tr");
    appendCell(row, "Observed time", record.timestampUtc);
    appendCell(row, "Status", humanize(record.status));
    appendCell(row, "SOL movement", movementFor(record));
    appendCell(row, "Fee", formatSol(record.feeBaseUnits));
    appendCell(row, "Signature", record.signature, "identifier");
    appendCell(row, "Source", record.sourceId, "identifier");
    elements.rows.append(row);
  });
  if (!records.length) appendEmptyRow("No supported transactions were returned.");

  renderIssues(issues);
  renderProvenance(provenance);
  elements.reportMeta.textContent = `${records.length} of ${provenance.page_limit} requested`;
}

function renderFailure(message) {
  elements.metrics.replaceChildren();
  appendMetric("Transactions", 0);
  appendMetric("Exceptions", 1, "warning");
  appendMetric("Pages", 0);
  appendMetric("Result", "Failed");
  elements.rows.replaceChildren();
  appendEmptyRow("The transaction report could not be generated.");
  renderIssues([{ code: "rpc_request_failed", detail: message, signature: null }]);
  elements.provenanceFacts.replaceChildren();
  addProvenanceFact("Wallet", elements.wallet.value.trim() || "Unresolved");
  addProvenanceFact("Endpoint", elements.endpoint.value.trim() || "Unresolved");
  elements.reportMeta.textContent = "Request failed";
}

async function responseJson(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The RPC server returned an invalid response (${response.status}).`);
  }
  if (!response.ok) throw new Error(payload.error || `Request failed with status ${response.status}.`);
  return payload;
}

async function fetchObservation(event) {
  event.preventDefault();
  if (!elements.form.reportValidity()) return;
  if (window.location.protocol === "file:") {
    const message = "Open the frontend through the W2TL server to run the Solana report.";
    renderFailure(message);
    setState("Server required", "error");
    setStatus(message, true);
    return;
  }
  setBusy(true);
  setState("Loading report", "busy");
  setStatus("");
  try {
    const response = await fetch("/api/public-solana", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        walletAddress: elements.wallet.value.trim(),
        endpoint: elements.endpoint.value.trim(),
        pageLimit: Number(elements.pageLimit.value),
        maxPages: 1,
      }),
    });
    renderObservation(await responseJson(response));
    setState("Report ready", "success");
    setStatus("Public transactions loaded.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Solana request failed.";
    renderFailure(message);
    setState("Report failed", "error");
    setStatus(message, true);
  } finally {
    setBusy(false);
  }
}

elements.form.addEventListener("submit", fetchObservation);
