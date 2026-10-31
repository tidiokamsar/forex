// ASTRA lab: market data, backtests, simulations and logs. The server checks every permission; this
// script only shows the forms to OPERATOR/ADMIN and sends the session CSRF token with each request.
"use strict";

const LAB_ROLE = document.querySelector('meta[name="astra-role"]')?.content || "READ_ONLY";
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
const CAN_ACT = LAB_ROLE === "OPERATOR" || LAB_ROLE === "ADMIN";
const fmt = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const fmt5 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 5 });
const usd = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const NATURE = { REFERENCE: "Taux de référence officiel (quotidien)", HISTORICAL: "Historique importé", SYNTHETIC: "SYNTHÉTIQUE (non réel)" };
const KIND = { REFERENCE_FIX: "Taux de référence (clôture seule)", OHLC: "Bougies OHLC" };
const STATUS = { QUEUED: "en attente", RUNNING: "en cours", DONE: "terminé", FAILED: "échec", OK: "OK", PARTIAL: "partiel",
  REJECTED: "refusé", STOPPED: "arrêtée", SKIPPED: "ignoré" };
const DECISION = { ACHAT: "ACHAT", VENTE: "VENTE", AUCUNE_POSITION: "AUCUNE POSITION" };
let coverage = [];
let selectedBacktest = null;
let selectedSession = null;

function $(id) { return document.getElementById(id); }
function when(value) { return value ? new Date(value).toLocaleString("fr-FR", { timeZone: "UTC" }) : "—"; }
function day(value) { return value ? new Date(value).toLocaleDateString("fr-FR", { timeZone: "UTC" }) : "—"; }
function num(value, digits) { return value == null ? "—" : (digits ? fmt5 : fmt).format(value); }
function money(value) { return value == null ? "—" : usd.format(value); }
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function row(values) {
  const tr = document.createElement("tr");
  for (const value of values) {
    if (value instanceof Node) { const td = document.createElement("td"); td.append(value); tr.append(td); }
    else tr.append(el("td", value == null ? "—" : String(value)));
  }
  return tr;
}
function fill(id, rows, emptyText, columns) {
  const body = $(id);
  if (!rows.length) {
    const td = el("td", emptyText, "empty-cell");
    td.colSpan = columns;
    const tr = document.createElement("tr");
    tr.append(td);
    body.replaceChildren(tr);
  } else body.replaceChildren(...rows);
}
function button(label, handler, className) {
  const node = el("button", label, className || "small-button");
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function status(id, text, isError) {
  const node = $(id);
  node.textContent = text;
  node.classList.toggle("is-error", Boolean(isError));
}

async function api(route, options) {
  const response = await fetch(route, { cache: "no-store", credentials: "same-origin", ...(options || {}) });
  if (response.status === 401) { window.location.assign("/login"); throw new Error("session expirée"); }
  let payload = null;
  try { payload = await response.json(); } catch (_e) { payload = null; }
  if (!response.ok) {
    const messages = { forbidden: "Action réservée au rôle OPERATOR.", csrf_refused: "Session expirée : rechargez la page.",
      request_too_large: "Fichier trop volumineux (12 Mo au plus).", storage_unavailable: "Stockage indisponible.",
      not_found: "Introuvable." };
    throw new Error(payload?.message || messages[payload?.error] || `Erreur ${response.status}`);
  }
  return payload;
}
function post(route, body, isForm) {
  const headers = { "X-CSRF-Token": CSRF };
  if (!isForm) headers["Content-Type"] = "application/json";
  return api(route, { method: "POST", headers, body: isForm ? body : JSON.stringify(body || {}) });
}

// -- SVG line chart (no library, no inline script: allowed by the CSP) ---------------------------------
function lineChart(container, points, label) {
  container.replaceChildren();
  if (points.length < 2) { container.append(el("p", "Pas assez de points pour un graphique.", "empty-cell")); return; }
  const NS = "http://www.w3.org/2000/svg";
  const width = 900, height = 260, pad = 46;
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x) => pad + (x - x0) / ((x1 - x0) || 1) * (width - 2 * pad);
  const sy = (y) => height - pad + 10 - (y - y0) / ((y1 - y0) || 1) * (height - 2 * pad);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const path = document.createElementNS(NS, "polyline");
  path.setAttribute("points", points.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" "));
  path.setAttribute("class", "chart-line");
  for (const [value, y] of [[y1, sy(y1)], [y0, sy(y0)]]) {
    const text = document.createElementNS(NS, "text");
    text.setAttribute("x", "4"); text.setAttribute("y", String(y + 4)); text.setAttribute("class", "chart-axis");
    text.textContent = fmt5.format(value);
    svg.append(text);
  }
  for (const [value, anchor] of [[x0, "start"], [x1, "end"]]) {
    const text = document.createElementNS(NS, "text");
    text.setAttribute("x", String(sx(value))); text.setAttribute("y", String(height - 4));
    text.setAttribute("text-anchor", anchor); text.setAttribute("class", "chart-axis");
    text.textContent = day(new Date(value).toISOString());
    svg.append(text);
  }
  svg.append(path);
  container.append(svg);
}

