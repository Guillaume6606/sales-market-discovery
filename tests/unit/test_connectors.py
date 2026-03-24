"""Unit tests for marketplace connector parsing logic (Task 3.5)."""

from ingestion.connectors.ebay import parse_ebay_response
from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector
from ingestion.connectors.vinted import VintedConnector
from ingestion.connectors.vinted_api import VintedAPIConnector
from libs.common.condition import normalize_condition

# =========================================================================== #
# eBay tests
# =========================================================================== #


def _make_ebay_item(
    item_id: str = "123456",
    title: str = "Sony WH-1000XM4",
    price: str = "199.99",
    currency: str = "EUR",
    condition: str = "Used",
    location: str = "Paris, France",
) -> dict:
    """Build a realistic eBay Finding API item dict."""
    return {
        "itemId": [item_id],
        "title": [title],
        "sellingStatus": [
            {
                "currentPrice": [{"__value__": [price], "@currencyId": [currency]}],
                "sellingState": ["EndedWithSales"],
            }
        ],
        "condition": [{"conditionDisplayName": [condition]}],
        "location": [location],
        "sellerInfo": [{"feedbackScore": ["1500"]}],
        "shippingInfo": [
            {
                "shippingServiceCost": [{"__value__": ["5.99"], "@currencyId": ["EUR"]}],
            }
        ],
    }


def _make_ebay_response(items: list[dict], count: int | None = None) -> dict:
    """Wrap items in eBay Finding API response structure."""
    if count is None:
        count = len(items)
    return {
        "searchResult": [{"@count": [str(count)], "item": items}],
    }


class TestEbayParsing:
    def test_basic_listing_extraction(self):
        item = _make_ebay_item()
        response = _make_ebay_response([item])
        listings = parse_ebay_response(response, is_sold=True)

        assert len(listings) == 1
        listing = listings[0]
        assert listing.listing_id == "123456"
        assert listing.title == "Sony WH-1000XM4"
        assert listing.price == 199.99
        assert listing.source == "ebay"
        assert listing.is_sold is True
        assert listing.url == "https://www.ebay.com/itm/123456"
        assert listing.condition_raw == "Used"

    def test_multiple_items(self):
        items = [
            _make_ebay_item(item_id="1", title="Item A", price="100.00"),
            _make_ebay_item(item_id="2", title="Item B", price="200.00"),
        ]
        response = _make_ebay_response(items)
        listings = parse_ebay_response(response, is_sold=False)

        assert len(listings) == 2
        assert listings[0].listing_id == "1"
        assert listings[1].listing_id == "2"
        assert listings[0].is_sold is False

    def test_zero_price_skipped(self):
        item = _make_ebay_item(price="0")
        response = _make_ebay_response([item])
        listings = parse_ebay_response(response, is_sold=True)

        assert len(listings) == 0

    def test_missing_price_skipped(self):
        item = {
            "itemId": ["999"],
            "title": ["No Price Item"],
            "sellingStatus": [{}],
        }
        response = _make_ebay_response([item])
        listings = parse_ebay_response(response, is_sold=True)

        assert len(listings) == 0

    def test_empty_results(self):
        response = {"searchResult": [{"@count": ["0"]}]}
        listings = parse_ebay_response(response, is_sold=True)

        assert listings == []

    def test_no_search_result(self):
        listings = parse_ebay_response({}, is_sold=True)
        assert listings == []

    def test_error_message_in_response(self):
        response = {"errorMessage": [{"error": [{"message": ["API key invalid"]}]}]}
        listings = parse_ebay_response(response, is_sold=True)
        assert listings == []

    def test_condition_normalization(self):
        from ingestion.connectors.ebay import normalize_condition

        assert normalize_condition("Brand New") == "new"
        assert normalize_condition("Like New") == "like_new"
        assert normalize_condition("Excellent") == "like_new"
        assert normalize_condition("Mint") == "like_new"
        assert normalize_condition("Very Good") == "good"
        assert normalize_condition("Acceptable") == "fair"
        assert normalize_condition("") is None
        assert normalize_condition("unknown_condition") is None

    def test_seller_rating_extraction(self):
        item = _make_ebay_item()
        response = _make_ebay_response([item])
        listings = parse_ebay_response(response, is_sold=True)

        assert listings[0].seller_rating == 1500.0

    def test_shipping_cost_extraction(self):
        item = _make_ebay_item()
        response = _make_ebay_response([item])
        listings = parse_ebay_response(response, is_sold=True)

        assert listings[0].shipping_cost == 5.99


