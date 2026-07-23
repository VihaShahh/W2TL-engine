"use strict";

const state = { demo: null, processed: null };

const elements = {
  form: document.querySelector("#demo-form"),
  generateButton: document.querySelector("#generate-report"),
  resetButton: document.querySelector("#start-over"),
  rowLimit: document.querySelector("#demo-row-limit"),
  runState: document.querySelector("#run-state"),
  statusMessage: document.querySelector("#status-message"),
  walletSection: document.querySelector("#wallet-section"),
  walletGrid: document.querySelector("#wallet-grid"),
  walletTotal: document.querySelector("#wallet-total"),
  processingSection: document.querySelector("#processing-section"),
  runMetrics: document.querySelector("#run-metrics"),
  policyLabel: document.querySelector("#policy-label"),
  exceptionSummary: document.querySelector("#exception-summary"),
  exceptionStage: document.querySelector("#exception-stage"),
  exceptionList: document.querySelector("#exception-list"),
  reportSection: document.querySelector("#form-8949"),
  shortTermRows: document.querySelector("#short-term-rows"),
  shortTermTotals: document.querySelector("#short-term-totals"),
  longTermRows: document.querySelector("#long-term-rows"),
  longTermTotals: document.querySelector("#long-term-totals"),
  incomeRows: document.querySelector("#income-rows"),
  incomeTotals: document.querySelector("#income-totals"),
  taxYear: document.querySelector("#tax-year"),
  fixtureId: document.querySelector("#fixture-id"),
  policyId: document.querySelector("#policy-id"),
  basisMethod: document.querySelector("#basis-method"),
};

function createElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function setRunState(label, tone = "neutral") {
  elements.runState.dataset.tone = tone;
  elements.runState.querySelector("strong").textContent = label;
}

function setStatus(message, isError = false) {
  elements.statusMessage.textContent = message;
  elements.statusMessage.classList.toggle("is-error", isError);
}

function setBusy(busy) {
  elements.generateButton.disabled = busy;
  elements.generateButton.textContent = busy ? "Generating..." : "Generate report";
  elements.generateButton.setAttribute("aria-busy", String(busy));
}

function formatDate(value) {
  if (!value) return "Unresolved";
  const parts = String(value).split("-");
  return parts.length === 3 ? `${parts[1]}/${parts[2]}/${parts[0]}` : String(value);
}

