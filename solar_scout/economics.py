"""Sizing, cost, revenue, payback and CO2 estimate for a proposed installation.

German market figures, mid-2026:
- turnkey prices fall with system size (residential ~1,350 EUR/kWp, large
  commercial roofs ~900-1,000 EUR/kWp), 0% VAT for residential.
- EEG feed-in tariff (Teileinspeisung, Feb-Jul 2026) is tiered by capacity:
  7.78 ct/kWh up to 10 kWp, 6.73 ct up to 40 kWp, 5.50 ct up to 100 kWp.
- households pay ~35 ct/kWh, businesses ~25 ct/kWh; businesses self-consume a
  much larger share because their load is during daylight (that is why
  commercial roofs pay back faster - the B2B angle).
"""

from dataclasses import dataclass

FEED_IN_TIERS = [(10.0, 0.0778), (40.0, 0.0673), (100.0, 0.0550), (1e9, 0.0550)]
PRICE_TIERS = [(10.0, 1350.0), (40.0, 1150.0), (100.0, 1000.0), (1e9, 900.0)]
GRID_CO2_KG_PER_KWH = 0.35


@dataclass
class Assumptions:
    panel_wp: float = 440.0
    panel_area_m2: float = 1.953              # 1.134 m x 1.722 m
    eur_per_kwp: float = 0.0                  # 0 -> use size-tiered PRICE_TIERS
    electricity_price: float = 0.35           # EUR/kWh avoided by self-consumption
    self_consumption: float = 0.30            # share of production used on site
    fallback_specific_yield: float = 950.0    # kWh/kWp/a if PVGIS is unreachable

    def for_business(self) -> "Assumptions":
        return Assumptions(self.panel_wp, self.panel_area_m2, self.eur_per_kwp,
                           electricity_price=0.25, self_consumption=0.60,
                           fallback_specific_yield=self.fallback_specific_yield)


@dataclass
class Estimate:
    n_panels: int
    kwp: float
    panel_area_m2: float
    cost_eur: float
    annual_kwh: float
    specific_yield: float
    feed_in_rate: float          # average EUR/kWh across the tiers
    annual_benefit_eur: float    # avoided purchase + feed-in revenue
    payback_years: float
    co2_t_per_year: float


def _tiered(value: float, tiers) -> float:
    """Average rate for `value` kWp across marginal tiers."""
    total, prev = 0.0, 0.0
    for limit, rate in tiers:
        if value <= prev:
            break
        total += (min(value, limit) - prev) * rate
        prev = limit
    return total / value if value > 0 else tiers[0][1]


def estimate(n_panels: int, a: Assumptions, annual_kwh: float = 0.0,
             specific_yield: float = 0.0) -> Estimate:
    kwp = n_panels * a.panel_wp / 1000.0
    if not annual_kwh:
        specific_yield = a.fallback_specific_yield
        annual_kwh = kwp * specific_yield
    eur_per_kwp = a.eur_per_kwp or _tiered(kwp, PRICE_TIERS)
    cost = kwp * eur_per_kwp
    feed_in = _tiered(kwp, FEED_IN_TIERS)
    benefit = (annual_kwh * a.self_consumption * a.electricity_price
               + annual_kwh * (1 - a.self_consumption) * feed_in)
    return Estimate(
        n_panels=n_panels,
        kwp=round(kwp, 2),
        panel_area_m2=round(n_panels * a.panel_area_m2, 1),
        cost_eur=round(cost, -2),
        annual_kwh=round(annual_kwh, -1),
        specific_yield=round(specific_yield),
        feed_in_rate=round(feed_in, 4),
        annual_benefit_eur=round(benefit, -1),
        payback_years=round(cost / benefit, 1) if benefit > 0 else 0.0,
        co2_t_per_year=round(annual_kwh * GRID_CO2_KG_PER_KWH / 1000.0, 1),
    )