# =========================================================================== #
# LeBonCoin API tests
# =========================================================================== #


class TestLeBonCoinAPIParsing:
    def setup_method(self):
        self.connector = LeBonCoinAPIConnector()

    def test_basic_ad_mapping(self):
        ad = {
            "list_id": "12345",
            "subject": "iPhone 14 Pro",
            "price": 500,
            "location": {"city": "Lyon", "zipcode": "69001"},
            "url": "https://www.leboncoin.fr/ad/12345",
        }
        listing = self.connector._map_ad_to_listing(ad)

        assert listing is not None
        assert listing.listing_id == "12345"
        assert listing.title == "iPhone 14 Pro"
        assert listing.price == 500.0
        assert listing.source == "leboncoin"
        assert listing.url == "https://www.leboncoin.fr/ad/12345"

    def test_nested_price_dict(self):
        ad = {
            "list_id": "67890",
            "subject": "PS5 Console",
            "price": {"amount": 350, "currency": "EUR"},
        }
        listing = self.connector._map_ad_to_listing(ad)

        assert listing is not None
        assert listing.price == 350.0
        assert listing.currency == "EUR"

    def test_price_as_float(self):
        ad = {
            "list_id": "11111",
            "subject": "Nintendo Switch",
            "price": 249.99,
        }
        listing = self.connector._map_ad_to_listing(ad)

        assert listing is not None
        assert listing.price == 249.99

    def test_location_extraction(self):
        ad = {
            "list_id": "22222",
            "subject": "Test",
            "price": 100,
            "location": {"city": "Paris", "zipcode": "75001"},
        }
        listing = self.connector._map_ad_to_listing(ad)

        assert listing is not None
        assert "Paris" in listing.location
        assert "75001" in listing.location

    def test_condition_normalization(self):
        assert normalize_condition("Neuf") == "new"
        assert normalize_condition("Très bon état") == "like_new"
        assert normalize_condition("Bon état") == "good"
        assert normalize_condition("Satisfaisant") == "fair"
        assert normalize_condition("") is None

    def test_missing_listing_id_returns_none(self):
        ad = {
            "subject": "No ID Ad",
            "price": 100,
        }
        listing = self.connector._map_ad_to_listing(ad)
        assert listing is None

    def test_ad_with_condition(self):
        ad = {
            "list_id": "33333",
            "subject": "Used Camera",
            "price": 200,
            "condition": "Bon état",
        }
        listing = self.connector._map_ad_to_listing(ad)

        assert listing is not None
        assert listing.condition_raw == "Bon état"
        assert listing.condition_norm == "good"


# =========================================================================== #
# Vinted tests
# =========================================================================== #


class TestVintedParsing:
    def setup_method(self):
        self.connector = VintedConnector()

    def test_extract_listing_id_from_url(self):
        url = "https://www.vinted.fr/items/12345-some-title"
        listing_id = self.connector._extract_listing_id(url)
        assert listing_id == "12345"

    def test_extract_listing_id_no_match(self):
        url = "https://www.vinted.fr/member/profile"
        listing_id = self.connector._extract_listing_id(url)
        assert listing_id == ""

    def test_condition_normalization(self):
        assert normalize_condition("neuf") == "new"
        assert normalize_condition("Neuf avec étiquette") == "new"
        assert normalize_condition("très bon état") == "like_new"
        assert normalize_condition("bon état") == "good"
        assert normalize_condition("satisfaisant") == "fair"
        assert normalize_condition("") is None

    def test_validate_item_data_valid(self):
        item = {
            "listing_id": "12345",
            "title": "Nike Air Max 90",
            "price": 75.0,
        }
        assert self.connector._validate_item_data(item) is True

    def test_validate_item_data_invalid_price(self):
        item = {
            "listing_id": "12345",
            "title": "Nike Air Max 90",
            "price": None,
        }
        assert self.connector._validate_item_data(item) is False

    def test_validate_item_data_price_too_low(self):
        item = {
            "listing_id": "12345",
            "title": "Nike Air Max 90",
            "price": 0.1,
        }
        assert self.connector._validate_item_data(item) is False

    def test_validate_item_data_non_numeric_id(self):
        item = {
            "listing_id": "abc-not-numeric",
            "title": "Nike Air Max 90",
            "price": 50.0,
        }
        assert self.connector._validate_item_data(item) is False

    def test_validate_item_data_short_title(self):
        item = {
            "listing_id": "12345",
            "title": "AB",
            "price": 50.0,
        }
        assert self.connector._validate_item_data(item) is False

    def test_validate_item_data_empty_items(self):
        """Test empty search results produce empty list."""
        # _parse_search_results with no item elements returns empty
        result = self.connector._parse_search_results("<html><body></body></html>")
        assert result == []

    def test_warmup_session_is_async(self) -> None:
        import inspect

        method = getattr(self.connector, "_warmup_session", None)
        assert method is not None, "_warmup_session not found on VintedConnector"
        assert inspect.iscoroutinefunction(method)

    def test_warmup_session_accepts_session_arg(self) -> None:
        import inspect

        sig = inspect.signature(self.connector._warmup_session)
        assert "session" in sig.parameters


