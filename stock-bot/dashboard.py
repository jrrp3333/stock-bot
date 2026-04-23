import sqlite3
import os
from pathlib import Path
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

from trade_logger import DB_PATH, initialize_schema
from optimization_agents import get_optimization_snapshot, get_ensemble_weight_state

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
REAL_MONEY_START_BUDGET = float(os.getenv("REAL_MONEY_START_BUDGET", "25.0"))
DASHBOARD_DEBUG = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"

app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trade Bot Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #f7f3ef;
      --panel: #fffdfb;
      --ink: #2c1f1b;
      --muted: #76635c;
      --good: #238254;
      --bad: #b44337;
      --accent: #0f5e9c;
      --border: #eadfd7;
      --hero-a: #fff5ec;
      --hero-b: #f5f7ff;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: "Manrope", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 85% 12%, #fcded4 0%, rgba(252,222,212,0) 38%),
        radial-gradient(circle at 8% 92%, #d7e8ff 0%, rgba(215,232,255,0) 36%),
        var(--bg);
    }

    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }

    .hero {
      background: linear-gradient(120deg, var(--hero-a), var(--hero-b));
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 26px;
      box-shadow: 0 10px 28px rgba(60, 30, 24, 0.08);
      margin-bottom: 18px;
      position: relative;
      overflow: hidden;
    }

    .hero::after {
      content: "";
      position: absolute;
      right: -70px;
      top: -70px;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(15,94,156,0.15) 0%, rgba(15,94,156,0) 70%);
      pointer-events: none;
    }

    .hero h1 {
      margin: 0;
      font-family: "Fraunces", Georgia, serif;
      font-size: 2rem;
      letter-spacing: 0.01em;
    }

    .hero p {
      margin: 8px 0 0;
      color: var(--muted);
      max-width: 72ch;
    }

    .hero-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }

    .pill {
      background: rgba(255,255,255,0.7);
      border: 1px solid var(--border);
      border-radius: 999px;
      font-size: 0.78rem;
      color: #5a4a43;
      padding: 6px 10px;
      backdrop-filter: blur(2px);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
      box-shadow: 0 4px 14px rgba(58, 36, 29, 0.06);
      animation: rise 0.6s ease both;
      transition: transform 0.18s ease, box-shadow 0.2s ease;
    }

    .card:hover {
      transform: translateY(-2px);
      box-shadow: 0 8px 20px rgba(58, 36, 29, 0.1);
    }

    .card.balance-showcase {
      grid-column: span 2;
      background: linear-gradient(135deg, #1d8e5a 0%, #28966c 50%, #33a37e 100%);
      border: 1px solid rgba(255,255,255,0.35);
      box-shadow: 0 10px 26px rgba(35, 130, 84, 0.28);
      color: white;
    }

    .card.balance-showcase h3 {
      color: rgba(255,255,255,0.85);
    }

    .card.balance-showcase .metric {
      color: white;
      font-size: 2.2rem;
      text-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }

    .card h3 {
      margin: 0 0 6px;
      font-size: 0.82rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .metric {
      margin: 0;
      font-size: 1.5rem;
      font-weight: 700;
    }

    .good { color: var(--good); }
    .bad { color: var(--bad); }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
      box-shadow: 0 4px 14px rgba(58, 36, 29, 0.06);
      margin-bottom: 18px;
      animation: rise 0.7s ease both;
    }

    .panel h2 {
      margin: 0 0 10px;
      font-family: "Fraunces", Georgia, serif;
      font-size: 1.2rem;
      letter-spacing: 0.01em;
    }

    .table-wrap {
      overflow-x: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      min-width: 900px;
    }

    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }

    th {
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
    }

    tbody tr:nth-child(even) {
      background: rgba(245, 234, 228, 0.32);
    }

    tbody tr:hover {
      background: rgba(15, 94, 156, 0.07);
    }

    .notes {
      max-width: 340px;
      white-space: normal;
      color: #3d4637;
    }

    @keyframes rise {
      from { transform: translateY(10px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }

    @media (max-width: 900px) {
      .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .card.balance-showcase {
        grid-column: span 2;
      }
    }

    @media (max-width: 560px) {
      .wrap { padding: 14px; }
      .hero h1 { font-size: 1.4rem; }
      .grid {
        grid-template-columns: 1fr;
      }
      .card.balance-showcase {
        grid-column: span 1;
      }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Autonomous Trade Dashboard</h1>
      <p>Live view of account health, executed trades, optimization intelligence, and risk posture in one place.</p>
      <div class="hero-meta">
        <span class="pill">Mode: {{ account['mode']|upper }}</span>
        <span class="pill">Universe: {{ stats.total_trades }} closed trades tracked</span>
        <span class="pill">Refresh: manual browser refresh</span>
      </div>
    </section>

    <section class="grid">
      <article class="card balance-showcase"><h3>💰 Account Balance</h3><p class="metric">${{ '%.0f'|format(account['equity']) }}</p></article>
      <article class="card"><h3>Total Trades</h3><p class="metric">{{ stats.total_trades }}</p></article>
      <article class="card"><h3>Win Rate</h3><p class="metric">{{ stats.win_rate }}%</p></article>
      <article class="card"><h3>Avg PnL</h3><p class="metric {{ 'good' if stats.avg_pnl >= 0 else 'bad' }}">{{ stats.avg_pnl }}%</p></article>
      <article class="card"><h3>Cumulative PnL</h3><p class="metric {{ 'good' if stats.total_pnl >= 0 else 'bad' }}">{{ stats.total_pnl }}%</p></article>
    </section>

    <section class="panel">
      <h2>Account Balance</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Mode</th>
              <th>Equity</th>
              <th>Cash</th>
              <th>Buying Power</th>
              <th>Effective Equity Budget</th>
              <th>Effective Cash Budget</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{{ account['mode'] }}</td>
              <td>{{ '%.2f'|format(account['equity']) }}</td>
              <td>{{ '%.2f'|format(account['cash']) }}</td>
              <td>{{ '%.2f'|format(account['buying_power']) }}</td>
              <td>{{ '%.2f'|format(account['effective_equity']) }}</td>
              <td>{{ '%.2f'|format(account['effective_cash']) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p style="margin:10px 0 0;color:#667060;">
        Live mode budget cap: ${{ '%.2f'|format(account['real_money_start_budget']) }} (applies only when PAPER_TRADING=false).
      </p>
    </section>

    <section class="panel">
      <h2>Optimization Recommendations</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Current</th>
              <th>Recommended</th>
              <th>Confidence</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Take Profit %</td>
              <td>{{ opt.take_profit.current }}</td>
              <td>{{ opt.take_profit.recommended }}</td>
              <td>{{ '%.0f'|format(opt.take_profit.confidence * 100) }}%</td>
              <td>{{ opt.take_profit.rationale }}</td>
            </tr>
            <tr>
              <td>Stop Loss %</td>
              <td>{{ opt.stop_loss.current }}</td>
              <td>{{ opt.stop_loss.recommended }}</td>
              <td>{{ '%.0f'|format(opt.stop_loss.confidence * 100) }}%</td>
              <td>{{ opt.stop_loss.rationale }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Signal Source Ranking (Recent)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Signal Source</th>
              <th>Trades</th>
              <th>Win Rate</th>
              <th>Avg PnL</th>
            </tr>
          </thead>
          <tbody>
            {% if signal_sources %}
              {% for s in signal_sources %}
              <tr>
                <td>{{ s['signal_source'] }}</td>
                <td>{{ s['count'] }}</td>
                <td>{{ s['win_rate'] }}%</td>
                <td class="{{ 'good' if s['avg_pnl'] >= 0 else 'bad' }}">{{ s['avg_pnl'] }}%</td>
              </tr>
              {% endfor %}
            {% else %}
              <tr>
                <td colspan="4">No signal-source history yet.</td>
              </tr>
            {% endif %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Ensemble Weights (Weekly Auto-Reweight)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th>Weight</th>
              <th>Avg Return %</th>
              <th>Hit Rate %</th>
              <th>Samples</th>
            </tr>
          </thead>
          <tbody>
            {% if weight_rows %}
              {% for row in weight_rows %}
              <tr>
                <td>{{ row['agent'] }}</td>
                <td>{{ row['weight'] }}</td>
                <td class="{{ 'good' if row['avg_return_pct'] >= 0 else 'bad' }}">{{ row['avg_return_pct'] }}</td>
                <td>{{ row['hit_rate_pct'] }}</td>
                <td>{{ row['samples'] }}</td>
              </tr>
              {% endfor %}
            {% else %}
              <tr>
                <td colspan="5">No backtest weight data yet.</td>
              </tr>
            {% endif %}
          </tbody>
        </table>
      </div>
      <p style="margin:10px 0 0;color:#667060;">
        Last reweight: {{ weight_state.get('last_reweight_date') or 'never' }}
      </p>
    </section>

    <section class="panel">
      <h2>PnL by Trade</h2>
      <canvas id="pnlChart" height="80"></canvas>
    </section>

    <section class="panel">
      <h2>Open Positions (Alpaca)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Qty</th>
              <th>Avg Entry</th>
              <th>Current</th>
              <th>Market Value</th>
              <th>Unrealized PnL %</th>
            </tr>
          </thead>
          <tbody>
            {% if positions %}
              {% for p in positions %}
              <tr>
                <td>{{ p['symbol'] }}</td>
                <td>{{ p['qty'] }}</td>
                <td>{{ '%.2f'|format(p['avg_entry_price']) }}</td>
                <td>{{ '%.2f'|format(p['current_price']) }}</td>
                <td>{{ '%.2f'|format(p['market_value']) }}</td>
                <td class="{{ 'good' if p['unrealized_plpc'] >= 0 else 'bad' }}">{{ '%.2f'|format(p['unrealized_plpc']) }}</td>
              </tr>
              {% endfor %}
            {% else %}
              <tr>
                <td colspan="6">No open positions.</td>
              </tr>
            {% endif %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Recent Entry Orders</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Timestamp</th>
              <th>Ticker</th>
              <th>Entry Price</th>
              <th>Qty</th>
              <th>Signal</th>
              <th>Order ID</th>
            </tr>
          </thead>
          <tbody>
            {% if entries %}
              {% for e in entries %}
              <tr>
                <td>{{ e['id'] }}</td>
                <td>{{ e['timestamp'] }}</td>
                <td>{{ e['ticker'] }}</td>
                <td>{{ '%.2f'|format(e['entry_price']) }}</td>
                <td>{{ e['qty'] }}</td>
                <td>{{ e['signal_source'] }}</td>
                <td>{{ e['order_id'] or '' }}</td>
              </tr>
              {% endfor %}
            {% else %}
              <tr>
                <td colspan="7">No entry logs yet.</td>
              </tr>
            {% endif %}
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Recent Closed Trades</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Timestamp</th>
              <th>Ticker</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Qty</th>
              <th>Signal</th>
              <th>PnL %</th>
              <th>Failure Notes</th>
            </tr>
          </thead>
          <tbody>
            {% for t in trades %}
            <tr>
              <td>{{ t['id'] }}</td>
              <td>{{ t['timestamp'] }}</td>
              <td>{{ t['ticker'] }}</td>
              <td>{{ '%.2f'|format(t['entry_price']) }}</td>
              <td>{{ '%.2f'|format(t['exit_price']) }}</td>
              <td>{{ t['qty'] }}</td>
              <td>{{ t['signal_source'] }}</td>
              <td class="{{ 'good' if t['pnl_percent'] >= 0 else 'bad' }}">{{ '%.2f'|format(t['pnl_percent']) }}</td>
              <td class="notes">{{ t['failure_notes'] or '' }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
  </div>

  <script>
    const labels = {{ labels|tojson }};
    const values = {{ values|tojson }};
    const colors = values.map(v => v >= 0 ? "#1f8f4c" : "#b9362f");

    new Chart(document.getElementById("pnlChart"), {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "PnL %",
          data: values,
          backgroundColor: colors,
          borderRadius: 6
        }]
      },
      options: {
        animation: { duration: 700 },
        plugins: {
          legend: { display: false }
        },
        scales: {
          y: {
            title: { display: true, text: "Percent" },
            grid: { color: "rgba(80,90,80,0.1)" }
          },
          x: {
            grid: { display: false }
          }
        }
      }
    });
  </script>
</body>
</html>
"""


def _fetch_trades(limit: int = 100):
    db_file = Path(DB_PATH)
    if not db_file.exists():
        return []

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, ticker, entry_price, exit_price, qty, signal_source, pnl_percent, failure_notes
            FROM trades
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _build_stats(trades):
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
        }

    pnls = [float(t["pnl_percent"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "total_trades": len(trades),
        "win_rate": round((wins / len(trades)) * 100, 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2),
        "total_pnl": round(sum(pnls), 2),
    }


def _fetch_entries(limit: int = 100):
    db_file = Path(DB_PATH)
    if not db_file.exists():
        return []

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, ticker, entry_price, qty, signal_source, order_id
            FROM trade_entries
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_open_positions():
    if not API_KEY or not SECRET_KEY:
        return []

    try:
        client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)
        positions = client.get_all_positions()
        normalized = []
        for p in positions:
            normalized.append(
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "market_value": float(p.market_value),
                    "unrealized_plpc": float(p.unrealized_plpc) * 100,
                }
            )
        return normalized
    except Exception:
        return []


def _fetch_account_balance():
    if not API_KEY or not SECRET_KEY:
        return {
            "mode": "unavailable",
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "effective_equity": 0.0,
            "effective_cash": 0.0,
            "real_money_start_budget": REAL_MONEY_START_BUDGET,
        }

    try:
        client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)
        acct = client.get_account()
        equity = float(getattr(acct, "equity", 0.0) or 0.0)
        cash = float(getattr(acct, "cash", 0.0) or 0.0)
        buying_power = float(getattr(acct, "buying_power", 0.0) or 0.0)

        effective_equity = equity if PAPER_TRADING else min(equity, REAL_MONEY_START_BUDGET)
        effective_cash = cash if PAPER_TRADING else min(cash, REAL_MONEY_START_BUDGET)

        return {
            "mode": "paper" if PAPER_TRADING else "live",
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "effective_equity": effective_equity,
            "effective_cash": effective_cash,
            "real_money_start_budget": REAL_MONEY_START_BUDGET,
        }
    except Exception:
        return {
            "mode": "error",
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "effective_equity": 0.0,
            "effective_cash": 0.0,
            "real_money_start_budget": REAL_MONEY_START_BUDGET,
        }


@app.route("/")
def index():
    initialize_schema()
    trades = _fetch_trades(limit=100)
    entries = _fetch_entries(limit=100)
    positions = _fetch_open_positions()
    account = _fetch_account_balance()
    stats = _build_stats(trades)

    optimization_snapshot = get_optimization_snapshot(lookback_days=90)
    opt = optimization_snapshot.get("recommendations", {})
    signal_sources = optimization_snapshot.get("signal_sources", [])
    weight_state = get_ensemble_weight_state()
    weights = weight_state.get("weights", {})
    score_map = ((weight_state.get("backtest") or {}).get("agent_scores") or {})

    weight_rows = []
    for agent, weight in weights.items():
        score = score_map.get(agent, {})
        weight_rows.append(
            {
                "agent": agent,
                "weight": round(float(weight), 4),
                "avg_return_pct": round(float(score.get("avg_return_pct", 0.0)), 4),
                "hit_rate_pct": round(float(score.get("hit_rate_pct", 0.0)), 2),
                "samples": int(score.get("samples", 0)),
            }
        )

    chart_trades = list(reversed(trades[:30]))
    labels = [f"{t['ticker']} #{t['id']}" for t in chart_trades]
    values = [round(float(t["pnl_percent"]), 2) for t in chart_trades]

    return render_template_string(
        HTML,
        trades=trades,
        entries=entries,
        positions=positions,
        account=account,
        stats=stats,
        labels=labels,
        values=values,
        opt=opt,
        signal_sources=signal_sources,
        weight_state=weight_state,
        weight_rows=weight_rows,
    )


@app.route("/api/summary")
def api_summary():
    trades = _fetch_trades(limit=500)
    stats = _build_stats(trades)
    return jsonify(stats)


@app.route("/api/optimization")
def api_optimization():
    return jsonify(get_optimization_snapshot(lookback_days=90))


@app.route("/api/weights")
def api_weights():
    return jsonify(get_ensemble_weight_state())


@app.route("/api/account")
def api_account():
    return jsonify(_fetch_account_balance())


@app.route("/api/health")
def api_health():
    account = _fetch_account_balance()
    return jsonify(
        {
            "status": "ok",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
            "mode": account.get("mode"),
        }
    )


if __name__ == "__main__":
    initialize_schema()
    app.run(host="127.0.0.1", port=5000, debug=DASHBOARD_DEBUG)

