#!/usr/bin/env python3
"""
Test script for the Computation Engine

This script tests all components of the computation engine:
1. PMN calculation and persistence
2. Liquidity score calculation
3. Margin estimation
4. Opportunity scoring
"""

import sys
from datetime import datetime, timezone
from libs.common.db import SessionLocal
from libs.common.models import (
    ProductTemplate,
    ListingObservation,
    MarketPriceNormal,
    ProductDailyMetrics
)
from ingestion.computation import (
    compute_pmn_for_product,
    compute_liquidity_score,
    estimate_margin,
    compute_opportunity_score,
    compute_all_product_metrics
)
from ingestion.pricing import pmn_from_prices

def test_pmn_calculation():
    """Test basic PMN calculation without database"""
    print("\n" + "="*80)
    print("TEST 1: PMN Calculation (without database)")
    print("="*80)
    
    # Test with sample prices
    prices = [100.0, 110.0, 105.0, 102.0, 108.0, 95.0, 112.0, 103.0, 107.0, 101.0]
    timestamps = [datetime.now(timezone.utc) for _ in prices]
    
    print(f"\nInput prices: {prices}")
    
    # Test without time weighting
    result = pmn_from_prices(prices)
    print(f"\n✓ PMN (no time weighting): €{result['pmn']:.2f}")
    print(f"  - Range: €{result['pmn_low']:.2f} - €{result['pmn_high']:.2f}")
    print(f"  - Sample size: {result['n']}")
    print(f"  - Method: {result['methodology']['method']}")
    
    # Test with time weighting
    result_weighted = pmn_from_prices(prices, timestamps, time_weighted=True)
    print(f"\n✓ PMN (with time weighting): €{result_weighted['pmn']:.2f}")
    print(f"  - Range: €{result_weighted['pmn_low']:.2f} - €{result_weighted['pmn_high']:.2f}")
    print(f"  - Method: {result_weighted['methodology']['method']}")
    
    return True


def test_pmn_persistence():
    """Test PMN computation and database persistence"""
    print("\n" + "="*80)
    print("TEST 2: PMN Computation and Persistence")
    print("="*80)
    
    with SessionLocal() as db:
        # Find a product with sufficient data
        products = db.query(ProductTemplate).filter(
            ProductTemplate.is_active == True
        ).limit(5).all()
        
        if not products:
            print("❌ No active products found in database")
            return False
        
        print(f"\nFound {len(products)} active product(s)")
        
        success_count = 0
        for product in products:
            print(f"\n--- Testing product: {product.name} ({product.product_id}) ---")
            
            # Count available observations
            obs_count = db.query(ListingObservation).filter(
                ListingObservation.product_id == product.product_id,
                ListingObservation.price.isnot(None)
            ).count()
            
            print(f"  Observations with price: {obs_count}")
            
            if obs_count < 3:
                print("  ⚠ Insufficient data, skipping")
                continue
            
            # Compute PMN
            result = compute_pmn_for_product(str(product.product_id), db)
            
            if result["status"] == "success":
                print(f"  ✓ PMN computed: €{result['pmn']:.2f}")
                print(f"    - Range: €{result['pmn_low']:.2f} - €{result['pmn_high']:.2f}")
                print(f"    - Sample size: {result['sample_size']}")
                print(f"    - Data source: {result['methodology']['data_source']}")
                success_count += 1
            else:
                print(f"  ✗ Failed: {result.get('error', result.get('status'))}")
        
        print(f"\n✓ PMN computed for {success_count}/{len(products)} products")
        return success_count > 0


def test_liquidity_calculation():
    """Test liquidity score calculation"""
    print("\n" + "="*80)
    print("TEST 3: Liquidity Score Calculation")
    print("="*80)
    
    with SessionLocal() as db:
        # Find products with PMN
        pmn_records = db.query(MarketPriceNormal).limit(3).all()
        
        if not pmn_records:
            print("❌ No products with PMN found")
            return False
        
        print(f"\nTesting {len(pmn_records)} product(s) with PMN")
        
        for pmn_record in pmn_records:
            product = db.query(ProductTemplate).filter(
                ProductTemplate.product_id == pmn_record.product_id
            ).first()
            
            if not product:
                continue
            
            print(f"\n--- Product: {product.name} ---")
            
            # Compute liquidity
            liquidity = compute_liquidity_score(str(product.product_id), db)
            
            print(f"  ✓ Liquidity score: {liquidity['liquidity_score']:.2f}/100")
            print(f"    - Sold (30d): {liquidity['sold_count_30d']}")
            print(f"    - Sold (7d): {liquidity['sold_count_7d']}")
            print(f"    - Active listings: {liquidity['active_listings_count']}")
            
            if liquidity.get('breakdown'):
                print(f"    - Breakdown:")
                print(f"      • Velocity: {liquidity['breakdown']['velocity_score']:.2f}")
                print(f"      • Depth: {liquidity['breakdown']['depth_score']:.2f}")
                print(f"      • Freshness: {liquidity['breakdown']['freshness_score']:.2f}")
        
        return True


