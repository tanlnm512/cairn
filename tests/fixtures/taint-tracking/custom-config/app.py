"""Fixture: queue-message flow matched only through workspace-config labels.

consume_job receives a work-queue message via the ``queue.receive_job``
call tail and passes the payload to render_invoice, which renders a
template via the ``renderer.render_template`` call tail. cairn.json
declares the ``queue-msg`` source and the ``render`` sink.
"""


def consume_job(queue, renderer):
    payload = queue.receive_job()
    return render_invoice(payload, renderer)


def render_invoice(payload, renderer):
    return renderer.render_template(payload)
