"""Compass: module navigation guides (Meta 5-question framework).

Deterministic (graph-derived) or LLM-assisted with a bounded critic-driven
revise loop. The deterministic critic fact-checks every file/symbol
reference against the L1 graph. Public API:

    from cairn.compass import generate_compass, critic_concept, route_query
"""
from cairn.compass.critic import critic_concept
from cairn.compass.generator import generate_compass, generate_compass_with_llm
from cairn.compass.router import classify_intent, route_query

__all__ = [
    "generate_compass",
    "generate_compass_with_llm",
    "critic_concept",
    "route_query",
    "classify_intent",
]
