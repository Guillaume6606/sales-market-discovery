import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any

def iqr_clip(series: pd.Series, k: float = 1.5) -> pd.Series:
    """
    Clip series values using Interquartile Range (IQR) method.
    
    Args:
        series: Pandas Series of numeric values
        k: IQR multiplier (default 1.5)
        
    Returns:
        Filtered series with outliers removed
    """
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - k*iqr, q3 + k*iqr
    return series[(series >= lower) & (series <= upper)]

def pmn_from_prices(
    prices: List[float],
    timestamps: List[datetime] | None = None,
    time_weighted: bool = False
) -> Dict[str, Any]:
    """
    Calculate Predicted Market Net (PMN) price from historical prices with methodology tracking.
    
    This function computes the median price as the PMN, along with confidence bounds
    based on standard deviation. It filters outliers by removing the bottom and top
    5% of prices when there are sufficient data points.
    
    Args:
        prices: List of historical price values
        timestamps: Optional list of timestamps for each price (for time-weighted calc)
        time_weighted: If True and timestamps provided, weight recent prices more heavily
        
    Returns:
        Dictionary containing:
            - pmn: Median price (predicted market net)
            - pmn_low: Lower bound (pmn - std)
            - pmn_high: Upper bound (pmn + std)
            - n: Number of valid prices used in calculation
            - methodology: Dict with calculation metadata
            
        Returns None values for pmn bounds if no prices provided.
    """
    if not prices:
        return {
            "pmn": None,
            "pmn_low": None,
            "pmn_high": None,
            "n": 0,
            "methodology": {
                "method": "none",
                "reason": "no_data"
            }
        }
    
    # Convert to pandas series
    s = pd.Series(prices, dtype=float).dropna()
    original_count = len(s)
    
    # Calculate time range if timestamps provided
    time_range_days = None
    if timestamps and len(timestamps) == len(prices):
        valid_timestamps = [ts for ts, p in zip(timestamps, prices) if pd.notna(p)]
        if valid_timestamps:
            time_range = max(valid_timestamps) - min(valid_timestamps)
            time_range_days = time_range.days
    
    # Handle small sample sizes
    if len(s) < 3:
        m = float(np.median(s))
        return {
            "pmn": m,
            "pmn_low": m,
            "pmn_high": m,
            "n": len(s),
            "methodology": {
                "method": "simple_median",
                "outlier_filter": "none",
                "sample_size": int(len(s)),
                "time_range_days": time_range_days,
                "reason": "insufficient_data_for_filtering"
            }
        }
    
    # Filter outliers using percentile method
    s_filtered = s[(s >= s.quantile(0.05)) & (s <= s.quantile(0.95))]
    filtered_count = len(s_filtered)
    
    # Apply time weighting if requested
    method_name = "median_std"
    if time_weighted and timestamps and len(timestamps) == original_count:
        try:
            # Create dataframe with prices and timestamps
            df = pd.DataFrame({
                'price': prices,
                'timestamp': timestamps
            }).dropna()
            
            # Filter outliers
            df = df[(df['price'] >= df['price'].quantile(0.05)) & 
                   (df['price'] <= df['price'].quantile(0.95))]
            
            # Calculate weights: exponential decay with 30-day half-life
            now = datetime.now()
            if df['timestamp'].dt.tz is None:
                # Make timestamps timezone-aware if needed
                df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize('UTC')
            
            df['age_days'] = (now.replace(tzinfo=None) - df['timestamp'].dt.tz_localize(None)).dt.days
            df['weight'] = np.exp(-df['age_days'] / 30.0)
            df['weight'] = df['weight'] / df['weight'].sum()  # Normalize weights
            
            # Weighted median approximation (use weighted mean as proxy)
            pmn = float((df['price'] * df['weight']).sum())
            s_filtered = df['price']
            method_name = "weighted_median"
        except Exception:
            # Fallback to standard median if weighting fails
            pmn = float(s_filtered.median())
            method_name = "median_std_fallback"
    else:
        pmn = float(s_filtered.median())
    
    # Calculate standard deviation
    std = float(s_filtered.std(ddof=0)) if len(s_filtered) > 1 else 0.0
    
    return {
        "pmn": pmn,
        "pmn_low": pmn - std,
        "pmn_high": pmn + std,
        "n": int(filtered_count),
        "methodology": {
            "method": method_name,
            "outlier_filter": "percentile_5_95",
            "sample_size": int(filtered_count),
            "original_sample_size": original_count,
            "outliers_removed": original_count - filtered_count,
            "time_range_days": time_range_days,
            "time_weighted": time_weighted
        }
    }
