from __future__ import annotations

from enum import Enum


class DataQuality(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"


class FSMState(str, Enum):
    S0_IDLE = "S0"
    S1_TRIAL = "S1"
    S2_BASE = "S2"
    S3_SCALED = "S3"
    S4_FULL = "S4"
    S5_REDUCED = "S5"


class ActionType(str, Enum):
    ENTRY_CONFIRM = "ENTRY_CONFIRM"
    SCALE_CONFIRM = "SCALE_CONFIRM"
    T0_SIGNAL = "T0_SIGNAL"
    EXIT_LAYER1_TRIGGER_CHECK = "EXIT_LAYER1_TRIGGER_CHECK"
    EXIT_LIFEBOAT_BUYBACK_CHECK = "EXIT_LIFEBOAT_BUYBACK_CHECK"
    PENDING_EXECUTE = "PENDING_EXECUTE"
    ORDER_STATUS_MONITOR = "ORDER_STATUS_MONITOR"
    OFFLINE_DAILY = "OFFLINE_DAILY"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"


class OrderTimeInForce(str, Enum):
    DAY = "DAY"


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"

