// ASTRA dashboard home: figures of the MT5 DEMO account, live quotes, candle chart with SMA, RSI and MACD,
// signals, positions, trade history and performance. Read only: this script never sends an order, and the server
// refuses one from a browser. It relies on the helpers of lab.js (loaded first).
"use strict";

const BOARD_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD"];
const BOARD_TFS = [["M5", "5m"], ["M15", "15m"], ["H1", "1h"], ["H4", "4h"], ["D1", "1J"]];
const BOARD_SOURCE = "MT5_METAQUOTES_DEMO";
const BOARD_FAST_MS = 5000;
const BOARD_SLOW_MS = 30000;
let boardPair = "EURUSD";
let boardTf = "H1";
let boardOverview = null;
let boardChartData = null;
let boardLastSlow = 0;

const pairLabel = (symbol) => `${symbol.slice(0, 3)}/${symbol.slice(3)}`;
const decimals = (symbol) => (symbol.includes("JPY") ? 3 : 5);
const price = (symbol, value) => (value == null ? "—" : Number(value).toFixed(decimals(symbol)));
const usdText = (value) => (value == null ? "—" : `${fmt.format(value)} USD`);
const signed = (value) => `${value >= 0 ? "+" : ""}${fmt.format(value)}`;
const percent = (value) => `${value >= 0 ? "+" : ""}${value.toFixed(2).replace(".", ",")} %`;
const tone = (value) => (value > 0 ? "gain" : value < 0 ? "loss" : "");
const NS = "http://www.w3.org/2000/svg";

