from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any, Optional

from core.constants import TICK_SIZE
from core.enums import DataQuality, OrderSide, OrderStatus
from core.interfaces import DataAdapter, InstrumentInfo, OrderRequest, OrderResult, TickSnapshot, TradingAdapter
from core.time_utils import next_trading_day
from .fail_fast_warn import degrade_once, warn_once

from .clock import SimulatedClock
from .store import MarketDataStore

logger = logging.getLogger("backtest.adapters")
LOT_SIZE = 100


def _next_weekday(d: date) -> date:
    out = d + timedelta(days=1)
    while out.weekday() >= 5:
        out = out + timedelta(days=1)
    return out


def _next_trading_date(d: date) -> date:
    try:
        ymd = d.strftime("%Y%m%d")
        out = next_trading_day(ymd, 1)
        return datetime.strptime(out, "%Y%m%d").date()
    except Exception:
        return _next_weekday(d)


class BacktestDataAdapter(DataAdapter):
    def __init__(self, *, store: MarketDataStore, clock: SimulatedClock) -> None:
        self._store = store
        self._clock = clock
        self._snapshot_cache_now: Optional[datetime] = None
        self._snapshot_cache: dict[str, TickSnapshot] = {}
        self._daily_bars_cache_day: Optional[date] = None
        self._daily_bars_cache: dict[tuple[str, bool], list[Any]] = {}

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        now = self._clock.now()
        code_key = str(etf_code or "").strip().upper()
        if self._snapshot_cache_now != now:
            self._snapshot_cache_now = now
            self._snapshot_cache.clear()
        cached = self._snapshot_cache.get(code_key)
        if cached is not None:
            return cached
        snap = self._store.tick_snapshot(code=etf_code, now=now)
        if snap is None:
            last = float(self._store.mark_price(code=etf_code, now=now, prefer_tick=False))
            warn_once(
                f"bt_snapshot_missing:{str(etf_code)}:{now.date().isoformat()}",
                (
                    "Backtest snapshot missing tick row; using mark price fallback. "
                    f"etf={etf_code} now={now.isoformat(timespec='seconds')} last={float(last):.6f}"
                ),
                logger_name="backtest.adapters",
            )
            logger.debug(
                "snapshot fallback | etf=%s now=%s fallback_last=%.6f",
                str(etf_code),
                now.isoformat(timespec="seconds"),
                float(last),
            )
            out = TickSnapshot(
                timestamp=now,
                last_price=float(last),
                volume=0,
                amount=0.0,
                ask1_price=float(last),
                bid1_price=float(last),
                ask1_vol=0,
                bid1_vol=0,
                iopv=None,
                stock_status=0,
                data_quality=DataQuality.MISSING,
            )
            self._snapshot_cache[code_key] = out
            return out

        tick_row, cum_volume, cum_amount = snap
        last = float(tick_row.last_price)
        tick = float(TICK_SIZE)
        ask1_raw = float(tick_row.ask1_price)
        bid1_raw = float(tick_row.bid1_price)
        ask1 = float(ask1_raw) if float(ask1_raw) > 0 else float(last + tick if last > 0 else 0.0)
        bid1 = float(bid1_raw) if float(bid1_raw) > 0 else float(max(0.0, last - tick))
        ask1_vol = int(max(0, int(tick_row.ask1_vol)))
        bid1_vol = int(max(0, int(tick_row.bid1_vol)))
        out = TickSnapshot(
            timestamp=now,
            last_price=float(last),
            volume=int(cum_volume),
            amount=float(cum_amount),
            ask1_price=float(ask1),
            bid1_price=float(bid1),
            ask1_vol=int(ask1_vol),
            bid1_vol=int(bid1_vol),
            iopv=(None if tick_row.iopv is None else float(tick_row.iopv)),
            stock_status=int(tick_row.stock_status),
            data_quality=DataQuality.OK,
        )
        self._snapshot_cache[code_key] = out
        return out

    def get_bars(self, etf_code: str, period: str, count: int) -> list:
        now = self._clock.now()
        p = str(period).lower()
        if p == "1d":
            include_today = bool(now.time() >= datetime.strptime("15:00", "%H:%M").time())
            if self._daily_bars_cache_day != now.date():
                self._daily_bars_cache_day = now.date()
                self._daily_bars_cache.clear()
            key = (str(etf_code or "").strip().upper(), bool(include_today))
            cached = self._daily_bars_cache.get(key)
            if cached is None:
                cached = list(self._store.daily_bars(code=etf_code, now=now, count=0, include_today=include_today))
                self._daily_bars_cache[key] = list(cached)
            if int(count) <= 0:
                return list(cached)
            return list(cached[-int(count) :])
        if p in {"1m", "m1"}:
            return self._store.minute_bars(code=etf_code, now=now, count=int(count))
        msg = f"unsupported period for backtest: {period}"
        degrade_once(
            f"bt_unsupported_period:{str(period).lower()}",
            f"Backtest adapter got unsupported period and will raise. period={period}",
            logger_name="backtest.adapters",
        )
        logger.error("%s", msg)
        raise RuntimeError(msg)

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        now = self._clock.now()
        prev_close = self._store.previous_close(code=etf_code, day=now.date())
        if prev_close is None or float(prev_close) <= 0:
            prev_close = self._store.mark_price(code=etf_code, now=now)
            warn_once(
                f"bt_prev_close_fallback:{str(etf_code)}:{now.date().isoformat()}",
                (
                    "Backtest instrument prev_close unavailable; fallback to mark price. "
                    f"etf={etf_code} now={now.isoformat(timespec='seconds')}"
                ),
                logger_name="backtest.adapters",
            )
            logger.debug(
                "instrument prev_close fallback | etf=%s now=%s fallback=%.6f",
                str(etf_code),
                now.isoformat(timespec="seconds"),
                float(prev_close or 0.0),
            )
        pc = float(prev_close or 0.0)
        if pc <= 0:
            degrade_once(
                f"bt_prev_close_non_positive:{str(etf_code)}",
                f"Backtest instrument prev_close is non-positive; limits may be unusable. etf={etf_code} prev_close={pc}",
                logger_name="backtest.adapters",
            )
        return InstrumentInfo(
            etf_code=str(etf_code),
            instrument_name=str(etf_code),
            prev_close=float(pc),
            limit_up=float(pc * 1.10),
            limit_down=float(max(0.0, pc * 0.90)),
            price_tick=float(TICK_SIZE),
        )

    def subscribe_quote(self, etf_code: str, callback: Any) -> None:
        _ = etf_code
        _ = callback
        return None

    def get_auction_volume(self, etf_code: str, date: str) -> float:
        _ = etf_code
        _ = date
        return 0.0


