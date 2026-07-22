from datetime import date

from schedule import assign_phase


def _assign_on_friday(hours_map=None, worker_day_map=None):
    friday = date(2026, 5, 8)
    schedule = {}
    result = assign_phase(
        schedule,
        friday,
        0,
        'soldar',
        'Proyecto viernes',
        'Cliente',
        10,
        None,
        '#ddd',
        'Mikel',
        friday.isoformat(),
        'P-001',
        hours_map or {},
        worker_day_map or {},
    )
    return schedule, result


def test_friday_defaults_to_seven_hours_and_pushes_remainder_to_monday():
    schedule, result = _assign_on_friday()

    assert schedule['2026-05-08'][0]['hours'] == 7
    assert schedule['2026-05-11'][0]['hours'] == 3
    assert '2026-05-09' not in schedule
    assert '2026-05-10' not in schedule
    assert result[2].isoformat() == '2026-05-11'


def test_global_daily_hours_override_can_change_friday_capacity():
    schedule, result = _assign_on_friday(hours_map={'2026-05-08': 9})

    assert schedule['2026-05-08'][0]['hours'] == 9
    assert schedule['2026-05-11'][0]['hours'] == 1
    assert result[2].isoformat() == '2026-05-11'


def test_worker_day_override_takes_precedence_over_global_override():
    schedule, result = _assign_on_friday(
        hours_map={'2026-05-08': 9},
        worker_day_map={'Mikel': {'2026-05-08': 6}},
    )

    assert schedule['2026-05-08'][0]['hours'] == 6
    assert schedule['2026-05-11'][0]['hours'] == 4
    assert result[2].isoformat() == '2026-05-11'