// -- tabs ------------------------------------------------------------------------------------------
function showTab() {
  const name = (window.location.hash || "#tableau").slice(1);
  const known = ["tableau", "apercu", "donnees", "backtests", "simulation", "apprentissage", "demo", "journaux"];
  const active = known.includes(name) ? name : "tableau";
  for (const view of known) $(`view-${view}`).hidden = view !== active;
  for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("is-active", tab.dataset.tab === active);
}

// -- data ------------------------------------------------------------------------------------------
function seriesKey(c) { return `${c.symbol}|${c.timeframe}|${c.source}`; }
function seriesLabel(c) { return `${c.symbol} · ${c.timeframe} · ${c.source} (${NATURE[c.nature] || c.nature}, ${num(c.candles)} bougies)`; }

function fillSelects(payload) {
  for (const select of document.querySelectorAll(".pair-select")) {
    if (!select.options.length) select.replaceChildren(...payload.pairs.map((p) => new Option(p, p)));
  }
  for (const select of document.querySelectorAll(".tf-select")) {
    if (!select.options.length) select.replaceChildren(...payload.timeframes.map((t) => new Option(t, t)));
  }
  const series = document.querySelector(".series-select");
  const current = series.value;
  series.replaceChildren(...(coverage.length ? coverage.map((c) => new Option(seriesLabel(c), seriesKey(c)))
    : [new Option("Aucune donnée : importez ou mettez à jour les taux BCE", "")]));
  if (current) series.value = current;
  const groups = [...new Set(coverage.map((c) => `${c.source}|${c.timeframe}`))];
  const sourceTf = document.querySelector(".source-tf-select");
  const chosen = sourceTf.value;
  sourceTf.replaceChildren(...(groups.length ? groups.map((g) => new Option(g.replace("|", " · "), g))
    : [new Option("Aucune donnée disponible", "")]));
  if (chosen) sourceTf.value = chosen;
  fillSimPairs();
}

function fillSimPairs() {
  const [source, timeframe] = (document.querySelector(".source-tf-select").value || "|").split("|");
  const pairs = coverage.filter((c) => c.source === source && c.timeframe === timeframe).map((c) => c.symbol);
  const box = $("sim-pairs");
  const checked = new Set([...box.querySelectorAll("input:checked")].map((i) => i.value));
  box.replaceChildren(...pairs.map((pair) => {
    const label = el("label", null, "check-label");
    const input = document.createElement("input");
    input.type = "checkbox"; input.name = "symbols"; input.value = pair;
    input.checked = checked.size ? checked.has(pair) : pair === "EURUSD";
    label.append(input, document.createTextNode(` ${pair}`));
    return label;
  }));
  if (!pairs.length) box.append(el("span", "Aucune paire pour cette source.", "muted"));
}

async function showChart(c) {
  $("chart-title").textContent = `${c.symbol} · ${c.timeframe} · ${c.source} — clôtures (${NATURE[c.nature] || c.nature})`;
  const data = await api(`/api/market/candles?symbol=${c.symbol}&timeframe=${c.timeframe}&source=${encodeURIComponent(c.source)}&limit=500`);
  lineChart($("price-chart"), data.candles.map((k) => ({ x: Date.parse(k.ts), y: k.close })), `Clôtures ${c.symbol}`);
}

async function showQuality(c) {
  status("quality-status", "Contrôle en cours…");
  try {
    const q = await api(`/api/market/quality?symbol=${c.symbol}&timeframe=${c.timeframe}&source=${encodeURIComponent(c.source)}`);
    const last = q.gap_examples.slice(-3).map((g) => `${g.missing} manquante(s) entre ${day(g.after)} et ${day(g.before)}`).join(" ; ");
    status("quality-status", `${c.symbol} ${c.timeframe} ${c.source} : ${num(q.candles)} bougies, ${q.gaps} lacune(s) hors fermeture du week-end`
      + ` (${num(q.missing_bars)} bougie(s) manquante(s))${last ? ` — dernières : ${last}` : ""}.`, q.gaps > 0);
  } catch (error) { status("quality-status", error.message, true); }
}

