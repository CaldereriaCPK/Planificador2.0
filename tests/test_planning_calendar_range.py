from pathlib import Path


def test_planning_calendar_only_keeps_one_week_back():
    source = Path('app.py').read_text(encoding='utf-8')

    assert 'PLANNING_CALENDAR_PAST_DAYS = 7' in source
    assert 'start = today - timedelta(days=PLANNING_CALENDAR_PAST_DAYS)' in source
    assert 'start = today - timedelta(days=30)' not in source
