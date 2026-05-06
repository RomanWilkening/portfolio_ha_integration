/*!
 * Portfolio Valuator – self-discovering Lovelace card and sidebar panel.
 *
 * This module defines two custom elements that share the same renderer:
 *
 *   <portfolio-valuator-card>   – Lovelace card. Drop into any dashboard with
 *                                 ``type: custom:portfolio-valuator-card``.
 *                                 No configuration required; an optional
 *                                 ``title`` / ``compact: true`` is supported.
 *
 *   <portfolio-valuator-panel>  – Sidebar panel auto-registered by the
 *                                 integration. Renders the full overview
 *                                 (portfolios + watchlist + FX + service
 *                                 status) at panel width.
 *
 * Discovery: every entity created by the integration carries
 * ``attributes.integration === "portfolio_valuator"`` plus a ``pv_kind`` tag
 * (``portfolio_market_value`` / ``position_price`` / ``watchlist_price`` /
 * ``fx_rate`` / ``ws_connected`` ...) and ids (``portfolio_id``,
 * ``position_id``, ``watch_id``, ``fx_id``). The renderer simply walks
 * ``hass.states`` and groups by those tags – no per-user setup needed.
 */
const CARD_VERSION = "0.2.0";
const INTEGRATION = "portfolio_valuator";

// ----------------------------------------------------------------- formatting
const fmtMoney = (value, currency) => {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "—";
  }
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "EUR",
      maximumFractionDigits: 2,
    }).format(Number(value));
  } catch (_e) {
    return `${Number(value).toFixed(2)} ${currency || ""}`.trim();
  }
};

const fmtNumber = (value, digits = 4) => {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "—";
  }
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 2,
  });
};