function renderData(payload) {
  coverage = payload.coverage;
  const live = payload.live_feed;
  $("live-state").textContent = live.connected ? "Temps réel : connecté" : "Temps réel : non connecté";
  $("live-state").title = live.reason;
  fill("sources-body", payload.sources.map((s) => row([s.name, NATURE[s.nature] || s.nature, s.freshness.label,
    when(s.last_data_ts), when(s.last_success_at), s.last_error ? `${when(s.last_error_at)} · ${s.last_error}` : "aucune"])),
  "Aucune source encore utilisée. « Mettre à jour les taux BCE » lance la première acquisition.", 6);
  $("coverage-total").textContent = `${num(payload.total_candles)} bougies`;
  fill("coverage-body", coverage.map((c) => {
    const actions = el("span", null, "button-row");
    actions.append(button("Voir", () => showChart(c).catch((e) => status("quality-status", e.message, true))),
      button("Qualité", () => showQuality(c)));
    return row([c.symbol, c.timeframe, c.source, `${KIND[c.kind] || c.kind} · ${NATURE[c.nature] || c.nature}`, num(c.candles),
      day(c.first), when(c.last), c.with_volume ? `${num(c.with_volume)} avec volume` : "absent", actions]);
  }), "Aucune donnée stockée. Mettez à jour les taux BCE ou importez un fichier CSV.", 9);
  fill("imports-body", payload.imports.map((i) => {
    const r = i.report || {};
    const issues = (i.issues || []).slice(0, 3).map((x) => `${x.code}${x.row ? ` l.${x.row}` : ""}`).join(", ");
    return row([when(i.started_at), i.source, i.symbol || "toutes", STATUS[i.status] || i.status, num(r.rows_read ?? r.accepted),
      num(r.accepted), num(r.inserted), num(r.rejected), num(r.gaps), r.error || issues || "aucune"]);
  }), "Aucun import.", 10);
  fillSelects(payload);
  const real = coverage.filter((c) => c.nature !== "SYNTHETIC");
  const synthetic = coverage.filter((c) => c.nature === "SYNTHETIC");
  $("data-label-value").textContent = real.length ? `${[...new Set(real.map((c) => NATURE[c.nature]))].join(", ")}`
    : (synthetic.length ? "SYNTHÉTIQUES uniquement" : "aucune donnée");
  $("data-label-detail").textContent = real.length
    ? `${num(real.reduce((a, c) => a + c.candles, 0))} bougies réelles sur ${new Set(real.map((c) => c.symbol)).size} paires · pas du temps réel`
    : "aucune donnée de marché réelle stockée";
  $("ov-candles").textContent = num(payload.total_candles);
  $("ov-sources").textContent = payload.sources.length
    ? payload.sources.map((s) => `${s.name} : ${s.freshness.label}`).join(" · ") : "Aucune source";
  const w = payload.worker;
  $("ov-worker").textContent = w.state === "OK" ? "Actif" : (w.state === "NEVER_SEEN" ? "Jamais vu" : "Silencieux");
  $("ov-worker-detail").textContent = w.last_heartbeat ? `dernier signe de vie ${when(w.last_heartbeat)} UTC` : w.label;
  $("worker-state").textContent = `Worker : ${w.label}`;
}

function periodText(b) {
  return `${b.period_start ? day(b.period_start) : "début des données"} → ${b.period_end ? day(b.period_end) : "fin des données"}`;
}

