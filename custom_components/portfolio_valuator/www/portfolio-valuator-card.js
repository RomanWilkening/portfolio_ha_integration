/*!
 * Portfolio Valuator – minimal Lovelace card.
 *
 * Displays one row per portfolio with market value, P/L (signed) and P/L %.
 * Reads its data straight from the integration's sensors via their attributes;
 * no extra REST calls.
 *
 * YAML usage:
 *   type: custom:portfolio-valuator-card
 *   title: Depots
 *   # Optional explicit list of portfolio entities. If omitted, the card auto-
 *   # discovers every sensor whose entity_id ends with "_market_value" and whose
 *   # attribute "integration" === "portfolio_valuator".
 *   entities:
 *     - sensor.portfolio_depot_market_value
 *     - sensor.portfolio_etf_market_value
 */
const CARD_VERSION = "0.1.0";

const fmtMoney = (value, currency) => {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "—";
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

const fmtPct = (value) => {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(2)} %`;
};

class PortfolioValuatorCard extends HTMLElement {
  setConfig(config) {
    this._config = { title: "Portfolios", ...(config || {}) };
    if (!this._root) {
      this.attachShadow({ mode: "open" });
      this._root = this.shadowRoot;
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  _resolveEntities() {
    if (this._config && Array.isArray(this._config.entities) && this._config.entities.length) {
      return this._config.entities;
    }
    if (!this._hass) return [];
    return Object.keys(this._hass.states).filter((eid) => {
      if (!eid.startsWith("sensor.")) return false;
      if (!eid.endsWith("_market_value")) return false;
      const st = this._hass.states[eid];
      return st && st.attributes && st.attributes.integration === "portfolio_valuator";
    });
  }

  _render() {
    if (!this._hass || !this._config) return;
    const entityIds = this._resolveEntities();

    const rows = entityIds
      .map((eid) => {
        const mv = this._hass.states[eid];
        if (!mv) return null;
        const pid = mv.attributes.portfolio_id;
        const baseId = eid.replace(/_market_value$/, "");
        const pl = this._hass.states[`${baseId}_profit_loss`];
        const plPct = this._hass.states[`${baseId}_profit_loss_pct`];
        const currency = mv.attributes.unit_of_measurement || mv.attributes.currency || "EUR";
        const name = mv.attributes.friendly_name
          ? mv.attributes.friendly_name.replace(/^Portfolio:\s*/, "").replace(/\s*Market value$/i, "")
          : `Portfolio ${pid ?? ""}`;
        const plNum = pl ? Number(pl.state) : NaN;
        const sign = !Number.isNaN(plNum) ? (plNum >= 0 ? "pos" : "neg") : "neutral";
        return { eid, name, mv: mv.state, currency, pl: pl ? pl.state : null, plPct: plPct ? plPct.state : null, sign };
      })
      .filter(Boolean);

    const empty = rows.length === 0
      ? `<div class="empty">Keine Portfolio-Sensoren gefunden.</div>`
      : "";

    const body = rows
      .map(
        (r) => `
        <div class="row" data-entity="${r.eid}">
          <div class="name">${r.name}</div>
          <div class="value">${fmtMoney(r.mv, r.currency)}</div>
          <div class="pl ${r.sign}">${fmtMoney(r.pl, r.currency)}</div>
          <div class="pct ${r.sign}">${fmtPct(r.plPct)}</div>
        </div>`,
      )
      .join("");

    this._root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; }
        .title { font-size: 1.1rem; font-weight: 500; margin-bottom: 8px; }
        .header, .row {
          display: grid;
          grid-template-columns: 1fr auto auto auto;
          gap: 12px;
          align-items: center;
          padding: 6px 0;
          border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08));
        }
        .header { font-size: 0.8rem; opacity: 0.7; text-transform: uppercase; }
        .row:last-child { border-bottom: none; }
        .row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .row .value, .row .pl, .row .pct { font-variant-numeric: tabular-nums; }
        .pos { color: var(--success-color, #2e7d32); }
        .neg { color: var(--error-color, #c62828); }
        .neutral { opacity: 0.7; }
        .empty { padding: 12px 0; opacity: 0.7; }
        .row:hover { cursor: pointer; background: var(--secondary-background-color, transparent); }
      </style>
      <ha-card>
        <div class="title">${this._config.title}</div>
        <div class="header">
          <div>Portfolio</div><div>Marktwert</div><div>P/L</div><div>%</div>
        </div>
        ${body}
        ${empty}
      </ha-card>
    `;

    this._root.querySelectorAll(".row").forEach((el) => {
      el.addEventListener("click", () => {
        const eid = el.getAttribute("data-entity");
        if (!eid) return;
        const ev = new Event("hass-more-info", { bubbles: true, composed: true });
        ev.detail = { entityId: eid };
        this.dispatchEvent(ev);
      });
    });
  }
}

if (!customElements.get("portfolio-valuator-card")) {
  customElements.define("portfolio-valuator-card", PortfolioValuatorCard);
  // Surface the card in the Lovelace card-picker.
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "portfolio-valuator-card",
    name: "Portfolio Valuator",
    description: "Compact overview of all Portfolio Valuator portfolios.",
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
