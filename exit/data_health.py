from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.enums import DataQuality

from .constants import S_CHIP_COLD_START_DAYS


@dataclass(frozen=True)
class DataHealthResult:
    health: dict[str, DataQuality]
    alerts: list[str]


def _date_eq(a: Optional[str], b: str) -> bool:
    if a is None:
        return False
    sa = str(a)
    sb = str(b)
    return bool(sa == sb)


def evaluate_data_health(
    *,
    expected_yyyymmdd: str,
    dpc_date_yyyymmdd: Optional[str],
    llm_date_yyyymmdd: Optional[str],
    bars_date_yyyymmdd: Optional[str],
    chip_engine_days: int,
) -> DataHealthResult:
    exp = str(expected_yyyymmdd)
    if not (len(exp) == 8 and exp.isdigit()):
        raise AssertionError(f"invalid expected_yyyymmdd: {expected_yyyymmdd}")

    health: dict[str, DataQuality] = {
        "S_chip": DataQuality.OK,
        "S_sentiment": DataQuality.OK,
        "S_diverge": DataQuality.OK,
        "S_time": DataQuality.OK,
    }
    alerts: list[str] = []

    if int(chip_engine_days) < int(S_CHIP_COLD_START_DAYS):
        health["S_chip"] = DataQuality.UNAVAILABLE
        alerts.append(
            f"⚠️ 筹码引擎冷启动第 {int(chip_engine_days)}/{int(S_CHIP_COLD_START_DAYS)} 天，S_chip 信号已禁用。"
        )
    else:
        if not _date_eq(dpc_date_yyyymmdd, exp):
            health["S_chip"] = DataQuality.UNAVAILABLE
            alerts.append(
                f"⚠️ DPC 数据过期（最新：{dpc_date_yyyymmdd}，期望：{exp}），S_chip 已降级为 UNAVAILABLE。请检查筹码引擎是否正常运行。"
            )

    if not _date_eq(llm_date_yyyymmdd, exp):
        health["S_sentiment"] = DataQuality.UNAVAILABLE
        alerts.append(
            f"⚠️ LLM 评分数据过期（最新：{llm_date_yyyymmdd}，期望：{exp}），S_sentiment 已降级为 UNAVAILABLE。请检查情绪管线是否正常运行。"
        )

    if not _date_eq(bars_date_yyyymmdd, exp):
        health["S_diverge"] = DataQuality.UNAVAILABLE
        alerts.append(
            f"⚠️ 日线数据过期（最新：{bars_date_yyyymmdd}，期望：{exp}），S_diverge 已降级为 UNAVAILABLE。"
        )

    if any(v == DataQuality.UNAVAILABLE for v in health.values()):
        missing = [k for k, v in health.items() if v == DataQuality.UNAVAILABLE]
        alerts.append(
            f"⚠️ 信号降级运行：{missing} 数据不可用。Layer 1 触发时将按最高风控模式执行（无救生衣）；Layer 2 以中等风险假设运行。"
        )

    return DataHealthResult(health=health, alerts=alerts)