// -- backtests --------------------------------------------------------------------------------------
function renderBacktests(list) {
  fill("backtests-body", list.map((b) => {
    const m = b.metrics || {};
    return row([b.id, when(b.created_at), `${b.symbol} ${b.timeframe} ${b.source}`,
      periodText(b), b.status === "FAILED" ? `échec : ${b.error}` : (STATUS[b.status] || b.status),
      num(m.trade_count), money(m.net_pnl), m.max_drawdown_pct == null ? "—" : `${num(m.max_drawdown_pct)} %`,
      b.status === "DONE" ? button("Détails", () => showBacktest(b.id)) : ""]);
  }), "Aucun backtest. Choisissez une série et lancez-en un.", 9);
  const done = list.find((b) => b.status === "DONE");
  if (done) {
    const m = done.metrics;
    $("ov-bt-net").textContent = money(m.net_pnl);
    $("ov-bt-detail").textContent = `${done.symbol} ${done.timeframe} · ${num(m.trade_count)} trades · coûts ${money(m.total_costs)}`;
    const list2 = $("ov-bt-list");
    list2.replaceChildren(...[["Série", `${done.symbol} · ${done.timeframe} · ${done.source}`],
      ["Stratégie", `${done.strategy} ${done.strategy_version} (expérimentale)`],
      ["Période", periodText(done)],
      ["Capital", `${money(m.initial_capital)} → ${money(m.final_capital)}`],
      ["Brut / coûts / net", `${money(m.gross_pnl)} / ${money(m.total_costs)} / ${money(m.net_pnl)}`],
      ["Taux de réussite", m.win_rate_pct == null ? "non défini" : `${num(m.win_rate_pct)} %`],
      ["Profit factor", m.profit_factor == null ? "non défini" : num(m.profit_factor)],
      ["Drawdown maximal", `${num(m.max_drawdown_pct)} %`]].map(([k, v]) => {
      const d = document.createElement("div"); d.append(el("dt", k), el("dd", v)); return d;
    }));
  }
  return list.some((b) => b.status === "QUEUED" || b.status === "RUNNING");
}

async function showBacktest(id) {
  const b = await api(`/api/lab/backtests/${id}`);
  selectedBacktest = id;
  $("backtest-detail").hidden = false;
  $("bt-title").textContent = `Backtest n° ${b.id} · ${b.symbol} ${b.timeframe} · ${b.source}`;
  const m = b.metrics;
  const cards = [["Capital initial → final", `${money(m.initial_capital)} → ${money(m.final_capital)}`],
    ["Résultat brut / net", `${money(m.gross_pnl)} / ${money(m.net_pnl)}`], ["Coûts simulés", money(m.total_costs)],
    ["Transactions", `${num(m.trade_count)} (${num(m.winning_trades)} gagnantes)`],
    ["Taux de réussite", m.win_rate_pct == null ? "non défini" : `${num(m.win_rate_pct)} %`],
    ["Profit factor", m.profit_factor == null ? "non calculable" : num(m.profit_factor)],
    ["Drawdown maximal", `${num(m.max_drawdown_pct)} %`],
    ["Gain moyen / perte moyenne", `${money(m.average_win)} / ${money(m.average_loss)}`]];
  $("bt-metrics").replaceChildren(...cards.map(([k, v]) => {
    const a = el("article", null, "metric-card"); a.append(el("span", k, "metric-label"), el("strong", v)); return a;
  }));
  lineChart($("bt-equity"), (b.equity || []).map(([ts, v]) => ({ x: Date.parse(ts), y: v })), "Courbe du capital");
  $("bt-assumptions").replaceChildren(...(b.assumptions || []).map((a) => el("li", a)));
  const d = b.data || {};
  $("bt-data").replaceChildren(...[["Bougies", num(d.candles)], ["Préchauffage", num(d.warmup_candles)],
    ["Type", (d.kinds || []).map((k) => KIND[k] || k).join(", ")], ["Lacunes", `${num(d.gaps)} (${num(d.missing_bars)} manquantes)`],
    ["Première / dernière", `${when(d.first)} → ${when(d.last)}`], ["Empreinte SHA-256", (d.fingerprint_sha256 || "").slice(0, 16) + "…"],
    ["Paramètres", JSON.stringify({ ...b.params.strategy_params, ...b.params.risk, ...b.params.costs })]].map(([k, v]) => {
    const x = document.createElement("div"); x.append(el("dt", k), el("dd", v)); return x;
  }));
  fill("bt-trades", (b.trades || []).slice(-200).reverse().map((t) => row([when(t.entry_ts), when(t.exit_ts), t.side, num(t.units),
    num(t.entry_price, true), num(t.exit_price, true), money(t.gross_pnl), money(t.costs), money(t.net_pnl), t.exit_reason])),
  "Aucune transaction : la stratégie n'a ouvert aucune position sur cette période.", 10);
  fill("bt-signals", (b.signals || []).slice(-200).reverse().map((s) => row([when(s.ts), DECISION[s.decision] || s.decision,
    num(s.price, true), Object.entries(s.indicators).map(([k, v]) => `${k} ${v}`).join(" · "), s.reason])), "Aucun signal.", 5);
  fill("bt-refused", (b.orders || []).filter((o) => o.status === "REFUSE").slice(-100).reverse().map((o) => row([when(o.ts), o.side, o.reason])),
    "Aucun ordre refusé.", 3);
  $("backtest-detail").scrollIntoView({ behavior: "smooth" });
}

