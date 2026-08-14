# -*- coding: utf-8 -*-
"""
Order-state integrity harness  --  Quiet Machines (by Cristian Lungu)

Throws the exact failure modes an Interactive Brokers connection produces at the
order-state engine and proves state stays correct. Runs offline, no broker, no
account -- so reliability is verified deterministically in CI, not hoped for.

Covered failure modes:
  1. Duplicate execDetails callbacks        (network ret/replay)
  2. Full reconnect replay of all fills      (IB Gateway drop + resubscribe)
  3. Out-of-order / multiple partial fills   (large order worked over time)
  4. Stale/late orderStatus after fill       (must not rewind state)
  5. Broker reconciliation drift detection
"""
from order_state import OrderBook, OrderState

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


# 1. Duplicate execution callbacks -------------------------------------------
def t_duplicate_execs():
    b = OrderBook()
    b.place(1, "AAPL", "BUY", 100)
    new1 = b.on_execution(1, "ex-1", 60, 190.00)
    dup = b.on_execution(1, "ex-1", 60, 190.00)      # exact duplicate callback
    o = b.orders[1]
    check("1. duplicate execution ignored",
          new1 is True and dup is False and o.filled_qty == 60,
          f"filled={o.filled_qty} (expected 60)")


# 2. Reconnect replays every fill again --------------------------------------
def t_reconnect_replay():
    b = OrderBook()
    b.place(2, "MSFT", "BUY", 50)
    fills = [("ex-a", 20, 410.0), ("ex-b", 30, 411.0)]
    for eid, q, p in fills:
        b.on_execution(2, eid, q, p)
    # --- connection drops, IB Gateway replays the SAME executions on resubscribe ---
    for eid, q, p in fills:
        b.on_execution(2, eid, q, p)
    o = b.orders[2]
    check("2. reconnect replay is idempotent",
          o.filled_qty == 50 and o.state == OrderState.FILLED,
          f"filled={o.filled_qty} (expected 50), state={o.state.name}")


# 3. Out-of-order partial fills + weighted avg price -------------------------
def t_partial_fills():
    b = OrderBook()
    b.place(3, "TSLA", "SELL", 100)
    b.on_execution(3, "p2", 25, 250.0)
    b.on_execution(3, "p1", 50, 240.0)               # arrives after p2
    check("3a. partially filled state",
          b.orders[3].state == OrderState.PARTIALLY_FILLED and b.orders[3].filled_qty == 75,
          f"filled={b.orders[3].filled_qty} (expected 75)")
    b.on_execution(3, "p3", 25, 260.0)               # completes it
    o = b.orders[3]
    expected_avg = round((25*250 + 50*240 + 25*260) / 100, 6)   # 248.5
    check("3b. completes + quantity-weighted avg price",
          o.state == OrderState.FILLED and o.avg_fill_price == expected_avg,
          f"avg={o.avg_fill_price} (expected {expected_avg}), state={o.state.name}")


# 4. Stale status after a fill must not rewind -------------------------------
def t_stale_status():
    b = OrderBook()
    b.place(4, "NVDA", "BUY", 10)
    b.on_execution(4, "e1", 10, 120.0)               # fully filled
    b.on_status(4, "Submitted")                      # a late/stale status shows up
    o = b.orders[4]
    check("4. stale 'Submitted' after fill ignored",
          o.state == OrderState.FILLED,
          f"state={o.state.name} (expected FILLED)")
    # and a status claiming Filled while nothing executed is rejected:
    b2 = OrderBook(); b2.place(5, "AMD", "BUY", 10)
    b2.on_status(5, "Filled")
    check("4b. 'Filled' status without executions rejected",
          b2.orders[5].state == OrderState.SUBMITTED,
          f"state={b2.orders[5].state.name} (expected SUBMITTED)")


# 5. Reconciliation detects drift --------------------------------------------
def t_reconcile():
    b = OrderBook()
    b.place(6, "AAPL", "BUY", 100); b.on_execution(6, "f1", 100, 190.0)   # filled -> +100 AAPL
    b.place(7, "MSFT", "BUY", 10)                                          # still live locally
    # broker says: order 7 NOT open, and AAPL position is only 90 (drift!)
    drift = b.reconcile(broker_open_order_ids=set(), broker_positions={"AAPL": 90})
    check("5. reconciliation flags missing order + position drift",
          any("order 7" in d for d in drift) and any("AAPL" in d for d in drift),
          f"drift={drift}")


def main():
    for t in (t_duplicate_execs, t_reconnect_replay, t_partial_fills, t_stale_status, t_reconcile):
        t()
    print("=" * 66)
    print(" IBKR order-state integrity harness  --  Quiet Machines")
    print("=" * 66)
    for name, status, detail in results:
        mark = "[OK]" if status == PASS else "[XX]"
        print(f" {mark} {name}")
        if status == FAIL:
            print(f"       -> {detail}")
    passed = sum(1 for _, s, _ in results if s == PASS)
    print("-" * 66)
    print(f" {passed}/{len(results)} checks passed")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
