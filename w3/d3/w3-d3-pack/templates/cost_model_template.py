#!/usr/bin/env python3
"""Break-even cost model for an AIOps platform (§8).

Usage:
    python cost_model.py
Edit the INPUT block below with your real numbers.
"""
from dataclasses import dataclass


@dataclass
class CostInputs:
    # --- Cost side (monthly USD) ---
    compute_usd_month: float = 0.0
    storage_usd_month: float = 0.0
    licenses_usd_month: float = 0.0
    engineer_fte_count: float = 0.0
    engineer_fte_usd_month: float = 12_500.0

    # --- Value side ---
    incidents_per_month: float = 0.0
    mttd_minutes_before: float = 30.0
    mttd_minutes_after: float = 10.0
    revenue_loss_usd_per_minute_down: float = 0.0
    incidents_prevented_per_month: float = 0.0
    on_call_hours_saved_per_month: float = 0.0
    on_call_usd_per_hour: float = 75.0


def total_cost(i: CostInputs) -> float:
    return (
        i.compute_usd_month
        + i.storage_usd_month
        + i.licenses_usd_month
        + i.engineer_fte_count * i.engineer_fte_usd_month
    )


def monthly_value(i: CostInputs) -> float:
    mttd_savings = (
        i.incidents_per_month
        * (i.mttd_minutes_before - i.mttd_minutes_after)
        * i.revenue_loss_usd_per_minute_down
    )
    prevention_value = (
        i.incidents_prevented_per_month
        * 60                                                # avg 1h incident
        * i.revenue_loss_usd_per_minute_down
    )
    oncall_value = i.on_call_hours_saved_per_month * i.on_call_usd_per_hour
    return mttd_savings + prevention_value + oncall_value


def break_even(i: CostInputs) -> dict:
    c = total_cost(i)
    v = monthly_value(i)
    return {
        "monthly_cost_usd": c,
        "monthly_value_usd": v,
        "net_monthly_usd": v - c,
        "roi_ratio": (v / c) if c else None,
        "verdict": (
            "GREEN — value clearly exceeds cost"  if v > 2 * c
            else "AMBER — value > cost but margin thin" if v > c
            else "RED — cost > value, do not deploy"
        ),
    }


if __name__ == "__main__":
    # === EDIT INPUTS BELOW ===
    inputs = CostInputs(
        compute_usd_month=800,
        storage_usd_month=200,
        licenses_usd_month=0,
        engineer_fte_count=0.25,
        incidents_per_month=4,
        mttd_minutes_before=30,
        mttd_minutes_after=8,
        revenue_loss_usd_per_minute_down=100,
        incidents_prevented_per_month=1,
        on_call_hours_saved_per_month=10,
    )
    result = break_even(inputs)
    for k, v in result.items():
        print(f"{k:25s} {v}")
