from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass
class ChipDistribution:
    etf_code: str
    base_price: float
    bucket_size: float = 0.001
    chips: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    total_shares: float = 0.0
    last_update: datetime = field(default_factory=datetime.now)

    def price_to_index(self, price: float) -> int:
        return int(round((price - self.base_price) / self.bucket_size))

    def index_to_price(self, index: int) -> float:
        return self.base_price + index * self.bucket_size

    def get_price_grid(self) -> np.ndarray:
        return self.base_price + np.arange(len(self.chips), dtype=np.float64) * self.bucket_size

    def ensure_range(self, low: float, high: float, *, padding_buckets: int = 50) -> None:
        if self.chips.size == 0:
            if high < low:
                low, high = high, low
            span = max(high - low, self.bucket_size)
            need = int(np.ceil(span / self.bucket_size)) + 1 + 2 * padding_buckets
            self.base_price = float(np.floor((low - padding_buckets * self.bucket_size) / self.bucket_size) * self.bucket_size)
            self.chips = np.zeros(max(need, 1), dtype=np.float32)
            return

        if high < low:
            low, high = high, low
        min_idx = self.price_to_index(low) - padding_buckets
        max_idx = self.price_to_index(high) + padding_buckets

        left_expand = max(0, -min_idx)
        right_expand = max(0, max_idx - (len(self.chips) - 1))

        if left_expand == 0 and right_expand == 0:
            return

        new_len = len(self.chips) + left_expand + right_expand
        new_chips = np.zeros(new_len, dtype=np.float32)
        new_chips[left_expand : left_expand + len(self.chips)] = self.chips

        self.base_price -= left_expand * self.bucket_size
        self.chips = new_chips

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            p,
            chips=self.chips,
            meta=np.array([self.base_price, self.bucket_size, self.total_shares], dtype=np.float64),
        )

    @classmethod
    def load(cls, path: str | Path, etf_code: str) -> "ChipDistribution":
        p = Path(path)
        data = np.load(p, allow_pickle=False)
        meta = data["meta"]
        return cls(
            etf_code=etf_code,
            base_price=float(meta[0]),
            bucket_size=float(meta[1]),
            chips=data["chips"].astype(np.float32),
            total_shares=float(meta[2]),
        )