function formatUsd(value) {
  if (value === null || value === undefined || value === "") return "";
  const [whole, fraction = "00"] = String(value).split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = sign ? whole.slice(1) : whole;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}$${grouped}.${fraction.padEnd(2, "0")}`;
}

function humanize(value) {
  if (value === null || value === undefined || value === "") return "Unresolved";
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatRange(range) {
  if (!range.first || !range.last) return "No valid activity";
  return `${formatDate(range.first)} to ${formatDate(range.last)}`;
}

function setStageOptions(values) {
  const current = elements.exceptionStage.value;
  elements.exceptionStage.replaceChildren(createElement("option", "", "All stages"));
  elements.exceptionStage.firstElementChild.value = "";
  values.forEach((value) => {
    const option = createElement("option", "", humanize(value));
    option.value = value;
    elements.exceptionStage.append(option);
  });
  elements.exceptionStage.value = values.includes(current) ? current : "";
}

function walletAlias(walletId) {
  const wallet = state.demo?.wallets.find((item) => item.walletId === walletId);
  return wallet ? wallet.name : walletId;
}

function renderWallets(payload) {
  elements.walletGrid.replaceChildren();
  payload.wallets.forEach((wallet, index) => {
    const card = createElement("article", "wallet-card");
    const header = createElement("header", "wallet-card-header");
    header.append(createElement("div", "wallet-visual", index === 0 ? "PW" : "TW"));
    const heading = createElement("div");
    heading.append(createElement("h3", "", wallet.name));
    heading.append(createElement("span", "wallet-id", wallet.walletId));
    header.append(heading);
    card.append(header);

    const facts = createElement("dl", "wallet-facts");
    [
      ["Records", wallet.recordCount],
      ["Assets", wallet.assets.join(", ") || "Unresolved"],
      ["Activity", formatRange(wallet.activityRange)],
    ].forEach(([label, value]) => {
      const fact = createElement("div");
      fact.append(createElement("dt", "", label));
      fact.append(createElement("dd", "", value));
      facts.append(fact);
    });
    card.append(facts);
    elements.walletGrid.append(card);
  });

  const fixture = payload.fixture;
  elements.walletTotal.textContent = `${fixture.recordCount} records | ${fixture.basisMethod} | ${fixture.valuationCurrency}`;
  elements.walletSection.hidden = false;
}

function appendMetric(label, value, tone) {
  const metric = createElement("div", "metric");
  if (tone) metric.dataset.tone = tone;
  metric.append(createElement("span", "", label));
  metric.append(createElement("strong", "", value));
  elements.runMetrics.append(metric);
}

function appendExceptionItem(container, item) {
  const card = createElement("article", "exception-card");
  const header = createElement("header", "exception-card-header");
  const title = createElement("div");
  title.append(createElement("strong", "", humanize(item.code)));
  title.append(createElement("code", "identifier", item.exception_id));
  header.append(title);
  header.append(createElement("span", "exception-severity", humanize(item.severity)));
  card.append(header);
  card.append(createElement("p", "exception-reason", item.reason));
  card.append(createElement("p", "exception-request", item.human_request));

  const facts = createElement("dl", "exception-facts");
  [
    ["Stage", humanize(item.stage)],
    ["Affected outputs", (item.affected_outputs || []).join(", ")],
    ["Sources", (item.source_refs || []).length],
  ].forEach(([label, value]) => {
    facts.append(createElement("dt", "", label));
    facts.append(createElement("dd", "", value));
  });
  card.append(facts);

  const details = createElement("details", "lineage");
  details.append(createElement("summary", "", "Evidence"));
  (item.observed_facts || []).forEach((fact) => {
    details.append(createElement("p", "", `${humanize(fact.name)}: ${fact.value}`));
  });
  (item.source_refs || []).forEach((source) => {
    details.append(createElement("p", "", `${source.source_id} | ${source.wallet_id} | ${source.signature}`));
  });
  card.append(details);
  container.append(card);
}

function renderExceptions(exceptions = state.processed?.exceptions || []) {
  const selected = exceptions.filter(
    (item) => !elements.exceptionStage.value || item.stage === elements.exceptionStage.value,
  );
  elements.exceptionList.replaceChildren();
  selected.forEach((item) => appendExceptionItem(elements.exceptionList, item));
  if (!selected.length) {
    elements.exceptionList.append(createElement("p", "empty-message", "No exceptions."));
  }
}

function renderProcessing(payload) {
  const run = payload.run;
  const exceptions = payload.exceptions || [];
  elements.runMetrics.replaceChildren();
  appendMetric("Report transactions", run.displayedReportRows);
  appendMetric("Disposals", run.disposalCount);
  appendMetric("Income", run.incomeCount);
  appendMetric("Exceptions", run.exceptionCount, run.exceptionCount > 0 ? "warning" : "");
  elements.policyLabel.textContent = `${run.policyId} v${run.policyVersion}`;
  elements.exceptionSummary.textContent = `Exceptions (${run.exceptionCount})`;
  setStageOptions([...new Set(exceptions.map((item) => item.stage).filter(Boolean))].sort());
  renderExceptions(exceptions);
  elements.processingSection.hidden = false;
}

function appendLineage(cell, row) {
  const details = createElement("details", "lineage");
  details.append(createElement("summary", "", "Source"));
  const list = createElement("dl");
  [
    ["Wallet", `${walletAlias(row.walletId)} (${row.walletId})`],
    ["Source", row.sourceId],
    ["Event", row.eventId],
    ["Lot", row.lotId],
    ["Disposal", row.disposalId],
  ].forEach(([label, value]) => {
    list.append(createElement("dt", "", label));
    list.append(createElement("dd", "", value));
  });
  details.append(list);
  cell.append(details);
}

function appendReportCell(rowElement, label, value, className = "") {
  const cell = createElement("td", className, value);
  cell.dataset.label = label;
  rowElement.append(cell);
  return cell;
}

function appendReportRow(row, container) {
  const rowElement = createElement("tr");
  const description = appendReportCell(rowElement, "Description", row.description);
  appendLineage(description, row);
  appendReportCell(rowElement, "Acquired", formatDate(row.dateAcquired));
  appendReportCell(rowElement, "Disposed", formatDate(row.dateDisposed));
  appendReportCell(rowElement, "Proceeds", formatUsd(row.proceedsUsd), "money");
  appendReportCell(rowElement, "Cost basis", formatUsd(row.costBasisUsd), "money");
  appendReportCell(rowElement, "Code", row.adjustmentCode || "");
  appendReportCell(rowElement, "Adjustment", formatUsd(row.adjustmentAmountUsd), "money");
  appendReportCell(rowElement, "Gain or loss", formatUsd(row.gainLossUsd), "money");
  container.append(rowElement);
}

function appendEmptyRow(container, message, columns) {
  const row = createElement("tr", "empty-row");
  const cell = createElement("td", "", message);
  cell.colSpan = columns;
  row.append(cell);
  container.append(row);
}

function renderTotals(totals, container) {
  container.replaceChildren();
  const row = createElement("tr", "report-total");
  appendReportCell(row, "Totals", "Totals");
  appendReportCell(row, "Acquired", "");
  appendReportCell(row, "Disposed", "");
  appendReportCell(row, "Proceeds", formatUsd(totals.proceedsUsd), "money");
  appendReportCell(row, "Cost basis", formatUsd(totals.costBasisUsd), "money");
  appendReportCell(row, "Code", "");
  appendReportCell(row, "Adjustment", formatUsd(totals.adjustmentAmountUsd), "money");
  appendReportCell(row, "Gain or loss", formatUsd(totals.gainLossUsd), "money");
  container.append(row);
}

function appendIncomeRow(row) {
  const rowElement = createElement("tr");
  const description = appendReportCell(rowElement, "Description", row.description);
  const details = createElement("details", "lineage");
  details.append(createElement("summary", "", "Source"));
  const facts = createElement("dl");
  [
    ["Wallet", `${walletAlias(row.walletId)} (${row.walletId})`],
    ["Source", row.sourceId],
    ["Event", row.eventId],
    ["Income", row.incomeId],
    ["Created lot", row.createdLotId],
  ].forEach(([label, value]) => {
    facts.append(createElement("dt", "", label));
    facts.append(createElement("dd", "", value));
  });
  details.append(facts);
  description.append(details);
  appendReportCell(rowElement, "Received", row.receivedAt);
  appendReportCell(rowElement, "Price", formatUsd(row.priceUsd), "money");
  appendReportCell(rowElement, "Fair value", formatUsd(row.fairValueUsd), "money");
  appendReportCell(rowElement, "Category", humanize(row.category));
  appendReportCell(rowElement, "Source", row.sourceId);
  elements.incomeRows.append(rowElement);
}

function renderIncome(report) {
  elements.incomeRows.replaceChildren();
  elements.incomeTotals.replaceChildren();
  report.income.rows.forEach(appendIncomeRow);
  if (!report.income.rows.length) appendEmptyRow(elements.incomeRows, "No income transactions.", 6);
  const totalRow = createElement("tr", "report-total");
  appendReportCell(totalRow, "Totals", "Income total");
  appendReportCell(totalRow, "Received", "");
  appendReportCell(totalRow, "Price", "");
  appendReportCell(totalRow, "Fair value", formatUsd(report.income.totals.fairValueUsd), "money");
  appendReportCell(totalRow, "Category", "");
  appendReportCell(totalRow, "Source", "");
  elements.incomeTotals.append(totalRow);
}

function renderReport(payload) {
  const { run, report } = payload;
  elements.shortTermRows.replaceChildren();
  elements.longTermRows.replaceChildren();
  report.shortTerm.rows.forEach((row) => appendReportRow(row, elements.shortTermRows));
  report.longTerm.rows.forEach((row) => appendReportRow(row, elements.longTermRows));
  if (!report.shortTerm.rows.length) appendEmptyRow(elements.shortTermRows, "No short-term transactions.", 8);
  if (!report.longTerm.rows.length) appendEmptyRow(elements.longTermRows, "No long-term transactions.", 8);
  renderTotals(report.shortTerm.totals, elements.shortTermTotals);
  renderTotals(report.longTerm.totals, elements.longTermTotals);
  renderIncome(report);
  elements.taxYear.textContent = run.taxYear;
  elements.fixtureId.textContent = run.fixtureSetId;
  elements.policyId.textContent = `${run.policyId} v${run.policyVersion}`;
  elements.basisMethod.textContent = `${run.basisMethod} by ${run.basisScope.replaceAll("_", " ")}`;
  elements.reportSection.hidden = false;
}

async function responseJson(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The report server returned an invalid response (${response.status}).`);
  }
  if (!response.ok) throw new Error(payload.error || `Request failed with status ${response.status}.`);
  return payload;
}

