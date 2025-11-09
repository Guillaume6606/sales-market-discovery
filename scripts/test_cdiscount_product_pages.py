"""
Test script for Cdiscount parse_product_page functionality
Tests product page parsing to extract detailed fields (description, brand, condition, shipping)

Usage:
    docker compose exec ingestion xvfb-run -a python test_cdiscount_product_pages.py KEYWORD [--limit N]

Examples:
    docker compose exec ingestion xvfb-run -a python test_cdiscount_product_pages.py "iPhone 13"
    docker compose exec ingestion xvfb-run -a python test_cdiscount_product_pages.py "MacBook Pro" --limit 3
"""
import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from loguru import logger
from ingestion.connectors.cdiscount_connector import CdiscountConnector


def print_comparison(listing_data: Dict[str, Any], detailed_data: Dict[str, Any], index: int) -> None:
    """Print before/after comparison of search vs product page data"""
    print(f"\n{'='*80}")
    print(f"Product #{index + 1}")
    print(f"{'='*80}")
    print(f"URL: {listing_data.get('url')}")
    print(f"\n--- FROM SEARCH RESULTS ---")
    print(f"Title:       {listing_data.get('title', 'N/A')}")
    print(f"Price:       {listing_data.get('price', 'N/A')} {listing_data.get('currency', 'EUR')}")
    print(f"Condition:   {listing_data.get('condition_raw', 'N/A')}")
    print(f"Brand:       {listing_data.get('brand', 'N/A')}")
    print(f"Description: {listing_data.get('description', 'N/A')}")
    print(f"Shipping:    {listing_data.get('shipping_cost', 'N/A')}")
    
    if detailed_data:
        print(f"\n--- FROM PRODUCT PAGE (parse_product_page) ---")
        print(f"Title:       {detailed_data.get('title', 'N/A')}")
        print(f"Price:       {detailed_data.get('price', 'N/A')} {detailed_data.get('currency', 'EUR')}")
        print(f"Condition:   {detailed_data.get('condition', 'N/A')}")
        print(f"Brand:       {detailed_data.get('brand', 'N/A')}")
        
        desc = detailed_data.get('description', '')
        if desc:
            desc_preview = desc[:150] + "..." if len(desc) > 150 else desc
            print(f"Description: {desc_preview}")
            print(f"             (Full length: {len(desc)} characters)")
        else:
            print(f"Description: N/A")
        
        shipping = detailed_data.get('shipping_cost')
        if shipping is not None:
            print(f"Shipping:    {shipping} EUR {'(FREE)' if shipping == 0.0 else ''}")
        else:
            print(f"Shipping:    N/A")
        
        print(f"Size:        {detailed_data.get('size', 'N/A')}")
        print(f"Color:       {detailed_data.get('color', 'N/A')}")
        
        # Show improvements
        improvements = []
        if detailed_data.get('description') and not listing_data.get('description'):
            improvements.append("✓ Description extracted")
        if detailed_data.get('brand') and not listing_data.get('brand'):
            improvements.append("✓ Brand extracted")
        if detailed_data.get('condition') and not listing_data.get('condition_raw'):
            improvements.append("✓ Condition extracted")
        if detailed_data.get('shipping_cost') is not None and listing_data.get('shipping_cost') is None:
            improvements.append("✓ Shipping cost extracted")
        
        if improvements:
            print(f"\nImprovements: {', '.join(improvements)}")
    else:
        print(f"\n--- PRODUCT PAGE PARSING FAILED ---")
    
    print(f"{'='*80}")


def save_to_csv(results: List[Dict[str, Any]], filename: str) -> None:
    """Save detailed results to CSV file"""
    if not results:
        logger.warning(f"No results to save to {filename}")
        return
    
    fieldnames = [
        "listing_id",
        "url",
        "title_search",
        "title_page",
        "price_search",
        "price_page",
        "condition_search",
        "condition_page",
        "brand_search",
        "brand_page",
        "description_length",
        "description_preview",
        "shipping_cost",
        "size",
        "color",
        "parsing_success",
    ]
    
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for result in results:
            listing = result['listing']
            details = result['details']
            
            row = {
                "listing_id": listing.get('listing_id', ''),
                "url": listing.get('url', ''),
                "title_search": listing.get('title', ''),
                "title_page": details.get('title', '') if details else '',
                "price_search": listing.get('price', ''),
                "price_page": details.get('price', '') if details else '',
                "condition_search": listing.get('condition_raw', ''),
                "condition_page": details.get('condition', '') if details else '',
                "brand_search": listing.get('brand', ''),
                "brand_page": details.get('brand', '') if details else '',
                "description_length": len(details.get('description', '')) if details and details.get('description') else 0,
                "description_preview": (details.get('description', '')[:200]) if details and details.get('description') else '',
                "shipping_cost": details.get('shipping_cost', '') if details else '',
                "size": details.get('size', '') if details else '',
                "color": details.get('color', '') if details else '',
                "parsing_success": bool(details),
            }
            writer.writerow(row)
    
    logger.info(f"Saved {len(results)} results to {filename}")


