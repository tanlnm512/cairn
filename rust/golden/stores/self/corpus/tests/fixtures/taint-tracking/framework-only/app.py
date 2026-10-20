"""Fixture: framework-idiomatic flow no generic default call name matches.

checkout_handler reads the request body as an attribute (no call) and
persists it via save_order through the framework call tail
``repository.save``; no default source or sink key matches, so the flow
stays unflagged on defaults and under the fuzzy opt-in.
"""


def checkout_handler(request, repository):
    body = request.body
    return save_order(body, repository)


def save_order(order_body, repository):
    return repository.save(order_body)
