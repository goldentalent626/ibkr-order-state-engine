# -*- coding: utf-8 -*-
"""
Live connectivity check  --  Quiet Machines
Connects the SAME engine (order_state.py + ib_gateway.py) to a real IBKR
TWS paper session on 127.0.0.1:7497, reconciles local state against the
broker's own snapshot, and prints the result. Read-only: places nothing.
"""
import logging
from ib_async import util
from ib_gateway import IBKRGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

gw = IBKRGateway(port=7497)                     # TWS paper socket
gw.ib.disconnectedEvent -= gw._on_disconnect    # one-shot: don't auto-reconnect on our own close

print("=" * 60)
print(" Connecting engine -> IBKR TWS paper (127.0.0.1:7497) ...")
print("=" * 60)

try:
    # readonly=True matches the TWS 'Read-Only API' setting, so the library
    # skips write-access requests and there is no 30s timeout.
    gw.ib.connect(gw.host, gw.port, clientId=gw.client_id, timeout=30, readonly=True)
except Exception as e:
    print("CONNECT FAILED:", repr(e))
    print("-> check: API 'Enable Socket Clients' ON, port 7497, and the TWS accept popup.")
    raise SystemExit(1)

print("\n[OK] CONNECTED to IBKR.")
print("     account(s):", list(gw.ib.managedAccounts()))
gw.ib.sleep(1)                                  # let TWS push the initial snapshot

# reconcile OUR engine's view against the broker's real snapshot (ib_gateway._resync)
util.run(gw._resync())

print("\n--- broker snapshot ---")
print("     open orders     :", len(gw.ib.openTrades()))
print("     broker positions:", {p.contract.symbol: p.position for p in gw.ib.positions()})
print("--- engine view ---")
print("     tracked orders  :", len(gw.book.orders))
print("     engine positions:", gw.book.net_positions())

gw.ib.disconnect()
print("\n[OK] reconciled clean and disconnected. Engine <-> IBKR link WORKS.")
