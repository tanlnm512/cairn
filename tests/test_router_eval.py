"""Router golden-query eval: verify classify_intent accuracy >= 85%.

No DB or bundle needed — classify_intent is pure string matching.
"""
import pytest

from cairn.compass.router import classify_intent


# (query, expected_layer)
GOLDEN = [
    # L1: graph
    ("where is UserRepository defined", "L1"),
    ("find definition of AuthService", "L1"),
    ("who calls processOrder", "L1"),
    ("what calls PaymentGateway.charge", "L1"),
    ("impact of changing the return type of getUserId", "L1"),
    ("what if I change validateInput", "L1"),
    ("blast radius of retryPayment", "L1"),
    ("get callers of fetchUser", "L1"),
    ("callees of handleRequest", "L1"),
    ("invokes buildPipeline", "L1"),

    # L2: wiki / feature understanding
    ("how does the checkout flow work", "L2"),
    ("how do we process refunds", "L2"),
    ("what is the authentication flow", "L2"),
    ("how does order routing work", "L2"),
    ("process for handling returns", "L2"),
    ("architecture of the payment module", "L2"),
    ("design pattern for the cache layer", "L2"),
    ("what is the retry strategy pattern", "L2"),

    # L3: compass
    ("navigate the inventory module", "L3"),
    ("where do I start with the shipping code", "L3"),
    ("guide for onboarding to the billing module", "L3"),
    ("gotchas in the data pipeline", "L3"),
    ("watch out for race conditions here", "L3"),
    ("non-obvious patterns in the auth module", "L3"),
    ("pitfalls when working with the scheduler", "L3"),

    # L4: memory
    ("why did we choose PostgreSQL over MySQL", "L4"),
    ("decision to migrate to gRPC", "L4"),
    ("rationale for the caching strategy", "L4"),
    ("mistake we made with the batch processor", "L4"),
    ("error we hit in the notification service", "L4"),
    ("wrong assumption about thread safety", "L4"),
    ("forgot to handle the timeout case", "L4"),
    ("bug we hit in production last week", "L4"),

    # L5: knowledge / business
    ("business rule for refund eligibility", "L5"),
    ("impact of changing the pricing policy", "L5"),
    ("affects which repos if the SLA changes", "L5"),
    ("what repos depend on the loyalty program", "L5"),
    ("requirement for GDPR compliance", "L5"),
    ("epic for multi-currency support", "L5"),
    ("business policy for order cancellation", "L5"),
    ("policy for data retention", "L5"),
]


def test_router_accuracy():
    """classify_intent should hit >= 85% of golden queries."""
    hits = 0
    misses = []
    for query, expected_layer in GOLDEN:
        result = classify_intent(query)
        if result["layer"] == expected_layer:
            hits += 1
        else:
            misses.append((query, expected_layer, result["layer"]))
    accuracy = hits / len(GOLDEN)
    assert accuracy >= 0.85, (
        f"Router accuracy {accuracy:.1%} < 85%. Misses ({len(misses)}): "
        + "; ".join(f"'{q}' expected {e} got {g}" for q, e, g in misses[:5])
    )


def test_unknown_queries_fall_to_ALL():
    """Queries matching no pattern should route to ALL layers."""
    for q in ["hello", "something random", "tell me about stuff"]:
        result = classify_intent(q)
        assert result["layer"] == "ALL", f"'{q}' should route to ALL, got {result['layer']}"
        assert result["intent"] == "complex"
