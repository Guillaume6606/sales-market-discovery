"""Listing Explorer API contracts against real PostgreSQL."""

import pytest
from fastapi.testclient import TestClient

from libs.common.models import ListingObservation


def test_explore_total_counts_only_listings_matching_score_filters(
    client: TestClient, scored_listings: list[ListingObservation]
) -> None:
    response = client.get("/listings/explore", params={"min_confidence": 10})

    assert response.status_code == 200
    body = response.json()
    assert [row["obs_id"] for row in body["listings"]] == [scored_listings[1].obs_id]
    assert body["total"] == 1


@pytest.mark.parametrize("sort_by", ["spread", "confidence", "roi"])
def test_explore_score_sorts_rank_scored_listings_before_unscored(
    client: TestClient, scored_listings: list[ListingObservation], sort_by: str
) -> None:
    response = client.get("/listings/explore", params={"sort_by": sort_by, "sort_order": "desc"})

    assert response.status_code == 200
    assert response.json()["listings"][0]["obs_id"] == scored_listings[1].obs_id