const fmtPct = (value) => {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "—";
  }
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)} %`;
};

const fmtSignedMoney = (value, currency) => {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "—";
  }
  const formatted = fmtMoney(Math.abs(Number(value)), currency);
  return Number(value) >= 0 ? `+${formatted}` : `−${formatted}`;
};

const fmtTimestamp = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(d);
  } catch (_e) {
    return d.toISOString();
  }
};

const sign = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "neutral";
  return Number(n) >= 0 ? "pos" : "neg";
};

const escapeHtml = (s) => {
  if (s === null || s === undefined) return "";
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
};

// =========================================================== data discovery
/**
 * Walk hass.states and group every Portfolio Valuator entity by its
 * structural tags. Returns a normalised model:
 *   {
 *     portfolios: [{ id, name, currency, totals:{mv,cost,pnl,pnl_pct},
 *                    valued_at, missing_fx, positions:[{id,name,...}],
 *                    entityIds:{...} }],
 *     watchlist:  [{ id, label, code, name, price, currency, source, eid }],
 *     fx:         [{ id, code, base, quote, price, source, eid }],
 *     service:    { ws_connected:bool|null, ws_eid, version, lastValuedAt },
 *     present:    bool,
 *   }
 */
function buildModel(hass) {
  const model = {
    portfolios: new Map(),
    watchlist: [],
    fx: [],
    service: { ws_connected: null, ws_eid: null, version: null, lastValuedAt: null },
    present: false,
  };
  if (!hass || !hass.states) return finaliseModel(model);

  const ensurePortfolio = (pid) => {
    if (!model.portfolios.has(pid)) {
      model.portfolios.set(pid, {
        id: pid,
        name: null,
        currency: null,
        totals: {},
        valued_at: null,
        missing_fx: null,
        positions: new Map(),
        entityIds: {},
      });
    }
    return model.portfolios.get(pid);
  };

  const ensurePosition = (pf, posId) => {
    if (!pf.positions.has(posId)) {
      pf.positions.set(posId, {
        id: posId,
        name: null,
        instrument_code: null,
        instrument_isin: null,
        instrument_name: null,
        quantity: null,
        currency: null,
        price: null,
        market_value: null,
        pnl: null,
        pnl_pct: null,
        price_source: null,
        fx_missing: null,
        entityIds: {},
      });
    }
    return pf.positions.get(posId);
  };

  const cleanFriendly = (s, suffixRe) => {
    if (!s) return null;
    return String(s)
      .replace(/^Portfolio:\s*/i, "")
      .replace(/^Watchlist:\s*/i, "")
      .replace(suffixRe || /\s*$/, "")
      .trim() || null;
  };

  for (const eid of Object.keys(hass.states)) {
    const st = hass.states[eid];
    if (!st || !st.attributes) continue;
    const attrs = st.attributes;
    if (attrs.integration !== INTEGRATION) continue;
    model.present = true;
    const kind = attrs.pv_kind;
    const stateNum = Number(st.state);
    const valid = !Number.isNaN(stateNum) && st.state !== "unavailable" && st.state !== "unknown";

    if (kind && kind.startsWith("portfolio_")) {
      const pid = attrs.portfolio_id;
      if (pid === undefined || pid === null) continue;
      const pf = ensurePortfolio(pid);
      switch (kind) {
        case "portfolio_market_value": {
          pf.totals.market_value = valid ? stateNum : null;
          pf.currency = attrs.unit_of_measurement || pf.currency;
          pf.valued_at = attrs.valued_at || pf.valued_at;
          pf.missing_fx = attrs.missing_fx;
          pf.entityIds.market_value = eid;
          pf.name = pf.name || cleanFriendly(attrs.friendly_name, /\s*Market value$/i);
          break;
        }
        case "portfolio_cost_basis":
          pf.totals.cost_basis = valid ? stateNum : null;
          pf.entityIds.cost_basis = eid;
          break;
        case "portfolio_pnl":
          pf.totals.pnl = valid ? stateNum : null;
          pf.entityIds.pnl = eid;
          break;
        case "portfolio_pnl_pct":
          pf.totals.pnl_pct = valid ? stateNum : null;
          pf.entityIds.pnl_pct = eid;
          break;
        case "portfolio_valued_at":
          pf.valued_at = valid ? st.state : (attrs.valued_at || pf.valued_at || st.state);
          pf.entityIds.valued_at = eid;
          break;
        default:
          break;
      }
      continue;
    }

    if (kind && kind.startsWith("position_")) {
      const pid = attrs.portfolio_id;
      const posId = attrs.position_id;
      if (pid === undefined || pid === null || posId === undefined || posId === null) continue;
      const pf = ensurePortfolio(pid);
      const pos = ensurePosition(pf, posId);
      pos.instrument_code = attrs.instrument_code || pos.instrument_code;
      pos.instrument_isin = attrs.instrument_isin || pos.instrument_isin;
      pos.instrument_name = attrs.instrument_name || pos.instrument_name;
      pos.quantity = attrs.quantity ?? pos.quantity;
      pos.currency = attrs.currency || pos.currency;
      pos.price_source = attrs.price_source || pos.price_source;
      pos.fx_missing = attrs.fx_missing ?? pos.fx_missing;
      // Friendly name looks like "Portfolio: X Position foo – Price"; strip suffix.
      const friendly = attrs.friendly_name || "";
      const m = friendly.match(/Position\s+(.+?)\s+[–-]\s+(Price|Market Value|P\/L|P\/L %)/i);
      if (m) pos.name = pos.name || m[1];
      switch (kind) {
        case "position_price":
          pos.price = valid ? stateNum : null;
          pos.entityIds.price = eid;
          if (!pos.currency) pos.currency = attrs.unit_of_measurement;
          break;
        case "position_market_value":
          pos.market_value = valid ? stateNum : null;
          pos.entityIds.market_value = eid;
          break;
        case "position_pnl":
          pos.pnl = valid ? stateNum : null;
          pos.entityIds.pnl = eid;
          break;
        case "position_pnl_pct":
          pos.pnl_pct = valid ? stateNum : null;
          pos.entityIds.pnl_pct = eid;
          break;
        default:
          break;
      }
      continue;
    }

    if (kind === "watchlist_price") {
      model.watchlist.push({
        id: attrs.watch_id,
        label: attrs.label,
        code: attrs.instrument_code,
        name: attrs.instrument_name,
        price: valid ? stateNum : null,
        currency: attrs.unit_of_measurement || "",
        source: attrs.price_source,
        eid,
      });
      continue;
    }

    if (kind === "fx_rate") {
      model.fx.push({
        id: attrs.fx_id,
        code: attrs.code,
        base: attrs.base_currency,
        quote: attrs.quote_currency,
        price: valid ? stateNum : null,
        source: attrs.price_source,
        eid,
      });
      continue;
    }

    if (kind === "ws_connected") {
      model.service.ws_connected = st.state === "on";
      model.service.ws_eid = eid;
      model.service.version = attrs.service_version || model.service.version;
      continue;
    }
  }

  return finaliseModel(model);
}

function finaliseModel(model) {
  const portfolios = Array.from(model.portfolios.values()).map((pf) => {
    const positions = Array.from(pf.positions.values()).sort((a, b) => {
      const an = (a.name || a.instrument_code || `${a.id}`).toLowerCase();
      const bn = (b.name || b.instrument_code || `${b.id}`).toLowerCase();
      return an.localeCompare(bn);
    });
    let lastValuedAt = pf.valued_at;
    return { ...pf, positions, valued_at: lastValuedAt };
  }).sort((a, b) => (a.name || `${a.id}`).localeCompare(b.name || `${b.id}`));

  const watchlist = model.watchlist.sort((a, b) =>
    (a.label || a.code || `${a.id}`).localeCompare(b.label || b.code || `${b.id}`),
  );
  const fx = model.fx.sort((a, b) => (a.code || "").localeCompare(b.code || ""));

  // Aggregate "last valued" across all portfolios for the service banner.
  let lastValuedAt = null;
  let lastValuedAtMs = -Infinity;
  for (const pf of portfolios) {
    if (!pf.valued_at) continue;
    const t = new Date(pf.valued_at).getTime();
    if (!Number.isFinite(t)) continue;
    if (t > lastValuedAtMs) {
      lastValuedAtMs = t;
      lastValuedAt = pf.valued_at;
    }
  }
  model.service.lastValuedAt = lastValuedAt;

  return { ...model, portfolios, watchlist, fx };
}

// =============================================================== rendering
const STYLES = `
  :host { display: block; color: var(--primary-text-color); }
  .pv-root {
    box-sizing: border-box;
    width: 100%;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    font-family: var(--paper-font-body1_-_font-family, "Roboto", sans-serif);
  }
  .pv-section { display: flex; flex-direction: column; gap: 8px; }
  .pv-section-title {
    font-size: 0.85rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
    margin: 4px 4px 0 4px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .pv-section-title .count {
    font-weight: 400;
    opacity: 0.8;
    font-size: 0.8rem;
  }
  .pv-card {
    background: var(--ha-card-background, var(--card-background-color, white));
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, none);
    border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, rgba(0,0,0,0.08)));
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .pv-status-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .pv-status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.85rem;
    background: var(--secondary-background-color, rgba(0,0,0,0.04));
  }
  .pv-status-pill .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--disabled-text-color, #888);
  }
  .pv-status-pill.ok .dot { background: var(--success-color, #2e7d32); }
  .pv-status-pill.bad .dot { background: var(--error-color, #c62828); }
  .pv-status-meta {
    font-size: 0.85rem;
    opacity: 0.75;
  }
  .pv-portfolio-head {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: baseline;
    gap: 8px;
  }
  .pv-portfolio-name {
    font-size: 1.15rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pv-portfolio-stamp {
    font-size: 0.8rem;
    opacity: 0.65;
    white-space: nowrap;
  }
  .pv-portfolio-totals {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    align-items: end;
  }
  .pv-tile {
    display: flex;
    flex-direction: column;
    gap: 2px;
    cursor: pointer;
    border-radius: 8px;
    padding: 8px;
    margin: -8px;
  }
  .pv-tile:hover { background: var(--secondary-background-color, rgba(0,0,0,0.04)); }
  .pv-tile-label {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
  }
  .pv-tile-value {
    font-size: 1.4rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .pv-tile-value.big { font-size: 1.7rem; }
  .pv-tile-sub {
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
    opacity: 0.85;
  }
  .pos { color: var(--success-color, #2e7d32); }
  .neg { color: var(--error-color, #c62828); }
  .neutral { opacity: 0.7; }
  .pv-warning {
    color: var(--warning-color, #f9a825);
    font-size: 0.85rem;
  }
  .pv-positions {
    margin-top: 4px;
    border-top: 1px solid var(--divider-color, rgba(0,0,0,0.08));
    padding-top: 8px;
  }
  .pv-positions-toggle {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 0;
    cursor: pointer;
    user-select: none;
    font-size: 0.85rem;
    opacity: 0.85;
  }
  .pv-positions-toggle .chev {
    transition: transform 120ms ease;
    display: inline-block;
  }
  .pv-positions[data-open="true"] .pv-positions-toggle .chev {
    transform: rotate(90deg);
  }
  .pv-positions-table {
    display: none;
    margin-top: 8px;
    overflow-x: auto;
  }
  .pv-positions[data-open="true"] .pv-positions-table { display: block; }
  table.pv-table {
    width: 100%;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
    font-size: 0.9rem;
  }
  table.pv-table th, table.pv-table td {
    padding: 6px 8px;
    text-align: right;
    border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.06));
  }
  table.pv-table th:first-child, table.pv-table td:first-child {
    text-align: left;
  }
  table.pv-table th {
    font-weight: 500;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.65;
  }
  table.pv-table tr:last-child td { border-bottom: none; }
  table.pv-table tr.clickable { cursor: pointer; }
  table.pv-table tr.clickable:hover { background: var(--secondary-background-color, rgba(0,0,0,0.04)); }
  .pv-pos-name { display: flex; flex-direction: column; gap: 2px; }
  .pv-pos-sub { font-size: 0.75rem; opacity: 0.65; }
  .pv-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }
  .pv-tile-card {
    background: var(--secondary-background-color, rgba(0,0,0,0.03));
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    cursor: pointer;
  }
  .pv-tile-card:hover { background: var(--divider-color, rgba(0,0,0,0.06)); }
  .pv-tile-card .head {
    display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  }
  .pv-tile-card .label {
    font-weight: 500;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .pv-tile-card .code {
    font-size: 0.75rem; opacity: 0.65;
  }
  .pv-tile-card .val {
    font-size: 1.25rem; font-weight: 500; font-variant-numeric: tabular-nums;
  }
  .pv-tile-card .src {
    font-size: 0.75rem; opacity: 0.6;
  }
  .pv-empty {
    padding: 12px;
    opacity: 0.7;
    text-align: center;
    font-size: 0.9rem;
  }
  .pv-loading {
    display: flex; align-items: center; justify-content: center;
    padding: 32px; opacity: 0.6;
  }
  /* Compact variant (legacy card mode) */
  .pv-root.compact .pv-section.compact-only { display: flex; }
  .pv-root.compact .pv-section:not(.compact-only) { display: none; }
  .pv-compact-row {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 12px;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08));
    cursor: pointer;
  }
  .pv-compact-row:last-child { border-bottom: none; }
  .pv-compact-row .name {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .pv-compact-row .num { font-variant-numeric: tabular-nums; }
  .pv-compact-head {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 12px;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08));
  }
`;

const STR_DE = {
  portfolios: "Portfolios",
  watchlist: "Watchlist",
  fx: "FX-Kurse",
  service: "Dienst",
  marketValue: "Marktwert",
  costBasis: "Einstand",
  pnl: "P/L",
  pnlPct: "P/L %",
  positions: "Positionen",
  showPositions: "Positionen anzeigen",
  hidePositions: "Positionen verbergen",
  noData: "Noch keine Daten — wartet auf erste Aktualisierung des Portfolio Valuators.",
  noPortfolios: "Keine Portfolios konfiguriert.",
  noWatchlist: "Keine Watchlist-Einträge.",
  noFx: "Keine FX-Kurse.",
  wsConnected: "WebSocket verbunden",
  wsDisconnected: "WebSocket getrennt",
  wsUnknown: "WebSocket-Status unbekannt",
  serviceVersion: "Dienst-Version",
  lastValuation: "Letzte Bewertung",
  qty: "Menge",
  price: "Preis",
  source: "Quelle",
  fxMissing: "FX fehlt",
  base: "Basis",
  quote: "Quote",
  rate: "Kurs",
  pair: "Paar",
  instrument: "Instrument",
};

function renderHtml(model, opts) {
  const { compact } = opts;
  if (!model.present) {
    return `
      <div class="pv-root ${compact ? "compact" : ""}">
        <div class="pv-card pv-loading">${STR_DE.noData}</div>
      </div>
    `;
  }

  // ---------------- compact mode (legacy card behaviour) ----------------
  if (compact) {
    const rows = model.portfolios.map((pf) => {
      const cur = pf.currency || "EUR";
      const s = sign(pf.totals.pnl);
      return `
        <div class="pv-compact-row" data-entity="${escapeHtml(pf.entityIds.market_value || "")}">
          <div class="name">${escapeHtml(pf.name || `Portfolio ${pf.id}`)}</div>
          <div class="num">${escapeHtml(fmtMoney(pf.totals.market_value, cur))}</div>
          <div class="num ${s}">${escapeHtml(fmtSignedMoney(pf.totals.pnl, cur))}</div>
          <div class="num ${s}">${escapeHtml(fmtPct(pf.totals.pnl_pct))}</div>
        </div>
      `;
    }).join("");
    const empty = model.portfolios.length === 0
      ? `<div class="pv-empty">${STR_DE.noPortfolios}</div>` : "";
    return `
      <div class="pv-root compact">
        <div class="pv-section compact-only">
          <div class="pv-card">
            <div class="pv-compact-head">
              <div>${STR_DE.portfolios}</div>
              <div>${STR_DE.marketValue}</div>
              <div>${STR_DE.pnl}</div>
              <div>%</div>
            </div>
            ${rows}${empty}
          </div>
        </div>
      </div>
    `;
  }

  // ---------------- full overview ----------------
  const svc = model.service;
  const svcCls = svc.ws_connected === true ? "ok" : svc.ws_connected === false ? "bad" : "";
  const svcLabel = svc.ws_connected === true
    ? STR_DE.wsConnected
    : svc.ws_connected === false
      ? STR_DE.wsDisconnected
      : STR_DE.wsUnknown;
  const serviceCard = `
    <div class="pv-section">
      <div class="pv-section-title">${STR_DE.service}</div>
      <div class="pv-card pv-status-row">
        <div class="pv-status-pill ${svcCls}" data-entity="${escapeHtml(svc.ws_eid || "")}">
          <span class="dot"></span><span>${svcLabel}</span>
        </div>
        ${svc.version ? `<div class="pv-status-meta">${STR_DE.serviceVersion}: ${escapeHtml(svc.version)}</div>` : ""}
        ${svc.lastValuedAt ? `<div class="pv-status-meta">${STR_DE.lastValuation}: ${escapeHtml(fmtTimestamp(svc.lastValuedAt))}</div>` : ""}
      </div>
    </div>
  `;

  const portfoliosHtml = model.portfolios.length === 0
    ? `<div class="pv-card pv-empty">${STR_DE.noPortfolios}</div>`
    : model.portfolios.map((pf) => renderPortfolioCard(pf)).join("");

  const watchlistHtml = model.watchlist.length === 0
    ? `<div class="pv-card pv-empty">${STR_DE.noWatchlist}</div>`
    : `<div class="pv-card"><div class="pv-grid">${model.watchlist.map(renderWatchTile).join("")}</div></div>`;

  const fxHtml = model.fx.length === 0
    ? `<div class="pv-card pv-empty">${STR_DE.noFx}</div>`
    : `<div class="pv-card">${renderFxTable(model.fx)}</div>`;

  return `
    <div class="pv-root">
      ${serviceCard}
      <div class="pv-section">
        <div class="pv-section-title">${STR_DE.portfolios} <span class="count">(${model.portfolios.length})</span></div>
        ${portfoliosHtml}
      </div>
      <div class="pv-section">
        <div class="pv-section-title">${STR_DE.watchlist} <span class="count">(${model.watchlist.length})</span></div>
        ${watchlistHtml}
      </div>
      <div class="pv-section">
        <div class="pv-section-title">${STR_DE.fx} <span class="count">(${model.fx.length})</span></div>
        ${fxHtml}
      </div>
    </div>
  `;
}

function renderPortfolioCard(pf) {
  const cur = pf.currency || "EUR";
  const s = sign(pf.totals.pnl);
  const tile = (label, valueHtml, subHtml, eid, big) => `
    <div class="pv-tile" data-entity="${escapeHtml(eid || "")}">
      <div class="pv-tile-label">${label}</div>
      <div class="pv-tile-value ${big ? "big" : ""}">${valueHtml}</div>
      ${subHtml ? `<div class="pv-tile-sub">${subHtml}</div>` : ""}
    </div>
  `;
  const positionsHtml = pf.positions.length === 0
    ? ""
    : `
      <div class="pv-positions" data-open="false">
        <div class="pv-positions-toggle">
          <span class="chev">▶</span>
          <span class="label-show">${STR_DE.showPositions}</span>
          <span class="count">(${pf.positions.length})</span>
        </div>
        <div class="pv-positions-table">
          <table class="pv-table">
            <thead>
              <tr>
                <th>${STR_DE.instrument}</th>
                <th>${STR_DE.qty}</th>
                <th>${STR_DE.price}</th>
                <th>${STR_DE.marketValue}</th>
                <th>${STR_DE.pnl}</th>
                <th>%</th>
              </tr>
            </thead>
            <tbody>
              ${pf.positions.map((pos) => renderPositionRow(pos, cur)).join("")}
            </tbody>
          </table>
        </div>
      </div>
    `;
  return `
    <div class="pv-card">
      <div class="pv-portfolio-head">
        <div class="pv-portfolio-name">${escapeHtml(pf.name || `Portfolio ${pf.id}`)}</div>
        <div class="pv-portfolio-stamp">${pf.valued_at ? escapeHtml(fmtTimestamp(pf.valued_at)) : ""}</div>
      </div>
      <div class="pv-portfolio-totals">
        ${tile(STR_DE.marketValue, escapeHtml(fmtMoney(pf.totals.market_value, cur)), "", pf.entityIds.market_value, true)}
        ${tile(STR_DE.costBasis, escapeHtml(fmtMoney(pf.totals.cost_basis, cur)), "", pf.entityIds.cost_basis, false)}
        ${tile(STR_DE.pnl, `<span class="${s}">${escapeHtml(fmtSignedMoney(pf.totals.pnl, cur))}</span>`,
               `<span class="${s}">${escapeHtml(fmtPct(pf.totals.pnl_pct))}</span>`, pf.entityIds.pnl, false)}
      </div>
      ${pf.missing_fx && pf.missing_fx.length ? `<div class="pv-warning">${STR_DE.fxMissing}: ${escapeHtml(pf.missing_fx.join(", "))}</div>` : ""}
      ${positionsHtml}
    </div>
  `;
}

function renderPositionRow(pos, fallbackCurrency) {
  const cur = pos.currency || fallbackCurrency || "EUR";
  const s = sign(pos.pnl);
  const eid = pos.entityIds.price || pos.entityIds.market_value || pos.entityIds.pnl || "";
  const subParts = [];
  if (pos.instrument_code) subParts.push(escapeHtml(pos.instrument_code));
  if (pos.instrument_isin) subParts.push(escapeHtml(pos.instrument_isin));
  if (pos.price_source) subParts.push(escapeHtml(pos.price_source));
  return `
    <tr class="clickable" data-entity="${escapeHtml(eid)}">
      <td>
        <div class="pv-pos-name">
          <span>${escapeHtml(pos.name || pos.instrument_name || pos.instrument_code || `#${pos.id}`)}</span>
          ${subParts.length ? `<span class="pv-pos-sub">${subParts.join(" · ")}</span>` : ""}
        </div>
      </td>
      <td>${escapeHtml(fmtNumber(pos.quantity, 4))}</td>
      <td>${escapeHtml(fmtMoney(pos.price, cur))}</td>
      <td>${escapeHtml(fmtMoney(pos.market_value, cur))}</td>
      <td class="${s}">${escapeHtml(fmtSignedMoney(pos.pnl, cur))}</td>
      <td class="${s}">${escapeHtml(fmtPct(pos.pnl_pct))}</td>
    </tr>
  `;
}

function renderWatchTile(it) {
  const label = it.label || it.name || it.code || `#${it.id}`;
  return `
    <div class="pv-tile-card" data-entity="${escapeHtml(it.eid)}">
      <div class="head">
        <div class="label">${escapeHtml(label)}</div>
        ${it.code ? `<div class="code">${escapeHtml(it.code)}</div>` : ""}
      </div>
      <div class="val">${escapeHtml(fmtMoney(it.price, it.currency))}</div>
      ${it.source ? `<div class="src">${STR_DE.source}: ${escapeHtml(it.source)}</div>` : ""}
    </div>
  `;
}

function renderFxTable(rows) {
  return `
    <table class="pv-table">
      <thead>
        <tr>
          <th>${STR_DE.pair}</th>
          <th>${STR_DE.base}</th>
          <th>${STR_DE.quote}</th>
          <th>${STR_DE.rate}</th>
          <th>${STR_DE.source}</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((r) => `
          <tr class="clickable" data-entity="${escapeHtml(r.eid)}">
            <td>${escapeHtml(r.code || `${r.base}/${r.quote}`)}</td>
            <td>${escapeHtml(r.base || "")}</td>
            <td>${escapeHtml(r.quote || "")}</td>
            <td>${escapeHtml(fmtNumber(r.price, 6))}</td>
            <td>${escapeHtml(r.source || "")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

// =========================================================== shared element
class PortfolioValuatorOverviewBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._compact = false;
    this._lastSig = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  // Lovelace lifecycle (panels don't use this, but harmless).
  setConfig(config) {
    this._config = config || {};
    this._compact = !!this._config.compact;
    this._lastSig = null;
    this._render();
  }

  // Lovelace card-helper used by the section/grid layouts.
  getCardSize() {
    return 6;
  }

  _render() {
    if (!this._hass) return;
    const model = buildModel(this._hass);
    // Cheap signature so we don't reflow on unrelated state changes.
    const sig = JSON.stringify({
      pf: model.portfolios.map((p) => [p.id, p.totals, p.valued_at, p.positions.map((x) => [x.id, x.price, x.market_value, x.pnl, x.pnl_pct, x.quantity])]),
      wl: model.watchlist.map((w) => [w.id, w.price, w.source]),
      fx: model.fx.map((f) => [f.id, f.price, f.source]),
      svc: model.service,
      compact: this._compact,
      present: model.present,
    });
    if (sig === this._lastSig) return;
    this._lastSig = sig;

    this.shadowRoot.innerHTML = `<style>${STYLES}</style>${renderHtml(model, { compact: this._compact })}`;
    this._wireInteractions();
  }

  _wireInteractions() {
    const root = this.shadowRoot;
    // More-info on any element that carries data-entity.
    root.querySelectorAll("[data-entity]").forEach((el) => {
      const eid = el.getAttribute("data-entity");
      if (!eid) return;
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const event = new Event("hass-more-info", { bubbles: true, composed: true });
        event.detail = { entityId: eid };
        this.dispatchEvent(event);
      });
    });
    // Collapsible position tables.
    root.querySelectorAll(".pv-positions").forEach((wrap) => {
      const toggle = wrap.querySelector(".pv-positions-toggle");
      if (!toggle) return;
      const showLabel = toggle.querySelector(".label-show");
      toggle.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const open = wrap.getAttribute("data-open") === "true";
        wrap.setAttribute("data-open", open ? "false" : "true");
        if (showLabel) {
          showLabel.textContent = open ? STR_DE.showPositions : STR_DE.hidePositions;
        }
      });
    });
  }
}

// ----------------------------------------------------------- Lovelace card
class PortfolioValuatorCard extends PortfolioValuatorOverviewBase {
  setConfig(config) {
    // Backward compatibility: previous versions accepted `entities` and a
    // `title`. The card is now self-discovering, so those are accepted but
    // ignored. ``compact: true`` keeps the old single-row summary look.
    super.setConfig(config);
  }

  static getStubConfig() {
    return { compact: false };
  }
}

// ----------------------------------------------------------- Sidebar panel
//
// Home Assistant injects ``hass``, ``narrow`` and ``panel`` properties on the
// custom element registered as a panel. We only need ``hass``.
class PortfolioValuatorPanel extends PortfolioValuatorOverviewBase {
  constructor() {
    super();
    this._compact = false;
  }

  set narrow(_v) { /* no-op; layout adapts via CSS */ }
  set panel(_v) { /* no-op */ }
  set route(_v) { /* no-op */ }
}

// ----------------------------------------------------------- registration
if (!customElements.get("portfolio-valuator-card")) {
  customElements.define("portfolio-valuator-card", PortfolioValuatorCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "portfolio-valuator-card",
    name: "Portfolio Valuator",
    description:
      "Self-discovering overview of all Portfolio Valuator portfolios, positions, watchlist and FX rates.",
    preview: false,
    documentationURL:
      "https://github.com/RomanWilkening/portfolio_ha_integration#frontend-card",
  });
  // eslint-disable-next-line no-console
  console.info(
    `%c portfolio-valuator-card %c ${CARD_VERSION} `,
    "color: white; background: #1976d2; font-weight: 700;",
    "color: #1976d2; background: white; font-weight: 700;",
  );
}

if (!customElements.get("portfolio-valuator-panel")) {
  customElements.define("portfolio-valuator-panel", PortfolioValuatorPanel);
}
