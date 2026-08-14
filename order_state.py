# -*- coding: utf-8 -*-
"""
Order-state engine  --  Quiet Machines (by Cristian Lungu)
The quiet machines that run your business.

Pure-Python core for an Interactive Brokers execution system. It is intentionally
decoupled from any live IB connection so the hard part -- keeping order state
correct under duplicate callbacks, reconnects, and out-of-order partial fills --
can be tested deterministically, offline, with zero broker access.

Design rules that keep state correct (the failure cases IBKR throws at you):
  * Executions are idempotent: each fill carries a unique execId; replaying it
    (after a reconnect, or a duplicated execDetails callback) is a no-op.
  * orderStatus callbacks can arrive late, duplicated, or out of order, so filled
    quantity is derived from summed executions, never trusted blindly from a
    single status message. Status can only move forward, never backward.
  * Partial fills accumulate; avg price is a quantity-weighted mean of real execs.
  * Reconciliation compares local truth against the broker's openOrders/positions
    and reports drift instead of silently overwriting.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum


class OrderState(IntEnum):
    """Monotonic lifecycle -- state can only advance, guarding against stale callbacks."""
    PENDING = 0
    SUBMITTED = 1
    PARTIALLY_FILLED = 2
    FILLED = 3
    CANCELLED = 4          # terminal
    REJECTED = 5           # terminal

    @property
    def is_terminal(self) -> bool:
        return self in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED)


@dataclass
class Execution:
    exec_id: str
    qty: float
    price: float


@dataclass
class Order:
    order_id: int
    symbol: str
    action: str                 # "BUY" / "SELL"
    total_qty: float
    state: OrderState = OrderState.PENDING
    _execs: dict[str, Execution] = field(default_factory=dict)   # exec_id -> Execution (dedup)

    # -- derived truth, always computed from real executions --
    @property
    def filled_qty(self) -> float:
        return round(sum(e.qty for e in self._execs.values()), 8)

    @property
    def remaining_qty(self) -> float:
        return round(self.total_qty - self.filled_qty, 8)

    @property
    def avg_fill_price(self) -> float:
        f = self.filled_qty
        if f == 0:
            return 0.0
        return round(sum(e.qty * e.price for e in self._execs.values()) / f, 6)

    def _advance(self, new: OrderState) -> None:
        # Terminal states are final; otherwise state only moves forward. This
        # guards against a stray Cancelled/Submitted mutating a Filled order.
        if self.state.is_terminal:
            return
        if new > self.state:
            self.state = new


class OrderBook:
    """Holds all live orders and applies broker events idempotently."""

    def __init__(self) -> None:
        self.orders: dict[int, Order] = {}

    # ---- intent ----
    def place(self, order_id: int, symbol: str, action: str, qty: float) -> Order:
        if order_id in self.orders:                       # idempotent re-place after reconnect
            return self.orders[order_id]
        o = Order(order_id, symbol, action.upper(), qty, OrderState.SUBMITTED)
        self.orders[order_id] = o
        return o

    # ---- broker callbacks (may duplicate / reorder / replay) ----
    def on_execution(self, order_id: int, exec_id: str, qty: float, price: float) -> bool:
        """Apply a fill. Returns True if it was new, False if it was a duplicate."""
        o = self.orders.get(order_id)
        if o is None:
            return False
        if exec_id in o._execs:                           # <-- the critical dedup
            return False
        o._execs[exec_id] = Execution(exec_id, qty, price)
        if o.remaining_qty <= 1e-9:
            o._advance(OrderState.FILLED)
        else:
            o._advance(OrderState.PARTIALLY_FILLED)
        return True

    def on_status(self, order_id: int, status: str) -> None:
        """Apply an orderStatus string. Fills are NOT trusted from here, only lifecycle hints."""
        o = self.orders.get(order_id)
        if o is None:
            return
        mapping = {
            "Submitted": OrderState.SUBMITTED, "PreSubmitted": OrderState.SUBMITTED,
            "Filled": OrderState.FILLED, "Cancelled": OrderState.CANCELLED,
            "ApiCancelled": OrderState.CANCELLED, "Inactive": OrderState.REJECTED,
        }
        target = mapping.get(status)
        if target is None:
            return
        # Only trust "Filled" from status if executions actually cover the order.
        if target == OrderState.FILLED and o.remaining_qty > 1e-9:
            return
        o._advance(target)

    # ---- reconciliation vs broker snapshot ----
    def reconcile(self, broker_open_order_ids: set[int],
                  broker_positions: dict[str, float]) -> list[str]:
        """Compare local state to a broker snapshot; return human-readable drift lines."""
        drift: list[str] = []
        for oid, o in self.orders.items():
            live_local = not o.state.is_terminal
            if live_local and oid not in broker_open_order_ids:
                drift.append(f"order {oid} ({o.symbol}) is live locally but MISSING at broker")
            if not live_local and oid in broker_open_order_ids:
                drift.append(f"order {oid} ({o.symbol}) is terminal locally but STILL OPEN at broker")
        local_pos = self.net_positions()
        for sym in set(local_pos) | set(broker_positions):
            if abs(local_pos.get(sym, 0) - broker_positions.get(sym, 0)) > 1e-9:
                drift.append(f"position {sym}: local {local_pos.get(sym, 0)} vs broker "
                             f"{broker_positions.get(sym, 0)}")
        return drift

    def net_positions(self) -> dict[str, float]:
        pos: dict[str, float] = {}
        for o in self.orders.values():
            sign = 1 if o.action == "BUY" else -1
            pos[o.symbol] = round(pos.get(o.symbol, 0) + sign * o.filled_qty, 8)
        return {s: q for s, q in pos.items() if abs(q) > 1e-9}
