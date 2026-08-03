"""Compass: module navigation guides (Meta 5-question framework).

Deterministic (graph-derived) or LLM-assisted with a bounded critic-driven
revise loop. The deterministic critic fact-checks every file/symbol
reference against the L1 graph. Public API:

    from codegraph.compass import generate_compass, critic_concept, route_query
"""
from codegraph.compass.critic import critic_concept
from codegraph.compass.generator import generate_compass, generate_compass_with_llm
from codegraph.compass.router import classify_intent, route_query

__all__ = [
    "generate_compass",
    "generate_compass_with_llm",
    "critic_concept",
    "route_query",
    "classify_intent",
]