// -- simulation -------------------------------------------------------------------------------------
function limitsText(a) {
  const parts = [];
  if (a.drawdown_halt) parts.push("drawdown max atteint");
  if (a.daily_halt) parts.push("perte journalière atteinte");
  return parts.join(", ") || "aucune atteinte";
}

function renderSessions(payload) {
  const sessions = payload.sessions;
  fill("sessions-body", sessions.map((s) => row([s.id, s.name, s.symbols.join(", "),
    s.last_error ? `${STATUS[s.status] || s.status} · erreur` : (s.status === "RUNNING" ? "en cours" : STATUS[s.status] || s.status),
    money(s.account.equity), money(s.account.net_pnl), Object.keys(s.account.positions).length, limitsText(s.account),
    when(s.updated_at), button("Détails", () => showSession(s.id))])), "Aucune simulation. Choisissez des paires et démarrez-en une.", 10);
  const running = sessions.filter((s) => s.status === "RUNNING");
  $("ov-sim").textContent = String(running.length);
  $("ov-sim-detail").textContent = sessions.length
    ? `${sessions.length} au total · capital ${running.map((s) => money(s.account.equity)).join(", ") || "—"}` : "Aucune simulation";
}

async function showSession(id) {
  const s = await api(`/api/sim/sessions/${id}`);
  selectedSession = id;
  $("sim-detail").hidden = false;
  $("sim-title").textContent = `Simulation n° ${s.id} · ${s.name}`;
  const buttons = $("sim-buttons");
  buttons.replaceChildren(s.status === "RUNNING"
    ? button("Arrêter", () => simAction(id, "stop"), "danger-button")
    : button("Relancer", () => simAction(id, "start")));
  const a = s.account;
  $("sim-metrics").replaceChildren(...[["Statut", s.status === "RUNNING" ? "en cours" : STATUS[s.status] || s.status],
    ["Capital (marqué au marché)", money(a.equity)], ["Solde réalisé", money(a.cash)], ["Résultat net", money(a.net_pnl)],
    ["Limites", limitsText(a)], ["Source", `${s.source} · ${s.timeframe}`],
    ["Dernières bougies traitées", Object.entries(s.last_processed).map(([k, v]) => `${k} ${when(v)}`).join(" · ") || "aucune"],
    ["Données", NATURE[s.nature] || s.nature || "inconnue"], ["Remarque", s.last_error || "aucune"]].map(([k, v]) => {
    const c = el("article", null, "metric-card"); c.append(el("span", k, "metric-label"), el("strong", v)); return c;
  }));
  lineChart($("sim-equity"), (s.equity || []).map((p) => ({ x: Date.parse(p.ts), y: p.equity })), "Capital de la simulation");
  fill("sim-positions", Object.entries(a.positions).map(([sym, p]) => row([sym, p.side, num(p.units), when(p.entry_ts),
    num(p.entry_ref, true), num(p.stop, true)])), "Aucune position ouverte.", 6);
  fill("sim-orders", s.orders.slice().reverse().map((o) => row([when(o.ts), o.symbol, o.kind, o.side, num(o.units),
    o.status === "EXECUTE" ? "exécuté (simulé)" : "refusé", num(o.fill_price ?? o.reference_price, true), o.reason])),
  "Aucun ordre simulé pour l'instant : le worker attend de nouvelles bougies.", 8);
  fill("sim-trades", s.trades.slice().reverse().map((t) => row([t.symbol, when(t.entry_ts), when(t.exit_ts), t.side,
    money(t.net_pnl), t.exit_reason])), "Aucune transaction clôturée.", 6);
  fill("sim-signals", s.signals.slice().reverse().map((x) => row([when(x.ts), x.symbol, DECISION[x.decision] || x.decision,
    num(x.price, true), x.reason])), "Aucun signal (préchauffage des indicateurs ou pas encore de bougie).", 5);
  $("sim-events").replaceChildren(...s.events.slice().reverse().map((e) => el("li", `${when(e.ts)} · ${e.kind} · ${e.message}`)));
}

async function simAction(id, action) {
  try {
    await post(`/api/sim/sessions/${id}/${action}`);
    await refreshSessions();
    await showSession(id);
  } catch (error) { status("sim-status", error.message, true); }
}