def test_margin_estimation():
    """Test margin estimation with fees"""
    print("\n" + "="*80)
    print("TEST 4: Margin Estimation")
    print("="*80)
    
    # Test cases
    test_cases = [
        {"listing_price": 80.0, "pmn": 120.0, "shipping": 5.0, "source": "ebay"},
        {"listing_price": 50.0, "pmn": 70.0, "shipping": 0.0, "source": "vinted"},
        {"listing_price": 100.0, "pmn": 110.0, "shipping": 3.0, "source": "leboncoin"},
    ]
    
    for i, case in enumerate(test_cases, 1):
        print(f"\n--- Test Case {i}: {case['source'].upper()} ---")
        print(f"  Purchase price: €{case['listing_price']:.2f}")
        print(f"  Expected resale (PMN): €{case['pmn']:.2f}")
        print(f"  Shipping: €{case['shipping']:.2f}")
        
        margin = estimate_margin(
            case['listing_price'],
            case['pmn'],
            case['shipping'],
            case['source']
        )
        
        print(f"\n  Results:")
        print(f"  ✓ Gross margin: €{margin['gross_margin']:.2f} ({margin['gross_margin_pct']:.1f}%)")
        print(f"  ✓ Net margin: €{margin['net_margin']:.2f} ({margin['net_margin_pct']:.1f}%)")
        print(f"    - Platform fee: €{margin['fees']['platform_fee']:.2f}")
        print(f"    - Payment fee: €{margin['fees']['payment_fee']:.2f}")
        print(f"    - Shipping: €{margin['fees']['shipping']:.2f}")
        print(f"    - Total fees: €{margin['fees']['total_fees']:.2f}")
        print(f"  ✓ Risk level: {margin['risk_level'].upper()} - {margin['risk_description']}")
    
    return True


def test_opportunity_scoring():
    """Test opportunity score calculation"""
    print("\n" + "="*80)
    print("TEST 5: Opportunity Scoring")
    print("="*80)
    
    with SessionLocal() as db:
        # Find active listings with PMN
        listings = db.query(ListingObservation).filter(
            ListingObservation.is_sold == False,
            ListingObservation.price.isnot(None)
        ).limit(5).all()
        
        if not listings:
            print("❌ No active listings found")
            return False
        
        print(f"\nAnalyzing {len(listings)} active listing(s)")
        
        scored_count = 0
        for listing in listings:
            # Get PMN data
            pmn_data = db.query(MarketPriceNormal).filter(
                MarketPriceNormal.product_id == listing.product_id
            ).first()
            
            if not pmn_data:
                continue
            
            # Get metrics
            metrics = db.query(ProductDailyMetrics).filter(
                ProductDailyMetrics.product_id == listing.product_id
            ).order_by(ProductDailyMetrics.date.desc()).first()
            
            product = db.query(ProductTemplate).filter(
                ProductTemplate.product_id == listing.product_id
            ).first()
            
            print(f"\n--- Listing: {listing.title[:60]} ---")
            print(f"  Product: {product.name if product else 'Unknown'}")
            print(f"  Source: {listing.source}")
            print(f"  Price: €{float(listing.price):.2f}")
            print(f"  PMN: €{float(pmn_data.pmn):.2f}")
            
            # Compute opportunity score
            opportunity = compute_opportunity_score(listing, metrics, pmn_data)
            
            print(f"\n  ✓ Opportunity Score: {opportunity['opportunity_score']:.2f}/100")
            print(f"    - Recommendation: {opportunity['recommendation'].upper()}")
            print(f"    - {opportunity['description']}")
            print(f"\n    Breakdown:")
            print(f"    • Margin: {opportunity['breakdown']['margin_score']:.2f}/40")
            print(f"    • Liquidity: {opportunity['breakdown']['liquidity_score']:.2f}/30")
            print(f"    • Risk: {opportunity['breakdown']['risk_score']:.2f}/30")
            
            if opportunity.get('margin_analysis'):
                ma = opportunity['margin_analysis']
                print(f"\n    Margin Analysis:")
                print(f"    • Net margin: €{ma['net_margin']:.2f} ({ma['net_margin_pct']:.1f}%)")
                print(f"    • Risk: {ma['risk_level']}")
            
            scored_count += 1
        
        print(f"\n✓ Scored {scored_count} listing(s)")
        return scored_count > 0


def test_batch_computation():
    """Test batch computation for all products"""
    print("\n" + "="*80)
    print("TEST 6: Batch Computation")
    print("="*80)
    
    with SessionLocal() as db:
        # Get active product IDs
        products = db.query(ProductTemplate.product_id).filter(
            ProductTemplate.is_active == True
        ).limit(3).all()
        
        if not products:
            print("❌ No active products found")
            return False
        
        product_ids = [str(p.product_id) for p in products]
        print(f"\nRunning batch computation for {len(product_ids)} product(s)")
        
        # Run batch computation
        result = compute_all_product_metrics(product_ids, db)
        
        print(f"\n✓ Batch computation completed:")
        print(f"  - Total products: {result['total']}")
        print(f"  - PMN computed: {result['pmn_computed']}")
        print(f"  - PMN insufficient data: {result['pmn_insufficient_data']}")
        print(f"  - PMN errors: {result['pmn_errors']}")
        print(f"  - Metrics updated: {result['metrics_updated']}")
        print(f"  - Metrics errors: {result['metrics_errors']}")
        
        return result['pmn_computed'] > 0 or result['metrics_updated'] > 0


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("COMPUTATION ENGINE TEST SUITE")
    print("="*80)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = [
        ("PMN Calculation", test_pmn_calculation),
        ("PMN Persistence", test_pmn_persistence),
        ("Liquidity Calculation", test_liquidity_calculation),
        ("Margin Estimation", test_margin_estimation),
        ("Opportunity Scoring", test_opportunity_scoring),
        ("Batch Computation", test_batch_computation),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, "PASSED" if success else "FAILED"))
        except Exception as e:
            print(f"\n❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, "ERROR"))
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, status in results if status == "PASSED")
    failed = sum(1 for _, status in results if status == "FAILED")
    errors = sum(1 for _, status in results if status == "ERROR")
    
    for test_name, status in results:
        emoji = "✅" if status == "PASSED" else "❌"
        print(f"{emoji} {test_name}: {status}")
    
    print(f"\nTotal: {len(results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Errors: {errors}")
    
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80 + "\n")
    
    return 0 if errors == 0 and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