function ago(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (seconds < 90) return `il y a ${seconds} s`;
  if (seconds < 5400) return `il y a ${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `il y a ${Math.round(seconds / 3600)} h`;
  return `il y a ${Math.round(seconds / 86400)} j`;
}

function chip(symbol) { return el("span", symbol.slice(0, 3), "pair-chip"); }

function card(label, value, small, toneClass) {
  const node = el("article", null, "metric-card");
  const strong = el("strong", value);
  if (toneClass) strong.classList.add(toneClass);
  const sub = el("small", small || "");
  node.append(el("span", label, "metric-label"), strong, sub);
  return node;
}

function renderKpis(d) {
  const acc = d.accounting || {};
  const snap = d.mt5.snapshot;
  const ok = acc.available;
  const net = ok ? acc.net_result : null;
  const dd = ok && acc.drawdown ? acc.drawdown.current_pct : null;
  const anyLive = Object.values(d.quotes || {}).some((q) => q.live);
  let system;
  if (!snap) system = ["EN ATTENTE", "Le pont MT5 n'a encore rien envoyé", "warn"];
  else if (!d.mt5.connected) system = ["PONT SILENCIEUX", `Dernier message ${ago(d.mt5.received_at)}`, "warn"];
  else if (!acc.consistent) system = ["ÉCART DE COMPTABILITÉ", "voir « Comptabilité du compte »", "loss"];
  else if (snap.bridge && snap.bridge.stop_file) system = ["ORDRES SUSPENDUS", "fichier STOP présent : aucune ouverture ni fermeture", "warn"];
  else system = anyLive ? ["OPÉRATIONNEL", "Cotations en continu, comptabilité cohérente", "gain"] : ["PARTIEL", "Compte lu, cotations anciennes", "warn"];
  $("board-kpis").replaceChildren(
    card("Fonds propres (DÉMO)", ok ? usdText(acc.equity) : "—", ok ? `balance ${usdText(acc.balance)} · latent ${signed(acc.floating_total)} USD` : "Aucun compte lu"),
    card("Positions ouvertes", ok ? String(acc.counts.open_positions) : "—", snap ? `${snap.astra_positions} d’origine ASTRA confirmée` : ""),
    card("Résultat net depuis le début", net == null ? "—" : `${signed(net)} USD`,
      ok ? (net == null ? "dépôts du courtier inconnus" : `réalisé ${signed(acc.realized_net)} · latent ${signed(acc.floating_total)}`) : "", net == null ? "" : tone(net)),
    card("Drawdown actuel", dd == null ? "—" : `${dd.toFixed(2).replace(".", ",")} %`,
      ok && acc.drawdown && acc.drawdown.max_pct != null ? `maximum ${acc.drawdown.max_pct.toFixed(2).replace(".", ",")} % depuis le ${day(acc.drawdown.since)}` : "série d’equity trop courte"),
    card("Clôturées aujourd’hui", ok && acc.counts.closed_today != null ? String(acc.counts.closed_today) : "—",
      ok ? `${acc.counts.closed_trades == null ? "—" : acc.counts.closed_trades} clôturées · ${acc.counts.orders} ordres · ${acc.counts.executions} exécutions` : ""),
    card("Statut système", system[0], system[1], system[2]));
}

function renderTicker(d) {
  $("board-ticker").replaceChildren(...BOARD_PAIRS.map((symbol) => {
    const q = d.quotes && d.quotes[symbol];
    const item = el("button", null, symbol === boardPair ? "ticker-item is-active" : "ticker-item");
    item.type = "button";
    item.addEventListener("click", () => { boardPair = symbol; boardChartData = null; renderBoard(); refreshChart(); });
    const change = q && q.change_24h_pct != null ? q.change_24h_pct : null;
    item.append(chip(symbol), el("strong", pairLabel(symbol)), el("span", q ? price(symbol, q.bid) : "—", "ticker-price"),
      el("span", change == null ? "" : percent(change), `ticker-change ${tone(change)}`));
    if (q && !q.live) item.title = "Cotation ancienne";
    return item;
  }));
}

// -- chart: candles, SMA 20 / 50, RSI 14, MACD ------------------------------------------------------
function svgNode(parent, tag, attrs, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text != null) node.textContent = text;
  parent.append(node);
  return node;
}

function polyline(svg, values, sx, sy, className) {
  let path = "";
  let open = false;
  values.forEach((v, i) => {
    if (v == null) { open = false; return; }
    path += `${open ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)} `;
    open = true;
  });
  if (path) svgNode(svg, "path", { d: path.trim(), class: className });
}

function lastValue(values) {
  for (let i = values.length - 1; i >= 0; i -= 1) if (values[i] != null) return values[i];
  return null;
}

function proChart(container, data, levels, live, symbol) {
  container.replaceChildren();
  const cs = data.candles;
  if (cs.length < 5) { container.append(el("p", "Pas assez de bougies pour cette paire et cette unité de temps.", "empty-cell")); return; }
  const W = 1000, padL = 6, padR = 70, top = 8, gap = 12, h1 = 280, h2 = 80, h3 = 90, bottom = 22;
  const H = top + h1 + gap + h2 + gap + h3 + bottom;
  const n = cs.length;
  const step = (W - padL - padR) / n;
  const sx = (i) => padL + step * (i + 0.5);
  const ind = data.indicators;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Bougies ${pairLabel(symbol)} avec SMA 20, SMA 50, RSI 14 et MACD`);
  // price panel
  const prices = [...cs.map((c) => c.low), ...cs.map((c) => c.high), ...levels.map((l) => l.price)];
  for (const s of [ind.sma20, ind.sma50]) for (const v of s) if (v != null) prices.push(v);
  if (live != null) prices.push(live);
  const lo0 = Math.min(...prices), hi0 = Math.max(...prices);
  const pad = (hi0 - lo0) * 0.05 || 0.0005;
  const lo = lo0 - pad, hi = hi0 + pad;
  const sy1 = (v) => top + (hi - v) / (hi - lo) * h1;
  for (let i = 0; i <= 5; i += 1) {
    const v = lo + (hi - lo) * i / 5;
    svgNode(svg, "line", { x1: padL, x2: W - padR, y1: sy1(v), y2: sy1(v), class: "candle-grid" });
    svgNode(svg, "text", { x: W - padR + 6, y: sy1(v) + 3, class: "chart-axis" }, price(symbol, v));
  }
  cs.forEach((c, i) => {
    const up = c.close >= c.open;
    svgNode(svg, "line", { x1: sx(i), x2: sx(i), y1: sy1(c.high), y2: sy1(c.low), class: up ? "candle-up" : "candle-down" });
    svgNode(svg, "rect", { x: sx(i) - Math.max(step * 0.34, 0.5), width: Math.max(step * 0.68, 1), y: sy1(Math.max(c.open, c.close)),
      height: Math.max(Math.abs(sy1(c.open) - sy1(c.close)), 1), class: up ? "candle-up candle-body" : "candle-down candle-body" });
  });
  polyline(svg, ind.sma20, sx, sy1, "line-sma20");
  polyline(svg, ind.sma50, sx, sy1, "line-sma50");
  for (const level of levels) {
    svgNode(svg, "line", { x1: padL, x2: W - padR, y1: sy1(level.price), y2: sy1(level.price), class: `level-${level.kind}` });
    svgNode(svg, "text", { x: W - padR - 4, y: sy1(level.price) - 3, "text-anchor": "end", class: `level-text level-${level.kind}-text` }, level.text);
  }
  if (live != null) {
    svgNode(svg, "line", { x1: padL, x2: W - padR, y1: sy1(live), y2: sy1(live), class: "line-live" });
    svgNode(svg, "rect", { x: W - padR, y: sy1(live) - 8, width: padR - 2, height: 16, class: "tag-live" });
    svgNode(svg, "text", { x: W - padR + 4, y: sy1(live) + 4, class: "tag-live-text" }, price(symbol, live));
  }
  // RSI panel
  const y2 = top + h1 + gap;
  const sy2 = (v) => y2 + (100 - v) / 100 * h2;
  svgNode(svg, "rect", { x: padL, y: y2, width: W - padL - padR, height: h2, class: "sub-frame" });
  for (const level of [30, 70]) {
    svgNode(svg, "line", { x1: padL, x2: W - padR, y1: sy2(level), y2: sy2(level), class: "line-rsi-band" });
    svgNode(svg, "text", { x: W - padR + 6, y: sy2(level) + 3, class: "chart-axis" }, String(level));
  }
  polyline(svg, ind.rsi14, sx, sy2, "line-rsi");
  svgNode(svg, "text", { x: padL + 6, y: y2 + 12, class: "sub-label" }, `RSI 14  ${lastValue(ind.rsi14) == null ? "—" : lastValue(ind.rsi14).toFixed(1)}`);
  // MACD panel
  const y3 = y2 + h2 + gap;
  const macdAll = [...ind.macd, ...ind.signal, ...ind.histogram].filter((v) => v != null);
  const bound = Math.max(...macdAll.map(Math.abs), 1e-9) * 1.1;
  const sy3 = (v) => y3 + (bound - v) / (2 * bound) * h3;
  svgNode(svg, "rect", { x: padL, y: y3, width: W - padL - padR, height: h3, class: "sub-frame" });
  svgNode(svg, "line", { x1: padL, x2: W - padR, y1: sy3(0), y2: sy3(0), class: "line-rsi-band" });
  ind.histogram.forEach((v, i) => {
    if (v == null) return;
    svgNode(svg, "rect", { x: sx(i) - Math.max(step * 0.3, 0.4), width: Math.max(step * 0.6, 0.8), y: Math.min(sy3(v), sy3(0)),
      height: Math.max(Math.abs(sy3(v) - sy3(0)), 0.5), class: v >= 0 ? "hist-up" : "hist-down" });
  });
  polyline(svg, ind.macd, sx, sy3, "line-macd");
  polyline(svg, ind.signal, sx, sy3, "line-signal");
  const m = lastValue(ind.macd);
  svgNode(svg, "text", { x: padL + 6, y: y3 + 12, class: "sub-label" }, `MACD 12 26 9  ${m == null ? "—" : m.toFixed(5)}`);
  // time axis
  const intraday = data.timeframe !== "D1";
  for (let i = 0; i < 6; i += 1) {
    const idx = Math.min(n - 1, Math.round((n - 1) * i / 5));
    const d = new Date(cs[idx].ts);
    const text = intraday ? d.toLocaleString("fr-FR", { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString("fr-FR", { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "2-digit" });
    svgNode(svg, "text", { x: sx(idx), y: H - 6, "text-anchor": i === 0 ? "start" : i === 5 ? "end" : "middle", class: "chart-axis" }, text);
  }
  container.append(svg);
  const legend = [["SMA 20", lastValue(ind.sma20), "legend-sma20"], ["SMA 50", lastValue(ind.sma50), "legend-sma50"]];
  $("board-legend").replaceChildren(...legend.map(([name, v, cls]) => el("span", `${name}  ${v == null ? "—" : price(symbol, v)}`, `legend-item ${cls}`)),
    el("span", `RSI 14  ${lastValue(ind.rsi14) == null ? "—" : lastValue(ind.rsi14).toFixed(1)}`, "legend-item"),
    el("span", `MACD  ${m == null ? "—" : m.toFixed(5)}`, "legend-item"));
}

function renderChart() {
  const d = boardOverview;
  $("board-pair-title").textContent = pairLabel(boardPair);
  const q = d && d.quotes && d.quotes[boardPair];
  $("board-live-price").textContent = q ? price(boardPair, q.bid) : "—";
  const change = q && q.change_24h_pct != null ? q.change_24h_pct : null;
  const changeNode = $("board-live-change");
  changeNode.textContent = change == null ? "" : `${percent(change)} sur 24 h`;
  changeNode.className = `live-change ${tone(change)}`;
  $("board-tfs").replaceChildren(...BOARD_TFS.map(([tf, label]) => {
    const b = button(label, () => { boardTf = tf; boardChartData = null; renderChart(); refreshChart(); }, tf === boardTf ? "tf-button is-active" : "tf-button");
    return b;
  }));
  if (!boardChartData) return;
  const positions = (d && d.mt5.snapshot ? d.mt5.snapshot.positions : []).filter((p) => p.symbol === boardPair);
  const levels = [];
  for (const p of positions) {
    levels.push({ kind: "entry", price: p.price_open, text: `${p.side === "LONG" ? "achat" : "vente"} ${fmt.format(p.volume)} lot  ${price(boardPair, p.price_open)}` });
    if (p.sl) levels.push({ kind: "stop", price: p.sl, text: `stop ${price(boardPair, p.sl)}` });
  }
  proChart($("board-chart"), boardChartData, levels, q ? q.bid : null, boardPair);
}

async function refreshChart() {
  try {
    boardChartData = await api(`/api/market/chart?symbol=${boardPair}&timeframe=${boardTf}&source=${BOARD_SOURCE}&limit=${boardTf === "M5" ? 220 : 200}`);
  } catch (error) {
    boardChartData = { candles: [], indicators: {}, timeframe: boardTf };
  }
  renderChart();
}

// -- side and bottom panels ------------------------------------------------------------------------
function badge(decision) {
  const text = decision === "ACHAT" ? "ACHAT" : decision === "VENTE" ? "VENTE" : "ATTENTE";
  return el("span", text, `badge ${decision === "ACHAT" ? "badge-buy" : decision === "VENTE" ? "badge-sell" : "badge-wait"}`);
}

const FAMILY = { sma_trend: "tendance SMA", momentum: "momentum", mean_reversion: "retour à la moyenne" };

function renderDecision(d) {
  const s = (d.signals || []).find((x) => x.symbol === boardPair);
  const box = $("board-decision");
  if (!s) { box.replaceChildren(el("p", "Aucune décision pour cette paire.", "empty-cell")); return; }
  const position = (d.mt5.snapshot ? d.mt5.snapshot.positions : []).find((p) => p.symbol === boardPair);
  const rows = [
    ["Paire", pairLabel(boardPair)],
    ["Stratégie en service", `${FAMILY[s.family] || s.family} · version ${s.version}`],
    ["Dernière décision", s.since ? `${when(s.since)} UTC` : "—"],
    ["Position MT5 démo", position ? `${position.side === "LONG" ? "ACHAT" : "VENTE"} ${fmt.format(position.volume)} lot` : "aucune"],
  ];
  const list = el("dl", null, "kv");
  for (const [k, v] of rows) list.append(el("dt", k), el("dd", v));
  box.replaceChildren(el("div", null, "decision-head"), list, el("p", s.reason || "—", "decision-reason"));
  box.firstChild.append(badge(s.decision), el("span", "décision d’ASTRA", "muted"));
}

function renderAccounts(d, demo) {
  const snap = d.mt5.snapshot;
  const bn = demo && demo.status;
  const rows = [
    ["BN", "Binance Spot DEMO", bn && bn.connected ? ["Connecté", "ok"] : ["Non connecté", "off"],
      bn && bn.connected ? Object.entries(bn.balances || {}).map(([k, v]) => `${k} ${fmt.format(Number(v.free))}`).slice(0, 2).join(" · ")
        : "clé démo à créer et déposer"],
    ["MT", "MetaTrader 5 DEMO", snap && d.mt5.connected ? ["Connecté", "ok"] : ["Hors ligne", "off"],
      snap ? `Solde ${usdText(snap.account.balance)} · résultat latent ${signed(snap.open_profit)}` : "pont arrêté ou terminal éteint"],
    ["IQ", "IQ Option", ["Indisponible", "off"], "pas d’API officielle : non connectable"],
  ];
  $("board-accounts").replaceChildren(...rows.map(([code, name, [state, kind], detail]) => {
    const item = el("div", null, "account-item");
    const head = el("div", null, "account-head");
    head.append(el("span", code, "pair-chip"), el("strong", name), el("span", state, `pill pill-${kind}`));
    item.append(head, el("small", detail));
    return item;
  }));
  const times = Object.values(d.quotes || {}).map((q) => Date.parse(q.received_at)).filter(Boolean);
  const newest = times.length ? new Date(Math.max(...times)).toISOString() : null;
  const live = Object.values(d.quotes || {}).some((q) => q.live);
  const flow = el("dl", null, "kv");
  for (const [k, v] of [["Source", "MetaQuotes-Demo (compte démo MT5)"], ["Dernière cotation", ago(newest)],
    ["Rythme", live ? "mise à jour toutes les 5 s" : "flux interrompu"], ["Couverture", "7 paires · 5 unités de temps"]]) flow.append(el("dt", k), el("dd", v));
  $("board-flow").replaceChildren(flow);
  $("board-flow-pill").textContent = live ? "En continu" : "Interrompu";
  $("board-flow-pill").className = `pill ${live ? "pill-ok" : "pill-off"}`;
}

function renderRisk(d) {
  const snap = d.mt5.snapshot;
  const risk = snap && snap.risk;
  const box = $("board-risk");
  const pill = $("board-risk-pill");
  if (!risk) {
    box.replaceChildren(el("p", "Le pont n’a pas encore publié l’état du portail de risque.", "empty-cell"));
    pill.textContent = "—";
    pill.className = "pill";
    return;
  }
  pill.textContent = risk.entries_allowed ? "Entrées autorisées" : "Entrées bloquées";
  pill.className = `pill ${risk.entries_allowed ? "pill-ok" : "pill-off"}`;
  const lim = risk.limits;
  const kv = el("dl", null, "kv");
  const rows = [
    ["Perte du jour", risk.daily_loss_pct == null ? "—" : `${risk.daily_loss_pct.toFixed(2).replace(".", ",")} % (limite ${lim ? lim.daily_loss_limit_pct : "—"} %)`],
    ["Drawdown", risk.drawdown_pct == null ? "—" : `${risk.drawdown_pct.toFixed(2).replace(".", ",")} % (limite ${lim ? lim.max_drawdown_pct : "—"} %)`],
    ["Risque par ordre", lim ? `${lim.risk_per_trade_pct} % des fonds propres` : "profil invalide"],
    ["Positions ASTRA", lim ? `${snap.astra_positions} sur ${lim.max_positions} au maximum` : "—"],
  ];
  for (const [k, v] of rows) kv.append(el("dt", k), el("dd", v));
  const parts = [kv];
  const refused = (risk.checks || []).filter((c) => !c.passed);
  const checks = el("div", null, "risk-list");
  if (refused.length) for (const c of refused) checks.append(el("small", `REFUS · ${c.name.replaceAll("_", " ")} : ${c.detail}`, "risk-ko"));
  else checks.append(el("small", `${(risk.checks || []).length} contrôles passés : compte démo, fraîcheur, arrêt d’urgence, pertes, positions`, "risk-ok"));
  parts.push(checks);
  const rec = snap.reconciliation;
  const recBox = el("div", null, "risk-list");
  if (rec) {
    const alerts = rec.divergences.filter((x) => x.level === "alerte");
    recBox.append(el("small", alerts.length ? `Rapprochement MT5 : ${alerts.length} écart(s)` : "Rapprochement MT5 : positions conformes aux décisions", alerts.length ? "risk-ko" : "risk-ok"));
    for (const x of rec.divergences.slice(0, 4)) recBox.append(el("small", `${x.level === "alerte" ? "ALERTE" : "info"} · ${pairLabel(x.symbol)} : ${x.detail}`, x.level === "alerte" ? "risk-ko" : "risk-muted"));
  }
  parts.push(recBox);
  const lastRefusals = (d.mt5.events || []).filter((e) => e.kind === "refused").slice(0, 3);
  if (lastRefusals.length) {
    const log = el("div", null, "risk-list");
    log.append(el("small", "Derniers ordres refusés :", "risk-muted"));
    for (const e of lastRefusals) log.append(el("small", `${when(e.ts).slice(0, 16)} · ${pairLabel(e.symbol)} : ${e.reason}`, "risk-ko"));
    parts.push(log);
  }
  const intents = (snap.bridge && snap.bridge.intents) || [];
  if (intents.length) {
    const STATES = { SOUMIS: "soumis, réponse attendue", INCONNU: "résultat inconnu : vérification du compte", EXECUTE: "exécuté",
      PARTIEL: "exécution partielle", REJETE: "rejeté par le courtier", ABSENT: "non exécuté (vérifié)" };
    const life = el("div", null, "risk-list");
    life.append(el("small", "Cycle de vie des derniers ordres :", "risk-muted"));
    for (const i of intents.slice(0, 5)) {
      const bad = ["REJETE", "INCONNU", "ABSENT"].includes(i.status);
      life.append(el("small", `${when(i.at).slice(0, 16)} · ${pairLabel(i.symbol)} ${i.side === "LONG" ? "achat" : "vente"} ${fmt.format(i.lots)} lot : ${STATES[i.status] || i.status}${i.lots_filled != null ? ` (${fmt.format(i.lots_filled)} lot rempli)` : ""}`, bad ? "risk-ko" : "risk-ok"));
    }
    parts.push(life);
  }
  box.replaceChildren(...parts);
}

function renderSignals(d) {
  $("board-signals").replaceChildren(...(d.signals || []).map((s) => {
    const item = el("div", null, "signal-item");
    const head = el("div", null, "signal-head");
    head.append(chip(s.symbol), el("strong", pairLabel(s.symbol)), badge(s.decision), el("small", s.since ? ago(s.since) : "—"));
    item.append(head, el("small", `${FAMILY[s.family] || s.family} v${s.version} · ${s.reason || "pas encore de décision"}`));
    return item;
  }));
}

function renderPositions(d) {
  const positions = d.mt5.snapshot ? d.mt5.snapshot.positions : [];
  $("board-positions-title").textContent = `Positions ouvertes (${positions.length})`;
  if (!positions.length) { $("board-positions").replaceChildren(el("p", "Aucune position ouverte.", "empty-cell")); return; }
  $("board-positions").replaceChildren(...positions.map((p) => {
    const item = el("div", null, "position-item");
    const head = el("div", null, "signal-head");
    head.append(chip(p.symbol), el("strong", pairLabel(p.symbol)),
      el("span", p.side === "LONG" ? "ACHAT" : "VENTE", `badge ${p.side === "LONG" ? "badge-buy" : "badge-sell"}`),
      el("small", `${fmt.format(p.volume)} lot`), el("span", `${signed(p.profit + (p.swap || 0))} USD`, `position-profit ${tone(p.profit + (p.swap || 0))}`));
    const grid = el("div", null, "position-grid");
    for (const [k, v] of [["Prix d’entrée", price(p.symbol, p.price_open)], ["Prix actuel", price(p.symbol, p.price_current)],
      ["Stop", price(p.symbol, p.sl)], ["Profit", `${signed(p.profit)} USD`], ["Swap", `${signed(p.swap || 0)} USD`],
      ["Billet", String(p.ticket)]]) { const cell = el("div"); cell.append(el("small", k), el("strong", v)); grid.append(cell); }
    const proof = el("small", `${p.origin ? p.origin.label : "origine non confirmée"}${p.origin && p.origin.proof ? ` · ${p.origin.proof}` : ""}`, p.origin && p.origin.astra ? "risk-ok" : "risk-muted");
    item.append(head, grid, proof);
    if (CAN_ACT && p.origin && (p.origin.astra || p.origin.manual)) {
      const buttons = el("div", null, "button-row position-actions");
      buttons.append(
        button("Fermer", () => sendRequest({ kind: "close", ticket: p.ticket },
          `Fermer la position ${p.ticket} (${pairLabel(p.symbol)}, ${p.side === "LONG" ? "achat" : "vente"} ${fmt.format(p.volume)} lot) au marché ?\n\nRésultat actuel : ${signed(p.profit + (p.swap || 0))} USD, qui devient réalisé.`), "small-button"),
        button("Resserrer le stop", () => {
          const value = window.prompt(`Nouveau stop pour ${pairLabel(p.symbol)} (prix). Il ne peut que se rapprocher du prix actuel ${price(p.symbol, p.price_current)}. Stop actuel : ${price(p.symbol, p.sl)}.`);
          if (value === null || value.trim() === "") return;
          const sl = Number(value.replace(",", "."));
          if (!Number.isFinite(sl) || sl <= 0) { status("board-act-status", "Prix de stop invalide.", true); return; }
          sendRequest({ kind: "modify_sl", ticket: p.ticket, sl }, `Déplacer le stop de la position ${p.ticket} à ${sl} ?`);
        }, "small-button"));
      item.append(buttons);
    }
    return item;
  }));
}

function renderHistory(d) {
  const deals = (d.mt5.recent_deals || []).slice(0, 12);
  fill("board-history", deals.map((x) => row([when(x.ts).slice(0, 16), pairLabel(x.symbol), x.entry === 0 ? "ouverture" : "fermeture",
    x.type === 0 ? "Achat" : "Vente", num(x.volume), el("span", signed(x.net), tone(x.net)), x.astra ? "ASTRA" : "autre"])),
  "Aucune transaction dans l’historique du courtier reçu.", 7);
}

function renderPerformance(d) {
  const acc = d.accounting || {};
  const p = d.performance;
  const stats = el("div", null, "perf-stats");
  const cell = (k, v, t) => { const c = el("div"); c.append(el("small", k), el("strong", v, t || "")); return c; };
  const closed = acc.closed_stats || {};
  stats.append(cell("Résultat net (depuis le début)", acc.net_result == null ? "—" : `${signed(acc.net_result)} USD`, acc.net_result == null ? "" : tone(acc.net_result)),
    cell("Transactions clôturées", acc.counts && acc.counts.closed_trades != null ? String(acc.counts.closed_trades) : "—"),
    cell("Taux de réussite", closed.win_rate_pct == null ? "—" : `${String(closed.win_rate_pct).replace(".", ",")} %`),
    cell("Facteur de profit", closed.profit_factor == null ? "—" : String(closed.profit_factor).replace(".", ",")));
  const x = p.execution;
  const quality = el("p", null, "footnote");
  quality.textContent = x && x.orders
    ? `Qualité d’exécution (ordres envoyés depuis la mise en service du suivi) : ${x.orders} ordre(s), ${x.refused} refusé(s) par le portail · glissement moyen ${x.avg_slippage_pips == null ? "—" : x.avg_slippage_pips} pip · spread moyen ${x.avg_spread_pips == null ? "—" : x.avg_spread_pips} pip · latence moyenne ${x.avg_latency_ms == null ? "—" : x.avg_latency_ms} ms`
    : "Qualité d’exécution : aucun ordre envoyé depuis la mise en service du suivi (les ordres plus anciens ne sont connus que par l’historique du courtier).";
  const chart = el("div", null, "chart");
  $("board-perf").replaceChildren(stats, chart, quality);
  const points = (p.curve || []).map((q) => ({ x: Date.parse(q.ts), y: q.equity }));
  if (points.length >= 2) lineChart(chart, points, "Fonds propres du compte démo MT5");
  else chart.append(el("p", "La courbe se construit au fil des minutes.", "empty-cell"));
}

function renderAccounting(d) {
  const a = d.accounting || {};
  const box = $("board-accounting");
  if (!a.available) { box.replaceChildren(el("p", "Aucun instantané du compte reçu.", "empty-cell")); return; }
  const def = a.definitions || {};
  const line = (label, value, key) => { const dt = el("dt", label); if (def[key]) dt.title = def[key]; return [dt, el("dd", value)]; };
  const usd2 = (v) => (v == null ? "inconnu" : `${signed(v)} USD`);
  const kv = el("dl", null, "kv");
  const rows = [
    ["Balance", usdText(a.balance), "balance"], ["Fonds propres", usdText(a.equity), "equity"],
    ["Profit latent (positions ouvertes)", usd2(a.floating_profit), "floating_profit"], ["Swaps latents", usd2(a.floating_swap), "floating_swap"],
    ["Résultat latent total", usd2(a.floating_total), "floating_total"], ["Résultat réalisé (hors frais)", usd2(a.realized_gross), "realized_gross"],
    ["Commissions, swaps et frais réalisés", usd2(a.realized_costs), "realized_costs"], ["Résultat réalisé net", usd2(a.realized_net), "realized_net"],
    ["Dépôts nets", a.net_deposits == null ? "inconnu" : usdText(a.net_deposits), "net_deposits"], ["Résultat net depuis le début", usd2(a.net_result), "net_result"],
    ["Marge utilisée · libre", `${usdText(a.margin)} · ${usdText(a.margin_free)}`, "equity"],
    ["Ordres", a.counts.orders, "orders"], ["Exécutions", a.counts.executions, "executions"],
    ["Transactions clôturées", a.counts.closed_trades == null ? "inconnu" : a.counts.closed_trades, "closed_trades"],
    ["Positions ouvertes", a.counts.open_positions, "open_positions"], ["Clôturées aujourd’hui", a.counts.closed_today == null ? "inconnu" : a.counts.closed_today, "closed_today"],
  ];
  for (const [label, value, key] of rows) kv.append(...line(label, String(value), key));
  const checks = el("div", null, "risk-list");
  for (const c of a.checks || []) checks.append(el("small", `${c.ok ? "OK" : "ÉCART"} · ${c.name.replaceAll("_", " ")} : ${c.detail}`, c.ok ? "risk-ok" : "risk-ko"));
  for (const m of a.missing || []) checks.append(el("small", `DONNÉE MANQUANTE · ${m}`, "risk-ko"));
  box.replaceChildren(kv, checks);
  $("board-accounting-pill").textContent = a.consistent ? "Rapprochée" : "Écart";
  $("board-accounting-pill").className = `pill ${a.consistent ? "pill-ok" : "pill-off"}`;
}

function renderFreshness(d) {
  const lines = d.freshness || [];
  const box = $("board-fresh");
  const items = lines.map((l) => {
    const item = el("div", null, "account-item");
    const head = el("div", null, "account-head");
    const kind = l.state === "ok" ? "ok" : l.state === "retard" ? "off" : "";
    head.append(el("strong", l.label), el("span", l.state === "ok" ? "à jour" : l.state === "retard" ? "en retard" : l.state === "info" ? "info" : "inconnu", `pill ${kind ? `pill-${kind}` : ""}`));
    item.append(head, el("small", `${l.at ? `${when(l.at)} UTC (${ago(l.at)})` : "jamais"} · ${l.note}`));
    return item;
  });
  box.replaceChildren(...items);
}

// -- actions on the DEMO account: the page queues a request, the bridge executes it with its protections ---------
const REQUEST_LABEL = { order: "Ordre", close: "Fermeture", modify_sl: "Stop", pause: "Suspendre", resume: "Reprendre" };
const REQUEST_STATUS = { EN_ATTENTE: "en attente du pont", EXECUTE: "exécutée", REFUSE: "refusée", ECHEC: "échec", EXPIRE: "expirée, jamais exécutée" };
let boardRequestWatch = null;

function describeRequest(r) {
  const p = r.params || {};
  if (r.kind === "order") return `${p.side === "LONG" ? "Achat" : "Vente"} ${pairLabel(p.symbol)}, stop ${p.stop_pips} pips`;
  if (r.kind === "close") return `Fermer la position ${p.ticket}`;
  if (r.kind === "modify_sl") return `Stop de la position ${p.ticket} à ${p.sl}`;
  return REQUEST_LABEL[r.kind];
}

function renderRequests(list) {
  fill("board-requests", list.slice(0, 12).map((r) => row([String(r.id), when(r.ts).slice(0, 16), (r.actor || "").replace("web:", ""),
    describeRequest(r), el("span", REQUEST_STATUS[r.status] || r.status, r.status === "EXECUTE" ? "gain" : r.status === "EN_ATTENTE" ? "" : "loss"),
    r.result && r.result.message ? r.result.message : "—"])), "Aucune demande.", 6);
}

async function refreshRequests() {
  try {
    const d = await api("/api/mt5/requests");
    renderRequests(d.requests || []);
    return d.requests || [];
  } catch (error) { return []; }
}

async function sendRequest(body, confirmation) {
  if (!window.confirm(confirmation)) return;
  const line = $("board-act-status");
  status("board-act-status", "Demande envoyée…");
  try {
    const r = await post("/api/mt5/requests", body);
    status("board-act-status", `Demande n° ${r.request_id} enregistrée : le pont la traite sous quelques secondes (elle expire après 2 minutes).`);
    if (boardRequestWatch) clearInterval(boardRequestWatch);
    let tries = 0;
    boardRequestWatch = setInterval(async () => {
      tries += 1;
      const list = await refreshRequests();
      const mine = list.find((x) => x.id === r.request_id);
      if (mine && mine.status !== "EN_ATTENTE") {
        status("board-act-status", `Demande n° ${mine.id} ${REQUEST_STATUS[mine.status]} : ${(mine.result && mine.result.message) || "—"}`, mine.status !== "EXECUTE");
        clearInterval(boardRequestWatch);
        refreshBoard();
      } else if (tries > 30) clearInterval(boardRequestWatch);
    }, 3000);
  } catch (error) { line.textContent = error.message; line.classList.add("is-error"); }
}

function wireActions() {
  const section = $("board-actions");
  if (!section || !CAN_ACT) return;
  section.hidden = false;
  $("board-ord-pair").replaceChildren(...BOARD_PAIRS.map((p) => new Option(pairLabel(p), p)));
  const risk = () => {
    const lim = boardOverview && boardOverview.mt5.snapshot && boardOverview.mt5.snapshot.risk ? boardOverview.mt5.snapshot.risk.limits : null;
    return lim ? `${lim.risk_per_trade_pct} % des fonds propres` : "la limite de risque";
  };
  const order = (side) => {
    const symbol = $("board-ord-pair").value;
    const stop = Number($("board-ord-stop").value);
    const verb = side === "LONG" ? "ACHAT" : "VENTE";
    sendRequest({ kind: "order", symbol, side, stop_pips: stop },
      `Placer un ordre DÉMO : ${verb} ${pairLabel(symbol)} au marché, stop à ${stop} pips.\n\nLe volume est calculé pour perdre au plus ${risk()} si le stop est atteint. L'ordre est refusé si le portail de risque, le fichier STOP ou l'arrêt d'urgence l'interdit.\n\nConfirmer ?`);
  };
  $("board-ord-buy").addEventListener("click", () => order("LONG"));
  $("board-ord-sell").addEventListener("click", () => order("SHORT"));
  $("board-pause").addEventListener("click", () => sendRequest({ kind: "pause" },
    "Suspendre les ordres automatiques ?\n\nLe pont ne fera plus aucune ouverture ni fermeture automatique. Les positions restent ouvertes avec leurs stops chez le courtier."));
  $("board-resume").addEventListener("click", () => sendRequest({ kind: "resume" },
    "Reprendre les ordres automatiques ?\n\nLe fichier STOP est retiré : le pont pourra ouvrir et fermer des positions démo selon les décisions des stratégies, avec le portail de risque. C'est refusé tant que l'arrêt d'urgence est actif."));
  refreshRequests();
}

function renderBoard() {
  if (!boardOverview) return;
  const d = boardOverview;
  renderKpis(d);
  renderTicker(d);
  renderChart();
  renderDecision(d);
  renderSignals(d);
  renderRisk(d);
  renderAccounting(d);
  renderFreshness(d);
  renderPositions(d);
  renderHistory(d);
  renderPerformance(d);
}

let boardDemo = null;

async function refreshBoard() {
  if (window.location.hash && window.location.hash !== "#tableau") return;
  try {
    boardOverview = await api("/api/dashboard/overview");
  } catch (error) {
    $("board-status").textContent = "Données du tableau de bord indisponibles";
    return;
  }
  $("board-status").textContent = "";
  if (Date.now() - boardLastSlow > BOARD_SLOW_MS || !boardChartData) {
    boardLastSlow = Date.now();
    try { boardDemo = await api("/api/demo/overview"); } catch (error) { boardDemo = null; }
    if (CAN_ACT) refreshRequests();
    refreshChart().catch(() => {});
  }
  renderBoard();
  renderAccounts(boardOverview, boardDemo);
}

document.addEventListener("DOMContentLoaded", () => {
  wireActions();
  refreshBoard();
  setInterval(refreshBoard, BOARD_FAST_MS);
  window.addEventListener("hashchange", refreshBoard);
});