// -- journals ---------------------------------------------------------------------------------------
function renderJobs(payload) {
  fill("jobs-body", payload.jobs.map((j) => {
    let outcome = j.error || "";
    if (!outcome && j.result) {
      const r = j.result;
      outcome = r.inserted != null ? `${num(r.accepted)} acceptées, ${num(r.inserted)} nouvelles${r.rejected ? `, ${num(r.rejected)} refusées` : ""}`
        : (r.net_pnl != null ? `net ${money(r.net_pnl)}, ${num(r.trade_count)} trades` : (r.status || "terminé"));
    }
    return row([j.id, j.kind, STATUS[j.status] || j.status, j.actor, when(j.created_at), when(j.finished_at), outcome || "—"]);
  }), "Aucune tâche.", 7);
  $("worker-state").textContent = `Worker : ${payload.worker.label}`;
}

// -- learning -----------------------------------------------------------------------------------------
function paramsText(p) {
  if (!p) return "—";
  const s = p.strategy || {};
  const family = { momentum: "Momentum", mean_reversion: `Retour à la moyenne (z ${s.z_entry})`, breakout: "Cassure de canal" }[s.family] || "Tendance SMA";
  return `${family} · ${s.fast}/${s.slow} · ATR ${s.atr_window} · stop ${p.stop_atr_multiple} ATR · ATR min ${num((s.min_atr_pct || 0) * 100)} %`;
}

function renderLearning(d) {
  fill("learn-pairs", d.pairs.map((p) => row([p.symbol, `v${p.champion.version}`, paramsText(p.champion.params), p.champion.reason,
    num(p.trials), when(p.last_run_at),
    p.session ? `${money(p.session.account.equity)} (${p.session.account.net_pnl >= 0 ? "+" : ""}${money(p.session.account.net_pnl)})` : "—",
    p.session ? num(p.session.trades_total) : "—"])), "Aucune paire avec des données BCE.", 8);
  fill("learn-runs", d.runs.map((r) => {
    const ev = r.evidence || {};
    const champ = ev.champion_selection || {};
    const best = (ev.best || {}).selection || {};
    const check = ev.final_check_best ? `${money(ev.final_check_best.net_pnl)} / ${money((ev.final_check_champion || {}).net_pnl)}` : "—";
    const decision = r.status === "FAILED" ? `échec : ${r.error}` : (r.decision === "PROMOTED" ? "NOUVEAU CHAMPION" : "champion conservé");
    return row([when(r.started_at), r.symbol, num((r.candidates || []).length), champ.score == null ? "—" : num(champ.score),
      best.score == null ? "—" : num(best.score), check, decision, ev.reason || "—"]);
  }), "Aucune évaluation encore : la première a lieu dans les minutes qui suivent le démarrage du worker.", 8);
  const m = d.method;
  $("learn-warning").textContent = `Méthode : ${m.selection} ; ${m.candidates_per_run} candidats par évaluation ; contrôle final sur les ${m.final_check_days} derniers jours ; gain minimal ${m.min_score_gain} point ; drawdown maximal ${m.max_drawdown_pct} % ; au moins ${m.min_trades} transactions. ${m.warning}.`;
}

