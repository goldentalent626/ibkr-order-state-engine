# -*- coding: utf-8 -*-
"""
IB Gateway adapter  --  Quiet Machines (by Cristian Lungu)

Thin, replaceable layer that connects the pure OrderBook engine (order_state.py)
to a live Interactive Brokers session via ib_async (the maintained successor to
ib_insync; the API is identical, so either import works).

All the fragile parts -- dedup, partial-fill aggregation, monotonic state,
reconciliation -- live in order_state.py and are unit-tested offline. This file
only does I/O: connect, auto-reconnect, forward IB events into the engine, and
periodically reconcile against the broker's own snapshot.

Boundaries respected (per the brief):
  * Paper / offline first -- host+port default to IB Gateway PAPER (127.0.0.1:4002).
  * Read + reconcile before acting; no live-capital assumptions.
  * Never weakens safety: reconnect REPLAYS executions, which the engine dedups.

Run (paper):  start IB Gateway in paper mode with the API enabled, then
    python ib_gateway.py
"""
from __future__ import annotations
import asyncio
import logging
from order_state import OrderBook

try:                                    # maintained fork first, fall back to original
    from ib_async import IB, Stock, MarketOrder
except ImportError:                     # pragma: no cover
    from ib_insync import IB, Stock, MarketOrder

log = logging.getLogger("qm.ibkr")

PAPER_HOST, PAPER_PORT = "127.0.0.1", 4002        # IB Gateway paper; TWS paper = 7497


class IBKRGateway:
    def __init__(self, host: str = PAPER_HOST, port: int = PAPER_PORT, client_id: int = 17):
        self.host, self.port, self.client_id = host, port, client_id
        self.ib = IB()
        self.book = OrderBook()
        self._wire_events()

    # ---- connection lifecycle with auto-reconnect ----
    async def connect(self, max_backoff: int = 30) -> None:
        backoff = 1
        while True:
            try:
                await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
                log.info("connected to IB %s:%s", self.host, self.port)
                await self._resync()            # pull open orders + positions after (re)connect
                return
            except Exception as e:              # gateway down / restarting
                log.warning("connect failed (%s); retrying in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    def _wire_events(self) -> None:
        self.ib.execDetailsEvent += self._on_exec
        self.ib.orderStatusEvent += self._on_status
        self.ib.disconnectedEvent += self._on_disconnect

    # ---- IB callbacks -> engine (idempotent by construction) ----
    def _on_exec(self, trade, fill) -> None:
        # execId is globally unique; the engine ignores replays after a reconnect.
        was_new = self.book.on_execution(
            order_id=trade.order.orderId,
            exec_id=fill.execution.execId,
            qty=fill.execution.shares,
            price=fill.execution.price,
        )
        if was_new:
            o = self.book.orders[trade.order.orderId]
            log.info("fill %s %s @ %s  (filled %s/%s, state=%s)",
                     o.action, fill.execution.shares, fill.execution.price,
                     o.filled_qty, o.total_qty, o.state.name)

    def _on_status(self, trade) -> None:
        self.book.on_status(trade.order.orderId, trade.orderStatus.status)

    def _on_disconnect(self) -> None:
        log.warning("disconnected -- scheduling reconnect")
        asyncio.ensure_future(self.connect())

    # ---- reconciliation against the broker's own truth ----
    async def _resync(self) -> None:
        for t in self.ib.openTrades():                       # re-register live orders locally
            self.book.place(t.order.orderId, t.contract.symbol,
                            t.order.action, t.order.totalQuantity)
        broker_open = {t.order.orderId for t in self.ib.openTrades()}
        broker_pos = {p.contract.symbol: p.position for p in self.ib.positions()}
        drift = self.book.reconcile(broker_open, broker_pos)
        if drift:
            log.error("RECONCILIATION DRIFT:\n  " + "\n  ".join(drift))
        else:
            log.info("reconciled clean: %d live order(s)", len(broker_open))

    # ---- example intent (paper only) ----
    def buy(self, symbol: str, qty: float):
        contract = Stock(symbol, "SMART", "USD")
        trade = self.ib.placeOrder(contract, MarketOrder("BUY", qty))
        self.book.place(trade.order.orderId, symbol, "BUY", qty)
        return trade


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    gw = IBKRGateway()
    await gw.connect()
    log.info("live positions: %s", gw.book.net_positions())
    await gw.ib.runAsync()          # keep processing events


if __name__ == "__main__":
    asyncio.run(_demo())
