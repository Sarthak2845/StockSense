import json

import pytest

from contract import InvalidEvent, parse_event

GOOD = {
    "external_event_id": "amz-7f3a91",
    "channel": "amazon",
    "type": "sale",
    "sku": "TIN-8IN",
    "quantity": -1,
    "unit_price": 499.00,
    "order_id": "AMZ-ORD-8812",
    "customer_ref": "amazon-cust-001",
    "occurred_at": "2026-10-18T14:02:11.204Z",
}


def test_accepts_the_contract_example():
    assert parse_event(json.dumps(GOOD)) == GOOD


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": 1},                  # sale must be negative
        {"type": "cancel", "quantity": -1},  # cancel must be positive
        {"channel": "ebay"},
        {"sku": "tin-8in"},
        {"occurred_at": "yesterday"},
        {"unit_price": None},             # sale needs a price
        {"extra": "field"},
    ],
)
def test_rejects_contract_violations(change):
    with pytest.raises(InvalidEvent):
        parse_event(json.dumps({**GOOD, **change}))


def test_rejects_missing_fields_and_bad_json():
    bad = dict(GOOD)
    del bad["external_event_id"]
    with pytest.raises(InvalidEvent):
        parse_event(json.dumps(bad))
    with pytest.raises(InvalidEvent):
        parse_event(b"{not json")
