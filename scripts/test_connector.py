"""
Generic test script for marketplace connectors
Tests searches for specified keywords and saves results to CSV

Usage:
    docker compose exec ingestion xvfb-run -a python test_connector.py CONNECTOR_NAME KEYWORD1 [KEYWORD2 ...]
    docker compose exec ingestion xvfb-run -a python test_connector.py --all KEYWORD1 [KEYWORD2 ...]

Examples:
    docker compose exec ingestion xvfb-run -a python test_connector.py vinted "Nike Air Max"
    docker compose exec ingestion xvfb-run -a python test_connector.py backmarket "iPhone 13" "Macbook Pro"
    docker compose exec ingestion xvfb-run -a python test_connector.py --all "iPhone 13"
"""
import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from loguru import logger
from libs.common.models import Listing


AVAILABLE_CONNECTORS = {
    "ebay": "ingestion.connectors.ebay",
    "leboncoin": "ingestion.connectors.leboncoin",
    "vinted": "ingestion.connectors.vinted",
    "backmarket": "ingestion.connectors.backmarket_connector",
    "cdiscount": "ingestion.connectors.cdiscount_connector",
    "fnac": "ingestion.connectors.fnac_connector",
    "rakuten": "ingestion.connectors.rakuten_connector",
}

CONNECTOR_CLASSES = {
    "ebay": None,  # Uses functions, not a class
    "leboncoin": "LeBonCoinConnector",
    "vinted": "VintedConnector",
    "backmarket": "BackmarketConnector",
    "cdiscount": "CdiscountConnector",
    "fnac": "FnacConnector",
    "rakuten": "RakutenConnector",
}


def get_connector(connector_name: str):
    """Dynamically import and instantiate a connector"""
    if connector_name not in AVAILABLE_CONNECTORS:
        raise ValueError(f"Unknown connector: {connector_name}. Available: {', '.join(AVAILABLE_CONNECTORS.keys())}")
    
    module_path = AVAILABLE_CONNECTORS[connector_name]
    class_name = CONNECTOR_CLASSES[connector_name]
    
    # Import the module
    import importlib
    module = importlib.import_module(module_path)
    
    # Get the connector class or function
    if class_name:
        connector_class = getattr(module, class_name)
        return connector_class()
    else:
        # For eBay, return the module (uses functions)
        return module


async def search_with_connector(connector, connector_name: str, keyword: str, limit: int = 20) -> List[Listing]:
    """Search using the appropriate connector method"""
    if connector_name == "ebay":
        # eBay uses function-based approach
        from ingestion.connectors.ebay import fetch_ebay_listings
        return await fetch_ebay_listings(keyword, limit=limit)
    else:
        # Other connectors use class-based approach
        return await connector.search_items(keyword, limit=limit)


def print_listing(listing: Listing, index: int) -> None:
    """Pretty print a single listing"""
    print(f"\n{'='*80}")
    print(f"Listing #{index + 1}")
    print(f"{'='*80}")
    print(f"ID:          {listing.listing_id}")
    print(f"Title:       {listing.title}")
    print(f"Price:       {listing.price} {listing.currency}")
    print(f"Condition:   {listing.condition_raw} (normalized: {listing.condition_norm})")
    print(f"Brand:       {listing.brand or 'N/A'}")
    print(f"Location:    {listing.location or 'N/A'}")
    print(f"Shipping:    {listing.shipping_cost} {listing.currency if listing.shipping_cost else 'N/A'}")
    print(f"URL:         {listing.url}")
    if listing.description:
        desc_preview = listing.description[:100] + "..." if len(listing.description) > 100 else listing.description
        print(f"Description: {desc_preview}")
    print(f"{'='*80}")


def save_to_csv(listings: List[Listing], filename: str) -> None:
    """Save listings to CSV file"""
    if not listings:
        logger.warning(f"No listings to save to {filename}")
        return
    
    # Define CSV columns
    fieldnames = [
        "source",
        "listing_id",
        "title",
        "description",
        "price",
        "currency",
        "condition_raw",
        "condition_norm",
        "location",
        "seller_rating",
        "shipping_cost",
        "observed_at",
        "is_sold",
        "url",
        "brand",
        "size",
        "color",
    ]
    
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for listing in listings:
            # Convert Listing to dict and write
            row = {
                "source": listing.source,
                "listing_id": listing.listing_id,
                "title": listing.title,
                "description": listing.description or "",
                "price": listing.price,
                "currency": listing.currency,
                "condition_raw": listing.condition_raw or "",
                "condition_norm": listing.condition_norm or "",
                "location": listing.location or "",
                "seller_rating": listing.seller_rating,
                "shipping_cost": listing.shipping_cost,
                "observed_at": listing.observed_at.isoformat() if listing.observed_at else "",
                "is_sold": listing.is_sold,
                "url": listing.url or "",
                "brand": listing.brand or "",
                "size": listing.size or "",
                "color": listing.color or "",
            }
            writer.writerow(row)
    
    logger.info(f"Saved {len(listings)} listings to {filename}")


