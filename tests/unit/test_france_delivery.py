from datetime import UTC, datetime
from types import SimpleNamespace

from ingestion.valuation import _effective_observation, _listing_reasons


def test_unknown_france_delivery_blocks_even_with_shipping_price():
    observation = SimpleNamespace(shipping_cost=5, delivery_to_france=None, delivery_evidence=None)
    assert "france_delivery_unconfirmed" in _listing_reasons(observation, datetime.now(UTC))


def test_explicit_no_delivery_blocks():
    observation = SimpleNamespace(delivery_to_france=False, delivery_evidence="Pickup only")
    assert "france_delivery_unavailable" in _listing_reasons(observation, datetime.now(UTC))


def test_delivery_requires_evidence():
    observation = SimpleNamespace(delivery_to_france=True, delivery_evidence=None)
    assert "france_delivery_unconfirmed" in _listing_reasons(observation, datetime.now(UTC))


def test_manual_delivery_review_applies():
    observation = SimpleNamespace(delivery_to_france=None, delivery_evidence=None)
    review = SimpleNamespace(
        reviewed_title="PS5",
        reviewed_condition="good",
        reviewed_shipping_cost_eur=5,
        reviewed_delivery_to_france=True,
        delivery_evidence="Checkout to France verified",
    )
    effective = _effective_observation(observation, review)
    assert effective.delivery_to_france is True
    assert effective.delivery_evidence == "Checkout to France verified"


def test_ebay_parser_does_not_infer_delivery_from_eur_or_french_url():
    from ingestion.connectors.ebay import parse_ebay_browse_response

    payload = {
        "itemSummaries": [
            {
                "itemId": "v1|123|0",
                "title": "Sony camera",
                "price": {"value": "300", "currency": "EUR"},
                "itemWebUrl": "https://www.ebay.fr/itm/123",
            }
        ]
    }
    assert parse_ebay_browse_response(payload)[0].delivery_to_france is None
    listing = parse_ebay_browse_response(payload, delivery_country="FR")[0]
    assert listing.delivery_to_france is True
    assert "deliveryCountry:FR" in listing.delivery_evidence


def test_review_is_bound_to_listing_url():
    from ingestion.valuation import _review_matches_observation

    observation = SimpleNamespace(
        obs_id=1,
        title="PS5",
        condition="good",
        price=300,
        shipping_cost=5,
        delivery_to_france=None,
        delivery_evidence=None,
        url="https://example.com/new-seller",
    )
    review = SimpleNamespace(
        is_active=True,
        obs_id=1,
        raw_title="PS5",
        raw_condition="good",
        raw_price_eur=300,
        raw_shipping_cost_eur=5,
        raw_delivery_to_france=None,
        raw_delivery_evidence=None,
        raw_url="https://example.com/original",
    )
    assert not _review_matches_observation(review, observation)


def test_ebay_shipping_cost_requires_france_and_eur_context():
    from ingestion.connectors.ebay import _shipping_cost_from_options

    def item(currency="EUR", country="FR", kind="FIXED"):
        return {
            "shippingOptions": [
                {
                    "shippingCostType": kind,
                    "shippingCost": {"value": "5.99", "currency": currency},
                    "shipToLocationUsedForEstimate": {"country": country},
                }
            ]
        }

    assert _shipping_cost_from_options(item()) == 5.99
    assert _shipping_cost_from_options(item(country="DE")) is None
    assert _shipping_cost_from_options(item(currency="USD")) is None
    assert _shipping_cost_from_options(item(kind="CALCULATED")) is None