// -- DEMO account -----------------------------------------------------------------------------------
function renderDemo(d) {
  const st = d.status;
  const beat = d.heartbeat && d.heartbeat.at;
  const alive = beat && Date.now() - Date.parse(beat) < 90000;
  let label = "Service DEMO jamais démarré";
  if (st) label = st.connected ? (alive ? "● CONNECTÉ (DEMO)" : "Service DEMO silencieux") : `DÉCONNECTÉ : ${st.error || "erreur"}`;
  $("demo-state").textContent = label;
  const cards = !st ? [["Connexion", "aucune", "Clé DEMO non déposée ou service astra-demo arrêté"]] : [
    ["Connexion", st.connected ? "connecté" : "déconnecté", `${when(st.checked_at)} UTC`],
    ["Environnement", `${st.environment_reported || "—"} (${st.environment_source || "—"})`, st.account_ref || "—"],
    ["Prix BTCUSDT", st.quote ? `${num(st.quote.bid, true)} / ${num(st.quote.ask, true)}` : "—",
      st.quote ? `achat / vente · ${when(st.quote.at)} UTC · Binance Demo, réaliste mais pas réel` : ""],
    ["Soldes virtuels", Object.entries(st.balances || {}).map(([k, v]) => `${k} ${num(Number(v.free), true)}`).join(" · ") || "—",
      "solde du compte de démonstration"],
    ["Exécution", d.paused ? "EN PAUSE" : "active", st.demo_gate_open ? "registre : ordres DEMO autorisés" : "registre : ordres DEMO pas encore autorisés"],
    ["Règles BTCUSDT", st.rules ? `pas ${st.rules.step_size} · minimum ${st.rules.min_notional} USDT` : "—", st.rules ? `statut ${st.rules.status}` : ""],
    ["Position ASTRA", (st.positions || []).map((p) => `${p.symbol} ${num(p.net_units, true)}`).join(" · ") || "aucune",
      "quantité nette des ordres ASTRA"],
    ["Limites", st.policy ? `allocation ${num(st.policy.allocation)} USDT · risque ${num(st.policy.risk_per_trade * 100)} %` : "—",
      st.policy ? `perte du jour ${num(st.policy.daily_loss_limit * 100)} % · drawdown ${num(st.policy.max_drawdown * 100)} %` : ""],
  ];
  $("demo-cards").replaceChildren(...cards.map(([k, v, small]) => {
    const a = el("article", null, "metric-card"); a.append(el("span", k, "metric-label"), el("strong", v), el("small", small)); return a;
  }));
  lineChart($("demo-chart"), (d.quotes || []).map((q) => ({ x: Date.parse(q.ts), y: (q.bid + q.ask) / 2 })), "Prix BTCUSDT Binance Demo");
  const stepText = (s) => {
    if (!s) return "—";
    let text = String(s.state);
    if (s.broker_order_id) text += ` · n° ${s.broker_order_id}`;
    if (s.filled_units) text += ` · ${num(s.filled_units, true)} @ ${num(s.average_price, true)}`;
    return text;
  };
  fill("demo-tests", (d.tests || []).map((t) => {
    const steps = Object.fromEntries((t.details.steps || []).map((s) => [s.step, s]));
    const recon = (steps.reconciliation_after_sell || steps.reconciliation_before || {}).status || "—";
    return row([t.id, when(t.started_at), t.actor, t.status, stepText(steps.buy), stepText(steps.sell), recon, t.details.error || "—"]);
  }), "Aucun ordre de test. Reprenez l’exécution DEMO puis lancez « Ordre de test DEMO ».", 8);
  if (d.orders) {
    fill("demo-orders", d.orders.map((o) => row([when(o.created_at), o.client_order_id, o.purpose, o.direction,
      num(o.requested_units, true), num(o.filled_units, true), num(o.average_fill_price, true), o.state,
      (o.reason_codes || []).join(", ") || "—"])), "Aucun ordre DEMO.", 9);
  }
  fill("demo-requests", (d.requests || []).map((r) => row([r.id, r.kind, r.actor, when(r.created_at), STATUS[r.status] || r.status,
    r.result ? (r.result.error || r.result.result || JSON.stringify(r.result)) : "—"])), "Aucune demande.", 6);
}

async function demoAction(action, label) {
  status("demo-status-line", `${label} : demande envoyée…`);
  try {
    const r = await post(`/api/demo/${action}`);
    const seen = r.request.duplicate ? "déjà en attente" : "enregistrée";
    status("demo-status-line", `${label} : demande n° ${r.request.id} ${seen} ; le service DEMO la traite sous 5 secondes.`);
    setTimeout(refreshDemo, 6000);
    if (action === "controlled-test") setTimeout(refreshDemo, 20000);
  } catch (error) { status("demo-status-line", error.message, true); }
}

// -- refresh ----------------------------------------------------------------------------------------
async function refreshData() { renderData(await api("/api/market/overview")); }
async function refreshBacktests() { return renderBacktests((await api("/api/lab/backtests")).backtests); }
async function refreshSessions() { renderSessions(await api("/api/sim/sessions")); }
async function refreshJobs() { renderJobs(await api("/api/system/jobs")); }
async function refreshDemo() { renderDemo(await api("/api/demo/overview")); }
async function refreshLearning() { renderLearning(await api("/api/learn/overview")); }

async function refreshAll() {
  const results = await Promise.allSettled([refreshData(), refreshBacktests(), refreshSessions(), refreshJobs(), refreshDemo(), refreshLearning()]);
  if (selectedSession) showSession(selectedSession).catch(() => {});
  return results[1].status === "fulfilled" && results[1].value;
}

function numberValue(form, name) {
  const value = form.elements[name]?.value;
  return value === "" || value == null ? undefined : Number(value);
}

