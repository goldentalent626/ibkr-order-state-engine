# IBKR Order-State Engine

**A small, tested Python core for reliable order handling on the Interactive Brokers API — built to survive duplicate callbacks, reconnects, and out-of-order partial fills without corrupting state.**

Most IB trading code breaks in the same places: a duplicated `execDetails`, a reconnect that replays fills, a partial fill that arrives out of order, or a stale `orderStatus` that rewinds a finished order. This project isolates that risk into a pure, unit-tested engine and keeps the live IB layer thin and replaceable.

> Built as a focused reliability demo — paper/offline first, no live capital. The point is not a strategy; it's an execution core you can trust.

---

## What it does

- **Idempotent executions** — every fill is keyed by its unique `execId`; replays (after a reconnect or a duplicated callback) are no-ops.
- **Truth from executions, not status** — filled quantity is summed from real executions, never trusted blindly from a single `orderStatus`. State only moves forward.
- **Partial-fill aggregation** — accumulates fills and computes a quantity-weighted average price.
- **Reconciliation** — compares local order/position truth against the broker's own `openOrders` / `positions` snapshot and reports drift instead of silently overwriting.
- **Auto-reconnect** — the gateway layer reconnects with backoff and re-syncs on every (re)connection.

## Architecture

| File | Role | Needs IB? |
|---|---|---|
| `order_state.py` | Pure order-state engine (dedup, aggregation, reconciliation). No I/O. | No |
| `ib_gateway.py` | Thin adapter: connects the engine to a live session via `ib_async` (successor to `ib_insync`), with auto-reconnect. | Live paper |
| `test_harness.py` | Simulates the failure modes and asserts state integrity — runs offline, deterministically. | No |

Keeping the fragile logic free of any broker dependency is deliberate: reliability is **verified in CI**, not hoped for on a live line.

## Test results

```
$ python test_harness.py
 [OK] 1. duplicate execution ignored
 [OK] 2. reconnect replay is idempotent
 [OK] 3a. partially filled state
 [OK] 3b. completes + quantity-weighted avg price
 [OK] 4. stale 'Submitted' after fill ignored
 [OK] 4b. 'Filled' status without executions rejected
 [OK] 4c. stray 'Cancelled' after fill ignored
 [OK] 5. reconciliation flags missing order + position drift
 8/8 checks passed
```

## Validated live against IBKR

Beyond the offline suite, the engine has been run against a **live Interactive Brokers paper account** over the TWS API — strictly read-only (it observes and reconciles; it never places orders):

- **Connect + reconcile** — attaches to the paper session, pulls the broker's own open-orders / positions snapshot, and reconciles clean.
- **Real order capture** — a market order placed *by hand* in TWS was captured end-to-end by the read-only engine and reconstructed independently:

```
 OUR ENGINE reconstructed state from real IBKR data
 order: EUR SELL  filled 20000/20000 @ avg 1.15539  state=FILLED
 engine positions : {'EUR': -20000.0}
 broker positions : {'EUR': -20000.0}
 reconciliation   : CLEAN (engine matches broker)
```

A person placed the order; the engine tracked every transition (Submitted → Filled), summed the fill, derived the position, and matched the broker exactly — the read-only, human-in-the-loop model this kind of system needs.

## Run it

```bash
# offline integrity checks — no account, no broker
python test_harness.py

# live paper (read-only) — start TWS or IB Gateway in PAPER mode with the API enabled, then:
pip install ib_async
python run_live_tws.py       # connect + reconcile against the paper account (TWS port 7497)
python run_live_listen.py    # observe orders placed by hand in TWS, read-only
```