async function generateReport(event) {
  event.preventDefault();
  if (!elements.form.reportValidity()) return;

  setBusy(true);
  setRunState("Generating report", "busy");
  setStatus("");
  elements.reportSection.hidden = true;
  elements.processingSection.hidden = true;
  elements.walletSection.hidden = true;

  try {
    const [demoResponse, processResponse] = await Promise.all([
      fetch("/api/demo?scenario=complete", { headers: { Accept: "application/json" } }),
      fetch("/api/process", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: "complete", rowLimit: Number(elements.rowLimit.value) }),
      }),
    ]);
    state.demo = await responseJson(demoResponse);
    state.processed = await responseJson(processResponse);
    renderReport(state.processed);
    renderProcessing(state.processed);
    renderWallets(state.demo);
    setRunState("Report ready", "success");
    setStatus(`${state.processed.run.displayedReportRows} report transactions generated.`);
  } catch (error) {
    state.demo = null;
    state.processed = null;
    setRunState("Report failed", "error");
    setStatus(error instanceof Error ? error.message : "Report generation failed.", true);
  } finally {
    setBusy(false);
  }
}

function resetReport() {
  state.demo = null;
  state.processed = null;
  elements.walletSection.hidden = true;
  elements.processingSection.hidden = true;
  elements.reportSection.hidden = true;
  setRunState("Ready");
  setStatus("");
  window.scrollTo({ top: 0, behavior: "smooth" });
  elements.rowLimit.focus({ preventScroll: true });
}

elements.form.addEventListener("submit", generateReport);
elements.resetButton.addEventListener("click", resetReport);
elements.exceptionStage.addEventListener("change", () => renderExceptions());

if (window.location.protocol === "file:") {
  setRunState("Server required", "error");
  setStatus("Open the frontend through the W2TL server to generate reports.", true);
} else {
  elements.form.requestSubmit();
}
