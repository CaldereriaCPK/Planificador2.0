from datetime import date

from flask import render_template_string
import pdfkit


pdfkit.configuration = lambda **kwargs: None

from app import app, _worker_day_capacity


FRIDAY = date(2026, 5, 8)


def test_worker_day_capacity_uses_friday_default_before_worker_limit():
    assert _worker_day_capacity('Mikel', FRIDAY, {}, {}) == 7


def test_worker_day_capacity_allows_global_friday_override():
    assert _worker_day_capacity('Mikel', FRIDAY, {'2026-05-08': 9}, {}) == 9


def test_worker_day_capacity_worker_day_override_wins_over_global():
    assert _worker_day_capacity(
        'Mikel',
        FRIDAY,
        {'2026-05-08': 9},
        {'Mikel': {'2026-05-08': 6}},
    ) == 6


def test_incomplete_cell_condition_does_not_mark_full_friday():
    template = """
    {% set total = 7 %}
    {% set limit = worker_day_capacity('Mikel', day, hours, worker_day_hours) %}
    {% if total < limit %}incomplete{% else %}complete{% endif %}
    """
    with app.test_request_context():
        rendered = render_template_string(
            template,
            worker_day_capacity=_worker_day_capacity,
            day=FRIDAY,
            hours={},
            worker_day_hours={},
        )

    assert rendered.strip() == 'complete'