async def test_search(connector, connector_name: str, keyword: str, limit: int = 20, test_product_pages: bool = False) -> List[Listing]:
    """Test search for a specific keyword"""
    logger.info(f"\n{'#'*80}")
    logger.info(f"# Testing {connector_name.upper()} search for: {keyword}")
    logger.info(f"{'#'*80}\n")
    
    try:
        listings = await search_with_connector(connector, connector_name, keyword, limit=limit)
        
        if not listings:
            logger.warning(f"No listings found for '{keyword}' on {connector_name}")
            return []
        
        logger.success(f"Found {len(listings)} listings for '{keyword}' on {connector_name}")
        
        # Analyze field population from search results
        total = len(listings)
        field_stats = {
            "title": sum(1 for l in listings if l.title),
            "price": sum(1 for l in listings if l.price is not None),
            "description": sum(1 for l in listings if l.description),
            "condition": sum(1 for l in listings if l.condition_raw),
            "url": sum(1 for l in listings if l.url),
            "brand": sum(1 for l in listings if l.brand),
            "shipping": sum(1 for l in listings if l.shipping_cost is not None),
        }
        
        logger.info("Field population statistics (from search results):")
        for field, count in field_stats.items():
            pct = (count / total * 100) if total > 0 else 0
            logger.info(f"  {field:12s}: {count:3d}/{total:3d} ({pct:5.1f}%)")
        
        # Optionally test product page parsing for first result
        if test_product_pages and listings and hasattr(connector, 'parse_product_page'):
            logger.info(f"\n--- Testing parse_product_page on first result ---")
            first_listing = listings[0]
            try:
                details = await connector.parse_product_page(first_listing.url)
                if details:
                    logger.success(f"✓ Successfully parsed product page")
                    logger.info(f"  Description: {'✓' if details.get('description') else '✗'} ({len(details.get('description', ''))} chars)")
                    logger.info(f"  Brand: {details.get('brand', 'N/A')}")
                    logger.info(f"  Condition: {details.get('condition', 'N/A')}")
                    logger.info(f"  Shipping: {details.get('shipping_cost', 'N/A')}")
                else:
                    logger.warning(f"✗ Failed to parse product page")
            except Exception as e:
                logger.error(f"Error parsing product page: {e}")
        
        # Print first 3 listings as preview
        for idx, listing in enumerate(listings[:3]):
            print_listing(listing, idx)
        
        if len(listings) > 3:
            logger.info(f"... and {len(listings) - 3} more listings")
        
        return listings
        
    except Exception as e:
        logger.error(f"Error during search for '{keyword}' on {connector_name}: {e}", exc_info=True)
        return []


async def test_connector(connector_name: str, keywords: List[str], limit: int = 20, test_product_pages: bool = False):
    """Test a single connector with given keywords"""
    logger.info(f"Testing {connector_name.upper()} connector")
    logger.info("=" * 80)
    
    # Initialize connector
    try:
        connector = get_connector(connector_name)
    except ValueError as e:
        logger.error(str(e))
        return []
    
    all_listings = []
    
    # Run searches
    for keyword in keywords:
        listings = await test_search(connector, connector_name, keyword, limit=limit, test_product_pages=test_product_pages)
        all_listings.extend(listings)
        
        # Small delay between searches to be polite
        if keyword != keywords[-1]:
            logger.info("\nWaiting 3 seconds before next search...\n")
            await asyncio.sleep(3)
    
    return all_listings, connector_name


async def main():
    """Main test function"""
    # Parse command-line arguments with argparse
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test marketplace connectors by searching for keywords",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--connector",
        help="Connector to test (or '--all' for all connectors)"
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=["iPhone 13", "Macbook Pro"],
        help="Search keywords (default: 'iPhone 13' 'Macbook Pro')"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of results per search (default: 5)"
    )
    parser.add_argument(
        "--test-product-pages",
        action="store_true",
        help="Also test parse_product_page() on the first result to extract detailed fields"
    )
    
    args = parser.parse_args()
    
    connector_arg = args.connector.lower()
    keywords = args.keywords
    limit = args.limit
    test_product_pages = args.test_product_pages
    
    logger.info("Starting Marketplace Connector Test Script")
    logger.info("=" * 80)
    
    # Determine which connectors to test
    if connector_arg == "all":
        connectors_to_test = list(AVAILABLE_CONNECTORS.keys())
        logger.info(f"Testing ALL connectors: {', '.join(connectors_to_test)}")
    else:
        connectors_to_test = [connector_arg]
        logger.info(f"Testing connector: {connector_arg}")
    
    logger.info(f"Keywords: {', '.join(keywords)}")
    logger.info("=" * 80)
    
    # Test each connector
    all_results = {}
    for connector_name in connectors_to_test:
        try:
            listings, name = await test_connector(connector_name, keywords, limit=limit, test_product_pages=test_product_pages)
            all_results[name] = listings
            
            # Delay between different connectors
            if connector_name != connectors_to_test[-1]:
                logger.info("\nWaiting 5 seconds before testing next connector...\n")
                await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Failed to test {connector_name}: {e}", exc_info=True)
            all_results[connector_name] = []
    
    # Overall Summary
    print(f"\n{'='*80}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*80}")
    
    total_listings = sum(len(listings) for listings in all_results.values())
    print(f"Total listings found: {total_listings}")
    print(f"\nResults by connector:")
    for connector_name, listings in all_results.items():
        print(f"  - {connector_name}: {len(listings)} listings")
    
    # Save results to CSV (one file per connector)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("/data") if Path("/data").exists() else Path(".")
    
    for connector_name, listings in all_results.items():
        if listings:
            csv_filename = f"{connector_name}_test_results_{timestamp}.csv"
            csv_path = output_dir / csv_filename
            save_to_csv(listings, str(csv_path))
            print(f"\n{connector_name} results saved to: {csv_path}")
            
            # Print statistics for this connector
            prices = [l.price for l in listings if l.price is not None]
            if prices:
                print(f"  Price range: {min(prices):.2f} - {max(prices):.2f} EUR")
                print(f"  Average price: {sum(prices) / len(prices):.2f} EUR")
    
    if total_listings == 0:
        logger.warning("No listings found in total")
    
    print(f"{'='*80}\n")


if __name__ == "__main__":
    # Configure logger for better output
    logger.remove()  # Remove default handler
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        colorize=True,
    )
    
    # Run the async main function
    asyncio.run(main())

