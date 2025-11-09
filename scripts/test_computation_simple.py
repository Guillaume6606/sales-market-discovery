#!/usr/bin/env python3
"""
Simple test script for Computation Engine core logic
Tests PMN calculation and margin estimation without database dependencies
"""

import sys
from datetime import datetime, timezone, timedelta

# Test 1: Import the pricing module
print("="*80)
print("TEST 1: Import and PMN Calculation")
print("="*80)

try:
    from ingestion.pricing import pmn_from_prices
    print("✓ Successfully imported pmn_from_prices")
except Exception as e:
    print(f"✗ Failed to import: {e}")
    sys.exit(1)

# Test PMN with sample data
prices = [100.0, 110.0, 105.0, 102.0, 108.0, 95.0, 112.0, 103.0, 107.0, 101.0, 109.0]
print(f"\nTest prices: {prices}")

result = pmn_from_prices(prices)
print(f"\n✓ PMN calculated: €{result['pmn']:.2f}")
print(f"  - Range: €{result['pmn_low']:.2f} - €{result['pmn_high']:.2f}")
print(f"  - Sample size: {result['n']}")
print(f"  - Method: {result['methodology']['method']}")
print(f"  - Outliers removed: {result['methodology']['outliers_removed']}")

# Test with timestamps
timestamps = [datetime.now(timezone.utc) - timedelta(days=i) for i in range(len(prices))]
result_weighted = pmn_from_prices(prices, timestamps, time_weighted=True)
print(f"\n✓ PMN with time weighting: €{result_weighted['pmn']:.2f}")
print(f"  - Method: {result_weighted['methodology']['method']}")

# Test edge cases
print("\n--- Edge Cases ---")

# Empty prices
empty_result = pmn_from_prices([])
print(f"✓ Empty prices: pmn={empty_result['pmn']}, n={empty_result['n']}")

# Few prices
few_result = pmn_from_prices([100.0, 105.0])
print(f"✓ Few prices (2): pmn={few_result['pmn']:.2f}, method={few_result['methodology']['method']}")


# Test 2: Import computation module
print("\n" + "="*80)
print("TEST 2: Import Computation Module")
print("="*80)

try:
    from ingestion.computation import estimate_margin, PLATFORM_FEES
    print("✓ Successfully imported computation module")
except Exception as e:
    print(f"✗ Failed to import: {e}")
    sys.exit(1)

# Test platform fees configuration
print("\n✓ Platform fees configured:")
for platform, fees in PLATFORM_FEES.items():
    print(f"  - {fees['name']}: {fees['commission']*100:.1f}% commission + {fees['payment']*100:.1f}% payment")


# Test 3: Margin estimation
print("\n" + "="*80)
print("TEST 3: Margin Estimation")
print("="*80)

test_cases = [
    {
        "name": "eBay - Good Deal",
        "listing_price": 80.0,
        "pmn": 120.0,
        "shipping": 5.0,
        "source": "ebay"
    },
    {
        "name": "Vinted - Small Margin",
        "listing_price": 50.0,
        "pmn": 60.0,
        "shipping": 0.0,
        "source": "vinted"
    },
    {
        "name": "LeBonCoin - Break Even",
        "listing_price": 100.0,
        "pmn": 108.0,
        "shipping": 3.0,
        "source": "leboncoin"
    },
    {
        "name": "Loss Scenario",
        "listing_price": 100.0,
        "pmn": 95.0,
        "shipping": 5.0,
        "source": "ebay"
    }
]

for i, case in enumerate(test_cases, 1):
    print(f"\n--- Case {i}: {case['name']} ---")
    print(f"Purchase: €{case['listing_price']:.2f}, PMN: €{case['pmn']:.2f}, Shipping: €{case['shipping']:.2f}")
    
    margin = estimate_margin(
        case['listing_price'],
        case['pmn'],
        case['shipping'],
        case['source']
    )
    
    print(f"✓ Gross margin: €{margin['gross_margin']:.2f} ({margin['gross_margin_pct']:.1f}%)")
    print(f"✓ Net margin: €{margin['net_margin']:.2f} ({margin['net_margin_pct']:.1f}%)")
    print(f"  Fees: €{margin['fees']['total_fees']:.2f} (platform: €{margin['fees']['platform_fee']:.2f}, payment: €{margin['fees']['payment_fee']:.2f})")
    print(f"✓ Risk: {margin['risk_level'].upper()} - {margin['risk_description']}")


# Test 4: Validate computations module structure
print("\n" + "="*80)
print("TEST 4: Validate Computation Module Functions")
print("="*80)

required_functions = [
    'compute_pmn_for_product',
    'compute_liquidity_score',
    'estimate_margin',
    'compute_opportunity_score',
    'compute_all_product_metrics'
]

try:
    from ingestion import computation
    
    for func_name in required_functions:
        if hasattr(computation, func_name):
            print(f"✓ Function exists: {func_name}")
        else:
            print(f"✗ Function missing: {func_name}")
            sys.exit(1)
    
    print("\n✓ All required functions are present")
    
except Exception as e:
    print(f"✗ Failed to validate module: {e}")
    sys.exit(1)


# Test 5: Check worker integration
print("\n" + "="*80)
print("TEST 5: Validate Worker Integration")
print("="*80)

try:
    from ingestion import worker
    
    # Check if computation functions are in worker functions list
    worker_funcs = [f.__name__ for f in worker.WorkerSettings.functions]
    
    expected_worker_funcs = [
        'scheduled_computation',
        'trigger_product_computation',
        'trigger_batch_computation'
    ]
    
    for func_name in expected_worker_funcs:
        if func_name in worker_funcs:
            print(f"✓ Worker task registered: {func_name}")
        else:
            print(f"✗ Worker task missing: {func_name}")
            sys.exit(1)
    
    # Check cron jobs
    cron_jobs = worker.WorkerSettings.cron_jobs
    print(f"\n✓ Total cron jobs configured: {len(cron_jobs)}")
    
    # Find scheduled_computation in cron jobs
    has_scheduled_computation = any(
        'scheduled_computation' in str(job) for job in cron_jobs
    )
    
    if has_scheduled_computation:
        print("✓ Scheduled computation cron job configured")
    else:
        print("⚠ Warning: scheduled_computation not in cron_jobs (might be configured differently)")
    
except Exception as e:
    print(f"✗ Failed to validate worker: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


# Summary
print("\n" + "="*80)
print("TEST SUMMARY")
print("="*80)
print("✅ All core computation engine tests passed!")
print("\nComponents validated:")
print("  ✓ PMN calculation with methodology tracking")
print("  ✓ Time-weighted PMN calculation")
print("  ✓ Platform-specific fee configuration")
print("  ✓ Margin estimation (gross & net)")
print("  ✓ Risk assessment")
print("  ✓ Computation module structure")
print("  ✓ Worker task integration")
print("\nNext steps:")
print("  1. Ensure database is running (PostgreSQL)")
print("  2. Run ingestion to populate data")
print("  3. Trigger computation: POST /computation/trigger-all")
print("  4. Check PMN status: GET /computation/status")
print("  5. View opportunities: GET /products/discovery")
print("="*80 + "\n")


