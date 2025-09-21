import numpy as np
import pandas as pd

def iqr_clip(series: pd.Series, k: float = 1.5) -> pd.Series:
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - k*iqr, q3 + k*iqr
    return series[(series >= lower) and (series <= upper)]

def pmn_from_prices(prices: list[float]) -> dict:
    if not prices:
        return {"pmn": None, "pmn_low": None, "pmn_high": None, "n": 0}
    s = pd.Series(prices, dtype=float).dropna()
    if len(s) < 3:
        m = float(np.median(s))
        return {"pmn": m, "pmn_low": m, "pmn_high": m, "n": len(s)}
    s = s[(s >= s.quantile(0.05)) & (s <= s.quantile(0.95))]
    pmn = float(s.median())
    std = float(s.std(ddof=0)) if len(s) > 1 else 0.0
    return {"pmn": pmn, "pmn_low": pmn - std, "pmn_high": pmn + std, "n": int(len(s))}
