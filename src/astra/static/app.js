const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const currency = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function shortHash(value) {
  return value ? `${value.slice(0, 12)}…` : "—";
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("fr-FR", { timeZone: "UTC" }) : "—";
}

function requireSession(response) {
  // The server is the security barrier; this only sends an expired session back to the login page.
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("session expirée");
  }
  return response;
}

const DATA_LABELS = {
  SYNTHETIC: "SYNTHÉTIQUE", SIMULATED: "SIMULATION", HISTORICAL: "HISTORIQUE (déclarée)",
  DELAYED: "DIFFÉRÉE (déclarée)", LIVE: "EN DIRECT (déclarée, non vérifiée)",
};

async function loadDashboard() {
  const state = document.getElementById("service-state");
  try {
    const [healthResponse, summaryResponse, runsResponse] = (await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/summary", { cache: "no-store" }),
      fetch("/api/runs", { cache: "no-store" }),
    ])).map(requireSession);
    if (![healthResponse, summaryResponse, runsResponse].every((response) => response.ok)) {
      throw new Error("API indisponible");
    }
    const [health, summary, runsPayload] = await Promise.all([
      healthResponse.json(), summaryResponse.json(), runsResponse.json(),
    ]);
    state.textContent = health.status === "ok" ? "● SERVICE DISPONIBLE" : "SERVICE DÉGRADÉ";
    const latest = summary.latest_run;
    setText("data-label-value", latest ? (DATA_LABELS[latest.source_type] || "INCONNUE") : "aucune exécution · SIMULATION");
    if (latest) {
      setText("dataset-label", `${latest.source_type} (déclarée) · ${latest.source_name}`);
      setText("dataset-hash", `Origine fournisseur non vérifiée · SHA-256 ${shortHash(latest.dataset_sha256)}`);
      setText("observations", number.format(latest.observations));
      setText("net-pnl", currency.format(latest.net_pnl));
      setText("trade-count", `${number.format(latest.trade_count)} trade(s) · brut ${currency.format(latest.gross_pnl)} · coûts ${currency.format(latest.total_costs)}`);
      setText("drawdown", `${number.format(latest.max_drawdown_pct)} %`);
      setText("strategy", `${latest.strategy} · ${latest.strategy_version}`);
      setText("instrument", latest.instrument);
      setText("period", `${formatDate(latest.start_time)} → ${formatDate(latest.end_time)} UTC`);
      setText("profit-factor", latest.profit_factor == null ? "Non défini" : number.format(latest.profit_factor));
      setText("win-rate", latest.win_rate == null ? "Non défini" : `${number.format(latest.win_rate * 100)} %`);
      const rejections = latest.details?.risk_rejections || [];
      setText("risk-rejections", `${rejections.length} · ${rejections.join(", ") || "aucun refus"}`);
    }
    const runs = runsPayload.runs || [];
    setText("run-total", `${runs.length} exécution${runs.length === 1 ? "" : "s"}`);
    if (runs.length) {
      const body = document.getElementById("runs-body");
      body.replaceChildren(...runs.map((run) => {
        const row = document.createElement("tr");
        const values = [formatDate(run.created_at), `${run.source_type} (déclarée) · ${run.source_name}`, `${run.strategy} ${run.strategy_version}`, number.format(run.trade_count), currency.format(run.net_pnl), run.mode];
        for (const value of values) {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.append(cell);
        }
        return row;
      }));
    }
  } catch (_error) {
    state.textContent = "API INDISPONIBLE";
    state.classList.add("is-error");
  }
}

function cell(value) {
  const td = document.createElement("td");
  td.textContent = value;
  return td;
}

async function getJson(route) {
  const response = requireSession(await fetch(route, { cache: "no-store" }));
  if (response.status === 403) throw new Error("forbidden");
  if (!response.ok) throw new Error(route);
  return response.json();
}

async function loadOperations() {
  try {
    const [controls, reconciliations, orders, models] = await Promise.all([
      getJson("/api/controls"), getJson("/api/reconciliations"), getJson("/api/orders"), getJson("/api/models"),
    ]);
    const kill = controls.kill_switch || {};
    setText("kill-switch", kill.active ? `ACTIF · ${kill.reason || ""}` : "inactif");
    setText("freezes", controls.entry_freezes.length ? controls.entry_freezes.join(", ") : "aucun");
    const entries = document.getElementById("entries-state");
    entries.textContent = controls.entries_allowed ? "ENTRÉES AUTORISÉES" : "ENTRÉES BLOQUÉES";
    entries.classList.toggle("is-blocked", !controls.entries_allowed);
    const last = (reconciliations.reconciliations || [])[0];
    setText("last-reconciliation", last ? `${last.status} · ${formatDate(last.occurred_at)}` : "aucune");

    const orderRows = orders.orders || [];
    setText("order-total", `${orderRows.length} ordre${orderRows.length === 1 ? "" : "s"}`);
    if (orderRows.length) {
      document.getElementById("orders-body").replaceChildren(...orderRows.map((order) => {
        const row = document.createElement("tr");
        row.append(
          cell(formatDate(order.created_at)), cell(order.account_ref), cell(order.purpose), cell(order.direction),
          cell(number.format(order.filled_units || order.requested_units)), cell(order.state),
          cell(order.reason_codes.join(", ") || "—"),
        );
        return row;
      }));
    }

    const versions = models.models || [];
    setText("model-total", `${versions.length} version${versions.length === 1 ? "" : "s"}`);
    if (versions.length) {
      document.getElementById("model-list").replaceChildren(...versions.slice(0, 8).map((version) => {
        const item = document.createElement("div");
        const term = document.createElement("dt");
        const detail = document.createElement("dd");
        term.textContent = `${version.model_id} · ${version.version}`;
        detail.textContent = version.status;
        item.append(term, detail);
        return item;
      }));
    }
  } catch (error) {
    setText("entries-state", error.message === "forbidden" ? "RÉSERVÉ AU RÔLE OPERATOR" : "INDISPONIBLE");
  }
}

loadDashboard();
// Display only: the server refuses these routes to READ_ONLY anyway (403).
const role = document.querySelector('meta[name="astra-role"]')?.content;
if (role === "OPERATOR" || role === "ADMIN") {
  loadOperations();
} else {
  setText("entries-state", "RÉSERVÉ AU RÔLE OPERATOR");
}