# =========================================================================== #
# Vinted API tests
# =========================================================================== #


class TestVintedAPIConnector:
    def setup_method(self) -> None:
        self.connector = VintedAPIConnector()

    def test_map_item_to_listing_basic(self) -> None:
        """Map a VintedItem-like dict to a Listing."""
        item_data = {
            "id": 12345,
            "title": "Samsung Galaxy S24 128GB",
            "price": {"amount": "450.00", "currency_code": "EUR"},
            "url": "/items/12345-samsung-galaxy",
            "status": "active",
            "brand_title": "Samsung",
            "size_title": "M",
            "color1": "Black",
            "localization": "Paris, France",
        }
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.source == "vinted"
        assert listing.listing_id == "12345"
        assert listing.price == 450.0
        assert listing.currency == "EUR"
        assert listing.title == "Samsung Galaxy S24 128GB"
        assert listing.brand == "Samsung"
        assert listing.url.endswith("/items/12345-samsung-galaxy")

    def test_map_item_to_listing_no_id_returns_none(self) -> None:
        listing = self.connector._map_item_to_listing({})
        assert listing is None

    def test_map_item_to_listing_price_as_float(self) -> None:
        item_data = {"id": 99, "title": "Test", "price": 25.0}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.price == 25.0

    def test_map_item_sold_detection(self) -> None:
        item_data = {"id": 1, "title": "Sold Item", "price": 10.0, "is_closed": True}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.is_sold is True

    def test_map_item_reserved_detection(self) -> None:
        item_data = {"id": 2, "title": "Reserved Item", "price": 15.0, "is_reserved": True}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.is_sold is True

    def test_condition_normalization(self) -> None:
        assert normalize_condition("neuf") == "new"
        assert normalize_condition("très bon état") == "like_new"
        assert normalize_condition("bon état") == "good"
        assert normalize_condition("satisfaisant") == "fair"
        assert normalize_condition("") is None

    def test_map_item_to_listing_price_as_int(self) -> None:
        item_data = {"id": 100, "title": "Int Price Item", "price": 30}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.price == 30.0

    def test_map_item_to_listing_price_as_string(self) -> None:
        item_data = {"id": 101, "title": "Str Price Item", "price": "42.50"}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.price == 42.50

    def test_map_item_to_listing_url_prefix(self) -> None:
        """Relative URL gets prefixed with BASE_URL."""
        item_data = {"id": 200, "title": "Relative URL", "price": 10.0, "url": "/items/200-test"}
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.url == "https://www.vinted.fr/items/200-test"

    def test_map_item_to_listing_absolute_url(self) -> None:
        """Absolute URL is kept as-is."""
        item_data = {
            "id": 201,
            "title": "Absolute URL",
            "price": 10.0,
            "url": "https://www.vinted.fr/items/201-test",
        }
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.url == "https://www.vinted.fr/items/201-test"

    def test_map_item_to_listing_size_and_color(self) -> None:
        item_data = {
            "id": 300,
            "title": "With Size & Color",
            "price": 20.0,
            "size_title": "L",
            "color1": "Blue",
        }
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.size == "L"
        assert listing.color == "Blue"

    def test_map_item_to_listing_location(self) -> None:
        item_data = {
            "id": 400,
            "title": "With Location",
            "price": 50.0,
            "localization": "Lyon, France",
        }
        listing = self.connector._map_item_to_listing(item_data)
        assert listing is not None
        assert listing.location == "Lyon, France"
