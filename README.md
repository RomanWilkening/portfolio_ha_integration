# Portfolio Valuator — Home Assistant Integration

Native [Home Assistant](https://www.home-assistant.io/) custom integration for the
[`portfolio_valuator`](https://github.com/RomanWilkening/portfolio_valuator) service.

It connects directly to the Valuator's REST + **WebSocket** API for live, push-based
updates of portfolio valuations, watchlist prices and FX rates — replacing the older
MQTT-Discovery path for HA usage. (MQTT remains optional in the Valuator service for
other consumers.)

## Features

- **Live updates via WebSocket** — values change in HA the instant the Valuator gets a
  new quote, no polling needed. Falls back to REST polling automatically when the WS
  is unreachable.
- **One device per Portfolio** with sensors for total Market Value, Cost Basis, P/L,
  P/L %, last valuation timestamp, plus per-position Price / Market Value / P/L / P/L %.
- **One device per Watchlist item** with a live Price sensor.
- **One FX Rates device** containing one sensor per configured FX pair.
- **Long-term statistics ready** — all monetary sensors are exposed with
  `state_class: measurement`, so HA's statistics layer keeps 5-minute aggregates
  forever, while the recorder retains every individual quote for the configured
  retention window.
- **Optional API token** (`X-API-Key` header / `?api_key=…` for WebSocket).
- **Auto-reload on structure changes** — when portfolios, positions, watchlist items
  or FX pairs are added or removed in the Valuator, the integration reloads its
  entities automatically (requires Valuator with `structure_changed` push event).
- German + English translations.

## Requirements

- Home Assistant `2024.4.0` or newer.
- A reachable Portfolio Valuator instance (default port `8000`).

## Installation

### Via HACS (recommended)

1. In HACS go to **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/RomanWilkening/portfolio_ha_integration` as **Integration**.
3. Install **Portfolio Valuator** and restart Home Assistant.

### Manual

Copy the `custom_components/portfolio_valuator` directory into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Portfolio
Valuator** and provide:

| Field | Description |
|---|---|
| Host | Hostname or IP of the Valuator service |
| Port | Default `8000` |
| Use HTTPS / WSS | Enable for TLS-terminated reverse-proxy setups |
| Verify SSL certificate | Disable only for self-signed certs |
| API token (optional) | Required if the Valuator was set up with an `api_token` |

The integration will connect to `/api/health` (or fall back to `/api/portfolios`) to
verify the connection.

## Entities

For a portfolio named *Depot* the following entities are created (translation keys
omitted; HA will auto-prefix the device name):

- `sensor.portfolio_depot_market_value`
- `sensor.portfolio_depot_cost_basis`
- `sensor.portfolio_depot_profit_loss`
- `sensor.portfolio_depot_profit_loss_pct`
- `sensor.portfolio_depot_valued_at`
- For every position in the portfolio: price, market value, P/L and P/L %.

Watchlist items become individual devices (`Watchlist: <label>`) with a single
`Price` sensor each.

FX rates land on a single `Portfolio Valuator – FX Rates` device, one sensor per pair.

## How push updates work

The integration opens a single WebSocket to `/ws` per configured Valuator instance
and processes the following frame types:

- `snapshot` (initial dump on connect)
- `valuations` (full re-bewertung after a quote tick)
- `quote` (single instrument update — merged into the watchlist cache)
- `structure_changed` (CRUD on portfolios / positions / watchlist / FX → triggers
  an entry reload so new devices appear and removed ones disappear without a HA
  restart). Requires Valuator support.

A 25-second WebSocket heartbeat plus exponential-backoff reconnect (2 → 60 s) keep
the link alive across reverse-proxy idle timeouts. While the WebSocket is healthy
the periodic REST poll is still used (every 60 s) as a safety net for fields not
covered by every WS frame.

## Long-term statistics

All monetary sensors carry `state_class: measurement` (and a `device_class` of
`monetary` where appropriate). This means:

- The HA **recorder** stores every individual state change for its configured
  retention window — perfect for detailed apex-charts / mini-graph views over the
  last few days.
- The HA **long-term statistics** engine stores 5-minute mean/min/max aggregates
  *forever*, which is what the standard *Statistics graph* card and the Energy /
  Statistics dashboard use.

A single entity therefore covers both the detailed and the long-term view.

## Troubleshooting

Enable debug logs:

```yaml
logger:
  default: info
  logs:
    custom_components.portfolio_valuator: debug
```

Common issues:

- *Cannot connect* — verify host/port and that `curl http://<host>:<port>/api/portfolios`
  works from the HA host.
- *Invalid auth* — the Valuator has an `api_token` configured; copy it into the
  integration options.

## License

MIT
