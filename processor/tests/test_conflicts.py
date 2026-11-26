from datetime import datetime, timedelta, timezone

import pytest

from conflicts import Candidate, ScoringConfig, customer_value, explain, resolve, score_breakdown

T0 = datetime(2026, 10, 18, 14, 2, 11, tzinfo=timezone.utc)
CFG = ScoringConfig()


def cand(event_id, channel, *, price=499.0, cost=240.0, ltv=0.0, penalty=0.0, ms=0, units=1, order=None):
    return Candidate(
        event_id=event_id, channel=channel, order_id=order or f"{channel.upper()}-{event_id}", units=units,
        unit_price=price, cost_price=cost, lifetime_value=ltv, cancel_penalty=penalty,
        occurred_at=T0 + timedelta(milliseconds=ms),
    )


def test_score_formula_matches_prd():
    c = cand(1, "amazon", price=499, cost=240, ltv=2500, penalty=250, ms=100)
    b = score_breakdown(c, T0, CFG)
    # margin 259 + 0.5 * 50 (2500 of a 5000 cap) + 250 penalty - 0.01 * 100 ms
    assert b["margin"] == 259
    assert b["customer_value"] == 50
    assert b["score"] == pytest.approx(259 + 25 + 250 - 1)


def test_customer_value_is_capped_and_never_negative():
    assert customer_value(10_000, 5000) == 100
    assert customer_value(-5, 5000) == 0
    assert customer_value(100, 0) == 0


def test_incoming_with_higher_score_takes_the_unit_from_a_lower_one():
    held = cand(1, "website", penalty=20)                # already holds the last unit
    incoming = cand(2, "amazon", penalty=250, ms=3)       # marketplace: costly to cancel
    res = resolve(incoming, [held], available=0, cfg=CFG)
    assert res.incoming_wins
    assert res.losers == [held]
    assert res.winner == incoming
    outcomes = {s["event_id"]: s["outcome"] for s in res.scores}
    assert outcomes == {1: "cancelled", 2: "kept"}


def test_incoming_with_lower_score_is_cancelled():
    held = cand(1, "amazon", penalty=250)
    incoming = cand(2, "website", penalty=20, ms=3)
    res = resolve(incoming, [held], available=0, cfg=CFG)
    assert not res.incoming_wins
    assert res.losers == [incoming]
    assert res.winner == held


def test_exact_tie_goes_to_the_earlier_order():
    held = cand(1, "flipkart", penalty=200)
    incoming = cand(2, "flipkart", penalty=200, ms=5)
    res = resolve(incoming, [held], available=0, cfg=CFG)
    assert res.losers == [incoming]


def test_out_of_stock_with_no_competitors_cancels_incoming():
    incoming = cand(1, "amazon", penalty=250)
    res = resolve(incoming, [], available=0, cfg=CFG)
    assert res.losers == [incoming]
    assert res.winner is None
    assert "out of stock" in explain("TIN-8IN", 0, incoming, res, incoming)


def test_does_not_cancel_two_orders_worth_more_together_to_save_one():
    a = cand(1, "flipkart", penalty=200)                   # ~459
    b = cand(2, "flipkart", penalty=200, ms=1)             # ~459
    incoming = cand(3, "amazon", penalty=250, units=2, ms=2)  # needs 2 units, ~768 < 918
    res = resolve(incoming, [a, b], available=0, cfg=CFG)
    assert not res.incoming_wins
    assert res.losers == [incoming]


def test_uses_free_units_first():
    held = cand(1, "website", penalty=20)
    incoming = cand(2, "amazon", penalty=250, units=2, ms=1)   # 1 free + needs 1 more
    res = resolve(incoming, [held], available=1, cfg=CFG)
    assert res.incoming_wins
    assert res.losers == [held]


def test_explanation_names_both_orders_and_a_reason():
    held = cand(1, "website", penalty=20, order="WEB-4521")
    incoming = cand(2, "amazon", penalty=250, ms=3, order="AMZ-8812")
    res = resolve(incoming, [held], available=0, cfg=CFG)
    text = explain("TIN-8IN", 0, incoming, res, held)
    assert "AMZ-8812" in text and "WEB-4521" in text
    assert "cancellation penalty" in text


def test_scoring_config_loads_from_json():
    cfg = ScoringConfig.load()
    assert cfg.w_margin == 1.0 and cfg.conflict_window_sec == 2.0
