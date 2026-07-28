from datetime import date

import app as planner_app


def test_consecutive_split_moves_reuse_and_patch_calendar_indexes(monkeypatch):
    projects = [{
        'id': 'p1',
        'name': 'Proyecto',
        'client': 'Cliente',
        'phases': {'montaje': 4},
        'assigned': {'montaje': 'Alice'},
        'frozen_tasks': [],
    }]
    calls = []

    def schedule_map(_projects):
        calls.append(True)
        return {'p1': [('Alice', '2026-07-28', 'montaje', 4, None)]}

    monkeypatch.setattr(planner_app, 'compute_schedule_map', schedule_map)
    monkeypatch.setattr(planner_app, '_raw_save_projects', lambda _projects: None)
    monkeypatch.setattr(planner_app._schedule_mod, '_build_vacation_map', lambda: {})
    planner_app.invalidate_planning_calendar_cache()

    first, warning, _ = planner_app.move_phase_date(
        projects, 'p1', 'montaje', date(2026, 7, 29), 'Alice', mode='split'
    )
    second, warning2, _ = planner_app.move_phase_date(
        projects, 'p1', 'montaje', date(2026, 7, 30), 'Alice', mode='split'
    )

    assert (first, warning) == ('2026-07-29', None)
    assert (second, warning2) == ('2026-07-30', None)
    assert len(calls) == 1
    entries = planner_app._phase_calendar_entries(projects, 'p1', 'montaje', None)
    assert [(entry['day'], entry['worker'], entry['start_hour'], entry['hours'])
            for entry in entries] == [('2026-07-30', 'Alice', 0, 4)]


def test_planning_input_save_invalidates_calendar_indexes(monkeypatch):
    planner_app._PLANNING_CALENDAR_INDEX_VERSION = 10
    planner_app._PLANNING_CALENDAR_CACHE_VERSION = 10
    monkeypatch.setattr(planner_app, '_PLANNING_PHASE_INDEX', {('p1', 'fase', None): []})
    monkeypatch.setattr(planner_app, '_PLANNING_CELL_INDEX', {('Alice', '2026-07-28'): []})
    monkeypatch.setattr(planner_app._schedule_mod, 'save_vacations', lambda _value: None)

    # Exercise the common invalidation contract directly; the save wrappers
    # use this same function for vacations, hours and resource availability.
    planner_app.invalidate_planning_calendar_cache()

    assert planner_app._PLANNING_CALENDAR_INDEX_VERSION == -1
    assert planner_app._PLANNING_PHASE_INDEX == {}
    assert planner_app._PLANNING_CELL_INDEX == {}
