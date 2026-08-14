# -*- coding: utf-8 -*-
"""
Live order-capture demo  --  Quiet Machines
Connects our engine to IBKR TWS paper as clientId 0 (so it can see orders
placed manually in TWS), waits while you place a paper order, then feeds the
REAL order + REAL fills reported by IBKR into the pure OrderBook engine and
reconciles engine state against the broker's own snapshot.
Read-only: it observes, it never places.
"""
import logging
from ib_gateway import IBKRGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

gw = IBKRGateway(port=7497, client_id=0)          # clientId 0 sees manual TWS orders
gw.ib.disconnectedEvent -= gw._on_disconnect      # one-shot: no auto-reconnect

print("Connecting (clientId 0) to observe manual orders ...", flush=True)
gw.ib.connect(gw.host, gw.port, clientId=gw.client_id, timeout=30, readonly=True)
print("[OK] connected. account:", list(gw.ib.managedAccounts()), flush=True)
print("\n>>> PLACE YOUR PAPER ORDER IN TWS NOW  --  listening ~180s <<<\n", flush=True)

gw.ib.sleep(180)                                  # listen window

# feed the REAL orders + fills reported by IBKR into OUR engine
for t in gw.ib.trades():
    gw.book.place(t.order.orderId, t.contract.symbol, t.order.action, t.order.totalQuantity)
    for f in t.fills:
        gw.book.on_execution(t.order.orderId, f.execution.execId,
                             f.execution.shares, f.execution.price)
    gw.book.on_status(t.order.orderId, t.orderStatus.status)

print("=" * 60)
print(" OUR ENGINE reconstructed state from real IBKR data")
print("=" * 60)
if not gw.book.orders:
    print("   (no orders seen this window -- was one placed in time?)")
for oid, o in gw.book.orders.items():
    print(f"   order {oid}: {o.symbol} {o.action} filled {o.filled_qty}/{o.total_qty}"
          f" @ avg {o.avg_fill_price}  state={o.state.name}")

broker_open = {t.order.orderId for t in gw.ib.openTrades()}
broker_pos = {p.contract.symbol: round(p.position, 4) for p in gw.ib.positions()}
drift = gw.book.reconcile(broker_open, broker_pos)
print("   engine positions :", gw.book.net_positions())
print("   broker positions :", broker_pos)
print("   reconciliation   :", "CLEAN (engine matches broker)" if not drift else drift)

gw.ib.disconnect()
print("[OK] done.")