function wireForms() {
  $("btn-ingest").addEventListener("click", async () => {
    status("ingest-status", "Demande envoyée au worker…");
    try {
      const r = await post("/api/market/ingest");
      status("ingest-status", `Tâche n° ${r.job.id} ${r.job.duplicate ? "déjà en file" : "en file"} : le worker interroge la BCE (quelques secondes).`);
      setTimeout(refreshAll, 8000);
    } catch (error) { status("ingest-status", error.message, true); }
  });
  $("import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    status("import-status", "Vérification du fichier…");
    try {
      const r = await post("/api/market/import", new FormData(event.target), true);
      const p = r.preview;
      status("import-status", `Fichier vérifié : ${num(p.accepted)} bougies valides sur ${num(p.rows_read)}, ${num(p.rejected)} refusées,`
        + ` ${num(p.gaps)} lacune(s), du ${when(p.first)} au ${when(p.last)}. Import en file (tâche n° ${r.job.id}).`, p.rejected > 0);
      setTimeout(refreshAll, 4000);
    } catch (error) { status("import-status", error.message, true); }
  });
  $("backtest-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target;
    const [symbol, timeframe, source] = (f.elements.series.value || "||").split("|");
    if (!symbol) { status("backtest-status", "Aucune série disponible : importez des données d'abord.", true); return; }
    const pct = (name) => (numberValue(f, name) == null ? undefined : numberValue(f, name) / 100);
    const body = { symbol, timeframe, source, start: f.elements.start.value || null, end: f.elements.end.value || null,
      strategy_params: { family: f.elements.family.value, z_entry: numberValue(f, "z_entry"), fast: numberValue(f, "fast"),
        slow: numberValue(f, "slow"), min_atr_pct: pct("min_atr_pct"), max_atr_pct: pct("max_atr_pct") },
      risk: { initial_capital: numberValue(f, "initial_capital"), risk_per_trade_pct: numberValue(f, "risk_per_trade_pct"),
        stop_atr_multiple: numberValue(f, "stop_atr_multiple"), daily_loss_limit_pct: numberValue(f, "daily_loss_limit_pct"),
        max_drawdown_pct: numberValue(f, "max_drawdown_pct"), max_position_units: numberValue(f, "max_position_units") },
      costs: { spread_pips: numberValue(f, "spread_pips"), slippage_pips: numberValue(f, "slippage_pips"),
        commission_per_100k: numberValue(f, "commission_per_100k") } };
    status("backtest-status", "Envoi…");
    try {
      const r = await post("/api/lab/backtests", body);
      status("backtest-status", `Backtest n° ${r.backtest_id} en file : le worker le calcule, le tableau se met à jour seul.`);
      pollBacktests(r.backtest_id);
    } catch (error) { status("backtest-status", error.message, true); }
  });
  $("demo-resume").addEventListener("click", () => demoAction("resume", "Reprise"));
  $("demo-pause").addEventListener("click", () => demoAction("pause", "Pause"));
  $("demo-test").addEventListener("click", () => demoAction("controlled-test", "Ordre de test DEMO"));
  document.querySelector(".source-tf-select").addEventListener("change", fillSimPairs);
  $("sim-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target;
    const [source, timeframe] = (f.elements.series.value || "|").split("|");
    const symbols = [...f.querySelectorAll("input[name=symbols]:checked")].map((i) => i.value);
    const body = { name: f.elements.name.value, source, timeframe, symbols, start: f.elements.start.value || null,
      risk: { initial_capital: numberValue(f, "initial_capital"), risk_per_trade_pct: numberValue(f, "risk_per_trade_pct"),
        max_positions: numberValue(f, "max_positions"), daily_loss_limit_pct: numberValue(f, "daily_loss_limit_pct"),
        max_drawdown_pct: numberValue(f, "max_drawdown_pct") },
      costs: { spread_pips: numberValue(f, "spread_pips") } };
    status("sim-status", "Démarrage…");
    try {
      const r = await post("/api/sim/sessions", body);
      status("sim-status", `Simulation n° ${r.session_id} démarrée : le worker traite les bougies (quelques secondes).`);
      await refreshSessions();
      setTimeout(() => showSession(r.session_id).catch(() => {}), 6000);
    } catch (error) { status("sim-status", error.message, true); }
  });
}

async function pollBacktests(focus) {
  for (let i = 0; i < 60; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const pending = await refreshBacktests().catch(() => true);
    if (!pending) {
      if (focus) showBacktest(focus).catch(() => {});
      refreshJobs().catch(() => {});
      return;
    }
  }
}

// Empty fields become undefined and JSON.stringify drops them: the server then applies its defaults.
window.addEventListener("hashchange", showTab);
document.addEventListener("DOMContentLoaded", () => {
  showTab();
  for (const node of document.querySelectorAll(".operator-only")) node.hidden = !CAN_ACT;
  wireForms();
  refreshAll();
  setInterval(refreshAll, 20000);
});
