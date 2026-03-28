from __future__ import annotations

from datetime import date, datetime

from entry.high_chase import decode_high_chase_signal_rows


def test_decode_high_chase_signal_rows_normalizes_datetime_before_sorting() -> None:
    rows = decode_high_chase_signal_rows(
        [
            (datetime(2024, 1, 3, 15, 1, 0), 1.03),
            (date(2024, 1, 2), 1.02),
            {"signal_date": "20240101", "ref_price": 1.01},
        ]
    )

    assert rows == [
        (date(2024, 1, 1), 1.01),
        (date(2024, 1, 2), 1.02),
        (date(2024, 1, 3), 1.03),
    ]