@dataclass
class _Lot:
    qty: int
    sellable_date: date


@dataclass
class _OrderRecord:
    order_id: int
    request: OrderRequest
    submitted_at: datetime
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_qty: int = 0
    avg_price: Optional[float] = None
    fee: float = 0.0
    error: str = ""
    executed: bool = False


class BacktestTradingAdapter(TradingAdapter):
    def __init__(
        self,
        *,
        clock: SimulatedClock,
        initial_cash: float,
        fee_rate: float = 0.000085,
        enable_t0: bool = False,
    ) -> None:
        self._clock = clock
        self._cash = float(initial_cash)
        self._fee_rate = float(max(0.0, fee_rate))
        self._enable_t0 = bool(enable_t0)

        self._next_order_id = 1
        self._orders: dict[int, _OrderRecord] = {}
        self._lots: dict[str, list[_Lot]] = {}
        self._fills: list[dict[str, object]] = []
        self._frozen = False
        self._freeze_reason = ""
        self._positions_cache_day: Optional[date] = None
        self._positions_cache_revision = -1
        self._positions_cache: list[dict[str, object]] = []
        self._lots_revision = 0
        self._logger = logging.getLogger("backtest.adapters.trading")
        self._logger.debug(
            "trading adapter init | initial_cash=%.6f fee_rate=%.8f enable_t0=%s",
            float(self._cash),
            float(self._fee_rate),
            bool(self._enable_t0),
        )

    @staticmethod
    def _replace_request_qty(req: OrderRequest, qty: int) -> OrderRequest:
        return OrderRequest(
            etf_code=str(req.etf_code),
            side=req.side,
            quantity=int(qty),
            order_type=req.order_type,
            price=float(req.price),
            tif=req.tif,
            strategy_name=str(req.strategy_name or ""),
            remark=str(req.remark or ""),
        )

    @property
    def cash(self) -> float:
        return float(self._cash)

    def fills(self) -> list[dict[str, object]]:
        return [dict(x) for x in self._fills]

    def _invalidate_positions_cache(self) -> None:
        self._lots_revision += 1
        self._positions_cache_day = None
        self._positions_cache_revision = -1
        self._positions_cache = []

    @staticmethod
    def _clone_positions_payload(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        return [dict(x) for x in rows]

    def apply_price_factor(self, *, etf_code: str, price_factor: float) -> bool:
        factor = float(price_factor)
        if factor <= 0:
            return False
        code = str(etf_code)
        lots = self._lots.get(code, [])
        if not lots:
            return False
        changed = False
        for lot in lots:
            new_qty = int(round(float(lot.qty) / factor))
            if new_qty != int(lot.qty):
                lot.qty = int(new_qty)
                changed = True
        self._lots[code] = [x for x in lots if int(x.qty) > 0]
        if changed:
            self._invalidate_positions_cache()
        return bool(changed)

    def _total_qty(self, etf_code: str) -> int:
        return int(sum(max(0, int(x.qty)) for x in self._lots.get(str(etf_code), [])))

    def _sellable_qty(self, etf_code: str) -> int:
        td = self._clock.now().date()
        qty = 0
        for lot in self._lots.get(str(etf_code), []):
            if int(lot.qty) <= 0:
                continue
            if td >= lot.sellable_date:
                qty += int(lot.qty)
        return int(qty)

    def _consume_sellable_lots(self, *, etf_code: str, qty: int) -> int:
        left = int(max(0, qty))
        if left <= 0:
            return 0
        td = self._clock.now().date()
        lots = self._lots.get(str(etf_code), [])
        sold = 0
        for lot in lots:
            if left <= 0:
                break
            if int(lot.qty) <= 0:
                continue
            if td < lot.sellable_date:
                continue
            take = min(int(lot.qty), int(left))
            lot.qty = int(lot.qty) - int(take)
            left -= int(take)
            sold += int(take)
        self._lots[str(etf_code)] = [x for x in lots if int(x.qty) > 0]
        if sold > 0:
            self._invalidate_positions_cache()
        return int(sold)

    def place_order(self, req: OrderRequest) -> OrderResult:
        self._logger.debug(
            "place_order request | etf=%s side=%s qty=%s price=%.6f cash=%.6f sellable=%s frozen=%s",
            str(req.etf_code),
            str(req.side.value),
            int(req.quantity),
            float(req.price),
            float(self._cash),
            int(self._sellable_qty(req.etf_code)),
            bool(self._frozen),
        )
        if self._frozen:
            warn_once(
                f"bt_place_reject_frozen:{str(self._freeze_reason)}",
                f"Backtest order rejected because trading is frozen. reason={self._freeze_reason}",
                logger_name="backtest.adapters.trading",
            )
            self._logger.warning(
                "order rejected | reason=frozen etf=%s side=%s qty=%s price=%.6f",
                str(req.etf_code),
                str(req.side.value),
                int(req.quantity),
                float(req.price),
            )
            return OrderResult(order_id=0, status=OrderStatus.REJECTED, error=f"frozen: {self._freeze_reason}")

        qty = int(req.quantity)
        price = float(req.price)
        if qty <= 0 or price <= 0:
            warn_once(
                f"bt_place_invalid_qty_price:{str(req.side.value)}",
                f"Backtest order rejected due to invalid qty/price. side={req.side.value} qty={qty} price={price}",
                logger_name="backtest.adapters.trading",
            )
            self._logger.warning(
                "order rejected | reason=invalid_qty_price etf=%s side=%s qty=%s price=%.6f",
                str(req.etf_code),
                str(req.side.value),
                int(qty),
                float(price),
            )
            return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="invalid qty/price")

        raw_qty = int(qty)
        if req.side == OrderSide.BUY:
            norm_qty = int(raw_qty // int(LOT_SIZE)) * int(LOT_SIZE)
            if norm_qty <= 0:
                warn_once(
                    f"bt_place_buy_below_lot:{str(req.etf_code)}",
                    (
                        "Backtest buy rejected because quantity is below board lot size. "
                        f"etf={req.etf_code} req_qty={raw_qty} lot={int(LOT_SIZE)}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.warning(
                    "order rejected | reason=buy_below_lot etf=%s req_qty=%s lot=%s",
                    str(req.etf_code),
                    int(raw_qty),
                    int(LOT_SIZE),
                )
                return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="buy qty below lot size")
            if norm_qty != raw_qty:
                warn_once(
                    f"bt_place_buy_qty_normalized:{str(req.etf_code)}",
                    (
                        "Backtest buy quantity normalized to board lot. "
                        f"etf={req.etf_code} req_qty={raw_qty} normalized_qty={norm_qty} lot={int(LOT_SIZE)}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.info(
                    "buy qty normalized | etf=%s req_qty=%s normalized_qty=%s lot=%s",
                    str(req.etf_code),
                    int(raw_qty),
                    int(norm_qty),
                    int(LOT_SIZE),
                )
                qty = int(norm_qty)
                req = self._replace_request_qty(req, qty)

        if req.side == OrderSide.SELL:
            sellable_qty = int(self._sellable_qty(req.etf_code))
            if int(sellable_qty) <= 0:
                warn_once(
                    f"bt_place_no_sellable:{str(req.etf_code)}",
                    (
                        "Backtest sell rejected because no sellable quantity is available. "
                        f"etf={req.etf_code} req_qty={int(qty)} sellable=0"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.debug(
                    "order rejected | reason=no_sellable etf=%s req_qty=%s",
                    str(req.etf_code),
                    int(qty),
                )
                return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="insufficient sellable qty")

            norm_qty = int(qty)
            lot = int(LOT_SIZE)

            if int(norm_qty) > int(sellable_qty):
                if int(sellable_qty) >= int(lot):
                    norm_qty = int(sellable_qty // lot) * int(lot)
                else:
                    norm_qty = int(sellable_qty)
                if int(norm_qty) <= 0:
                    warn_once(
                        f"bt_place_insufficient_sellable:{str(req.etf_code)}",
                        (
                            "Backtest sell rejected due to insufficient sellable qty after normalization. "
                            f"etf={req.etf_code} req_qty={int(qty)} sellable={int(sellable_qty)}"
                        ),
                        logger_name="backtest.adapters.trading",
                    )
                    self._logger.debug(
                        "order rejected | reason=insufficient_sellable etf=%s req=%s sellable=%s",
                        str(req.etf_code),
                        int(qty),
                        int(sellable_qty),
                    )
                    return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="insufficient sellable qty")
                warn_once(
                    f"bt_place_sell_cap_to_sellable:{str(req.etf_code)}",
                    (
                        "Backtest sell quantity capped to current sellable quantity. "
                        f"etf={req.etf_code} req_qty={int(qty)} normalized_qty={int(norm_qty)} sellable={int(sellable_qty)}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.info(
                    "sell qty capped | etf=%s req_qty=%s normalized_qty=%s sellable=%s",
                    str(req.etf_code),
                    int(qty),
                    int(norm_qty),
                    int(sellable_qty),
                )

            if int(norm_qty) == int(sellable_qty) and int(norm_qty) % int(lot) != 0:
                warn_once(
                    f"bt_place_sell_odd_cleanup:{str(req.etf_code)}",
                    (
                        "Backtest sell allows odd-lot full cleanup for remaining position. "
                        f"etf={req.etf_code} qty={int(norm_qty)}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.debug(
                    "sell odd-lot cleanup allowed | etf=%s qty=%s",
                    str(req.etf_code),
                    int(norm_qty),
                )
            else:
                if int(norm_qty) < int(lot):
                    if int(sellable_qty) >= int(lot):
                        norm_qty = int(lot)
                        warn_once(
                            f"bt_place_sell_raise_to_lot:{str(req.etf_code)}",
                            (
                                "Backtest sell quantity raised to minimum board lot to avoid non-actionable odd request. "
                                f"etf={req.etf_code} req_qty={int(qty)} normalized_qty={int(norm_qty)} sellable={int(sellable_qty)}"
                            ),
                            logger_name="backtest.adapters.trading",
                        )
                        self._logger.info(
                            "sell qty raised to lot | etf=%s req_qty=%s normalized_qty=%s sellable=%s",
                            str(req.etf_code),
                            int(qty),
                            int(norm_qty),
                            int(sellable_qty),
                        )
                    else:
                        norm_qty = int(sellable_qty)
                        warn_once(
                            f"bt_place_sell_small_full_cleanup:{str(req.etf_code)}",
                            (
                                "Backtest sell quantity switched to full odd-lot cleanup because sellable<lot. "
                                f"etf={req.etf_code} req_qty={int(qty)} normalized_qty={int(norm_qty)}"
                            ),
                            logger_name="backtest.adapters.trading",
                        )
                if int(norm_qty) % int(lot) != 0 and int(norm_qty) != int(sellable_qty):
                    down = int(norm_qty // lot) * int(lot)
                    if int(down) > 0:
                        warn_once(
                            f"bt_place_sell_qty_normalized:{str(req.etf_code)}",
                            (
                                "Backtest sell quantity normalized down to board lot. "
                                f"etf={req.etf_code} req_qty={int(qty)} normalized_qty={int(down)} lot={int(lot)}"
                            ),
                            logger_name="backtest.adapters.trading",
                        )
                        self._logger.info(
                            "sell qty normalized | etf=%s req_qty=%s normalized_qty=%s lot=%s",
                            str(req.etf_code),
                            int(qty),
                            int(down),
                            int(lot),
                        )
                        norm_qty = int(down)
                if int(norm_qty) <= 0:
                    warn_once(
                        f"bt_place_sell_round_to_zero:{str(req.etf_code)}",
                        (
                            "Backtest sell rejected because normalized lot quantity becomes zero. "
                            f"etf={req.etf_code} req_qty={int(qty)} sellable={int(sellable_qty)} lot={int(lot)}"
                        ),
                        logger_name="backtest.adapters.trading",
                    )
                    self._logger.debug(
                        "order rejected | reason=sell_round_to_zero etf=%s req_qty=%s lot=%s",
                        str(req.etf_code),
                        int(qty),
                        int(lot),
                    )
                    return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="sell qty below lot size")

            if int(norm_qty) != int(qty):
                req = self._replace_request_qty(req, int(norm_qty))
                qty = int(norm_qty)

        amount = float(price) * int(qty)
        if req.side == OrderSide.BUY:
            need = float(amount) + float(amount) * float(self._fee_rate)
            if need > float(self._cash) + 1e-9:
                warn_once(
                    f"bt_place_insufficient_cash:{str(req.etf_code)}",
                    (
                        "Backtest buy rejected due to insufficient cash (with fee). "
                        f"etf={req.etf_code} need={need:.6f} cash={float(self._cash):.6f}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.warning(
                    "order rejected | reason=insufficient_cash etf=%s qty=%s price=%.6f need=%.6f cash=%.6f",
                    str(req.etf_code),
                    int(qty),
                    float(price),
                    float(need),
                    float(self._cash),
                )
                return OrderResult(order_id=0, status=OrderStatus.REJECTED, error="insufficient cash")
        oid = int(self._next_order_id)
        self._next_order_id += 1
        rec = _OrderRecord(order_id=oid, request=req, submitted_at=self._clock.now())
        self._orders[oid] = rec
        self._logger.debug(
            "order submitted | order_id=%s etf=%s side=%s qty=%s price=%.6f",
            int(oid),
            str(req.etf_code),
            str(req.side.value),
            int(qty),
            float(price),
        )
        return OrderResult(order_id=oid, status=OrderStatus.SUBMITTED, raw={"order_id": oid})

    def _apply_fill(self, rec: _OrderRecord) -> None:
        if rec.executed:
            self._logger.debug(
                "fill skipped | order_id=%s already_executed status=%s",
                int(rec.order_id),
                str(rec.status.value),
            )
            return
        req = rec.request
        qty = int(req.quantity)
        price = float(req.price)
        amount = float(price) * int(qty)
        fee = float(amount) * float(self._fee_rate)
        cash_before = float(self._cash)

        if req.side == OrderSide.BUY:
            gross = float(amount) + float(fee)
            if gross > float(self._cash) + 1e-9:
                degrade_once(
                    f"bt_confirm_insufficient_cash:{str(req.etf_code)}",
                    (
                        "Backtest buy confirmation failed due to insufficient cash at confirm stage. "
                        f"etf={req.etf_code} order_id={rec.order_id} gross={gross:.6f} cash={float(self._cash):.6f}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                rec.status = OrderStatus.REJECTED
                rec.error = "insufficient cash on confirm"
                rec.executed = True
                rec.filled_qty = 0
                rec.avg_price = None
                rec.fee = 0.0
                self._logger.warning(
                    "fill rejected | side=BUY reason=insufficient_cash_on_confirm order_id=%s etf=%s gross=%.6f cash=%.6f",
                    int(rec.order_id),
                    str(req.etf_code),
                    float(gross),
                    float(cash_before),
                )
                return
            self._cash = float(self._cash) - float(gross)
            sd = self._clock.now().date() if self._enable_t0 else _next_trading_date(self._clock.now().date())
            self._lots.setdefault(req.etf_code, []).append(_Lot(qty=int(qty), sellable_date=sd))
            self._invalidate_positions_cache()
            rec.status = OrderStatus.FILLED
            rec.filled_qty = int(qty)
            rec.avg_price = float(price)
            rec.fee = float(fee)
            rec.executed = True
            self._logger.debug(
                "fill success | side=BUY order_id=%s etf=%s qty=%s price=%.6f fee=%.6f cash_before=%.6f cash_after=%.6f sellable_date=%s",
                int(rec.order_id),
                str(req.etf_code),
                int(qty),
                float(price),
                float(fee),
                float(cash_before),
                float(self._cash),
                sd.isoformat(),
            )
        else:
            sold = self._consume_sellable_lots(etf_code=req.etf_code, qty=int(qty))
            if sold <= 0:
                degrade_once(
                    f"bt_confirm_no_sellable:{str(req.etf_code)}",
                    (
                        "Backtest sell confirmation found no sellable lots. "
                        f"etf={req.etf_code} order_id={rec.order_id} req_qty={qty}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                rec.status = OrderStatus.REJECTED
                rec.error = "no sellable qty on confirm"
                rec.executed = True
                rec.filled_qty = 0
                rec.avg_price = None
                rec.fee = 0.0
                self._logger.warning(
                    "fill rejected | side=SELL reason=no_sellable_on_confirm order_id=%s etf=%s req_qty=%s",
                    int(rec.order_id),
                    str(req.etf_code),
                    int(qty),
                )
                return
            sold_amount = float(price) * int(sold)
            sold_fee = float(sold_amount) * float(self._fee_rate)
            self._cash = float(self._cash) + float(sold_amount) - float(sold_fee)
            rec.status = OrderStatus.FILLED
            rec.filled_qty = int(sold)
            rec.avg_price = float(price)
            rec.fee = float(sold_fee)
            rec.executed = True
            if int(sold) < int(qty):
                warn_once(
                    f"bt_sell_partial_fill:{str(req.etf_code)}",
                    (
                        "Backtest sell partially filled due to sellable constraint at confirm stage. "
                        f"etf={req.etf_code} req_qty={qty} filled_qty={sold}"
                    ),
                    logger_name="backtest.adapters.trading",
                )
                self._logger.warning(
                    "fill partial | side=SELL order_id=%s etf=%s req_qty=%s filled_qty=%s",
                    int(rec.order_id),
                    str(req.etf_code),
                    int(qty),
                    int(sold),
                )
            self._logger.debug(
                "fill success | side=SELL order_id=%s etf=%s qty=%s price=%.6f fee=%.6f cash_before=%.6f cash_after=%.6f",
                int(rec.order_id),
                str(req.etf_code),
                int(sold),
                float(price),
                float(sold_fee),
                float(cash_before),
                float(self._cash),
            )

        if rec.status == OrderStatus.FILLED:
            self._fills.append(
                {
                    "timestamp": self._clock.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "order_id": int(rec.order_id),
                    "etf_code": str(req.etf_code),
                    "side": str(req.side.value),
                    "quantity": int(rec.filled_qty),
                    "price": float(rec.avg_price or 0.0),
                    "amount": float((rec.avg_price or 0.0) * int(rec.filled_qty)),
                    "fee": float(rec.fee),
                }
            )
            self._logger.debug(
                "fill recorded | order_id=%s side=%s etf=%s qty=%s price=%.6f fee=%.6f",
                int(rec.order_id),
                str(req.side.value),
                str(req.etf_code),
                int(rec.filled_qty),
                float(rec.avg_price or 0.0),
                float(rec.fee),
            )

    def cancel_order(self, order_id: int) -> bool:
        rec = self._orders.get(int(order_id))
        if rec is None:
            self._logger.debug("cancel_order ignored | order_id=%s reason=not_found", int(order_id))
            return False
        if rec.status in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED):
            self._logger.debug(
                "cancel_order ignored | order_id=%s status=%s",
                int(order_id),
                str(rec.status.value),
            )
            return False
        rec.status = OrderStatus.CANCELED
        self._logger.debug("order canceled | order_id=%s", int(order_id))
        return True

    def query_positions(self) -> list[Any]:
        day = self._clock.now().date()
        if self._positions_cache_day == day and self._positions_cache_revision == self._lots_revision:
            return self._clone_positions_payload(self._positions_cache)

        out: list[dict[str, object]] = []
        for code in sorted(self._lots.keys()):
            total = self._total_qty(code)
            if total <= 0:
                continue
            sellable = self._sellable_qty(code)
            out.append(
                {
                    "etf_code": str(code),
                    "total_qty": int(total),
                    "sellable_qty": int(sellable),
                }
            )
        self._positions_cache_day = day
        self._positions_cache_revision = self._lots_revision
        self._positions_cache = self._clone_positions_payload(out)
        return self._clone_positions_payload(out)

    def query_orders(self) -> list[Any]:
        out: list[dict[str, object]] = []
        for rec in self._orders.values():
            out.append(
                {
                    "order_id": int(rec.order_id),
                    "status": str(rec.status.value),
                    "etf_code": str(rec.request.etf_code),
                    "side": str(rec.request.side.value),
                    "qty": int(rec.request.quantity),
                    "filled_qty": int(rec.filled_qty),
                    "avg_price": rec.avg_price,
                    "error": str(rec.error or ""),
                }
            )
        return out

    def query_asset(self) -> dict[str, Any]:
        return {"cash": float(self._cash), "total_asset": float(self._cash)}

    def confirm_order(self, order_id: int, timeout_s: float = 10.0) -> OrderResult:
        _ = timeout_s
        rec = self._orders.get(int(order_id))
        if rec is None:
            warn_once(
                f"bt_confirm_unknown_order:{int(order_id)}",
                f"Backtest confirm_order got unknown order_id={int(order_id)}",
                logger_name="backtest.adapters.trading",
            )
            self._logger.warning("confirm_order unknown | order_id=%s", int(order_id))
            return OrderResult(order_id=int(order_id), status=OrderStatus.UNKNOWN, error="order not found")
        if rec.status == OrderStatus.CANCELED:
            self._logger.debug("confirm_order canceled | order_id=%s", int(order_id))
            return OrderResult(order_id=int(order_id), status=OrderStatus.CANCELED, error=rec.error)
        if rec.status == OrderStatus.REJECTED:
            self._logger.debug(
                "confirm_order rejected | order_id=%s error=%s",
                int(order_id),
                str(rec.error or ""),
            )
            return OrderResult(order_id=int(order_id), status=OrderStatus.REJECTED, error=rec.error)
        if rec.status != OrderStatus.FILLED:
            self._logger.debug(
                "confirm_order applying fill | order_id=%s current_status=%s",
                int(order_id),
                str(rec.status.value),
            )
            self._apply_fill(rec)
        out = OrderResult(
            order_id=int(rec.order_id),
            status=rec.status,
            filled_qty=int(rec.filled_qty),
            avg_price=rec.avg_price,
            raw={"fee": float(rec.fee)},
            error=str(rec.error or ""),
        )
        self._logger.debug(
            "confirm_order done | order_id=%s status=%s filled_qty=%s avg_price=%s fee=%.6f error=%s",
            int(out.order_id),
            str(out.status.value),
            int(out.filled_qty),
            out.avg_price,
            float(rec.fee),
            str(out.error or ""),
        )
        return out

    def force_reconcile(self) -> dict[str, Any]:
        return {"positions": self.query_positions(), "orders": self.query_orders(), "asset": self.query_asset()}

    @staticmethod
    def _normalize_freeze_reason(reason: str) -> str:
        out = str(reason or "").strip()
        while out.lower().startswith("frozen:"):
            out = str(out.split(":", 1)[1] if ":" in out else "").strip()
        if not out:
            out = "UNKNOWN_FREEZE_REASON"
        if len(out) > 240:
            out = f"{out[:240]}..."
        return str(out)

    def enter_freeze_mode(self, reason: str) -> None:
        norm = self._normalize_freeze_reason(str(reason or ""))
        lower = str(norm).lower()
        benign = (
            "sell qty below lot size",
            "buy qty below lot size",
            "insufficient sellable qty",
            "insufficient cash",
            "no sellable",
            "round_to_zero",
        )
        if any(x in lower for x in benign):
            warn_once(
                f"bt_freeze_skip_benign:{norm}",
                (
                    "Backtest skip freeze for benign execution normalization issue. "
                    f"reason={norm}"
                ),
                logger_name="backtest.adapters.trading",
            )
            self._logger.info("freeze skipped | reason=%s", str(norm))
            return
        if self._frozen and str(self._freeze_reason) == str(norm):
            self._logger.debug("freeze mode unchanged | reason=%s", str(norm))
            return
        self._frozen = True
        self._freeze_reason = str(norm)
        self._logger.warning("freeze mode entered | reason=%s", str(self._freeze_reason))

    def exit_freeze_mode(self) -> None:
        self._frozen = False
        self._freeze_reason = ""
        self._logger.info("freeze mode exited")