async def test_cdiscount_product_pages(keyword: str, limit: int = 5):
    """Test Cdiscount product page parsing"""
    logger.info(f"Starting Cdiscount Product Page Test")
    logger.info(f"Keyword: {keyword}")
    logger.info(f"Limit: {limit}")
    logger.info("=" * 80)
    
    connector = CdiscountConnector()
    
    # Step 1: Search for products
    logger.info(f"\nStep 1: Searching for '{keyword}'...")
    listings = await connector.search_items(keyword, limit=limit)
    
    if not listings:
        logger.error(f"No listings found for '{keyword}'")
        return
    
    logger.success(f"Found {len(listings)} listings from search")
    
    # Step 2: Parse each product page
    logger.info(f"\nStep 2: Parsing product pages to extract detailed information...")
    results = []
    
    for idx, listing in enumerate(listings):
        logger.info(f"\n--- Processing product {idx + 1}/{len(listings)} ---")
        
        # Convert Listing to dict for comparison
        listing_data = {
            'listing_id': listing.listing_id,
            'url': listing.url,
            'title': listing.title,
            'price': listing.price,
            'currency': listing.currency,
            'condition_raw': listing.condition_raw,
            'brand': listing.brand,
            'description': listing.description,
            'shipping_cost': listing.shipping_cost,
        }
        
        # Parse the product page
        try:
            details = await connector.parse_product_page(listing.url)
            
            if details:
                logger.success(f"✓ Successfully parsed product page")
            else:
                logger.warning(f"✗ Failed to parse product page")
            
            results.append({
                'listing': listing_data,
                'details': details,
            })
            
            # Print comparison
            print_comparison(listing_data, details, idx)
            
        except Exception as e:
            logger.error(f"Error parsing product page: {e}")
            results.append({
                'listing': listing_data,
                'details': None,
            })
        
        # Delay between requests
        if idx < len(listings) - 1:
            logger.info("\nWaiting 3 seconds before next product...")
            await asyncio.sleep(3)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    
    total = len(results)
    successful_parses = sum(1 for r in results if r['details'])
    
    print(f"Total products tested: {total}")
    print(f"Successfully parsed: {successful_parses}/{total} ({successful_parses/total*100:.1f}%)")
    
    # Field extraction statistics
    if successful_parses > 0:
        stats = {
            'description': sum(1 for r in results if r['details'] and r['details'].get('description')),
            'brand': sum(1 for r in results if r['details'] and r['details'].get('brand')),
            'condition': sum(1 for r in results if r['details'] and r['details'].get('condition')),
            'shipping': sum(1 for r in results if r['details'] and r['details'].get('shipping_cost') is not None),
            'size': sum(1 for r in results if r['details'] and r['details'].get('size')),
            'color': sum(1 for r in results if r['details'] and r['details'].get('color')),
        }
        
        print(f"\nField extraction success rate (from product pages):")
        for field, count in stats.items():
            pct = (count / successful_parses * 100) if successful_parses > 0 else 0
            print(f"  {field:12s}: {count:2d}/{successful_parses:2d} ({pct:5.1f}%)")
    
    # Save to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("/data") if Path("/data").exists() else Path(".")
    csv_filename = f"cdiscount_product_pages_{timestamp}.csv"
    csv_path = output_dir / csv_filename
    save_to_csv(results, str(csv_path))
    
    print(f"\nResults saved to: {csv_path}")
    print(f"{'='*80}\n")


async def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test Cdiscount product page parsing to extract descriptions and detailed fields",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "keyword",
        help="Search keyword"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of products to test (default: 5)"
    )
    
    args = parser.parse_args()
    
    await test_cdiscount_product_pages(args.keyword, args.limit)


if __name__ == "__main__":
    # Configure logger
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        colorize=True,
    )
    
    # Run
    asyncio.run(main())


