from pathlib import Path
import copy
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_move_publishes_map_and_increments_version_once(monkeypatch):
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'client': 'Cliente',
            'phases': {'montar': 2},
            'assigned': {'montar': 'Mikel'},
        }
    ]
    old_map = {'p1': [('Mikel', '2026-07-28', 'montar', 2, None)]}
    new_map = {'p1': [('Mikel', '2026-07-29', 'montar', 2, None)]}
    saved = []
    compute_calls = []

    monkeypatch.setattr(planner_app, 'get_projects', lambda: copy.deepcopy(projects))
    monkeypatch.setattr(
        planner_app,
        '_save_projects_to_storage',
        lambda value: saved.append(copy.deepcopy(value)),
    )
    def fake_move(value, *args, **kwargs):
        value[0].setdefault('segment_starts', {})['montar'] = ['2026-07-29']
        return '2026-07-29', None, {}

    def fake_compute(value):
        compute_calls.append(value)
        return copy.deepcopy(new_map)

    monkeypatch.setattr(planner_app, 'move_phase_date', fake_move)
    monkeypatch.setattr(planner_app, 'compute_schedule_map', fake_compute)
    monkeypatch.setattr(planner_app, 'build_move_reason', lambda *args: 'movimiento')
    monkeypatch.setattr(planner_app, 'load_tracker', lambda: [])
    monkeypatch.setattr(planner_app, 'save_tracker', lambda value: None)
    monkeypatch.setattr(planner_app, 'load_phase_history', lambda: {})
    monkeypatch.setattr(planner_app, 'save_phase_history', lambda value: None)
    monkeypatch.setattr(planner_app, 'material_blockers_for_project', lambda *args: [])

    with planner_app._PLANNING_CALENDAR_CACHE_LOCK:
        planner_app._PLANNING_CALENDAR_CACHE.update(version=41, map=copy.deepcopy(old_map))
        planner_app._PLANNING_CALENDAR_CACHE_PROJECTS = copy.deepcopy(projects)

    with planner_app.app.test_client() as client:
        response = client.post(
            '/move',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            json={
                'pid': 'p1',
                'phase': 'montar',
                'date': '2026-07-29',
                'worker': 'Mikel',
            },
        )

    assert response.status_code == 200
    assert len(saved) == 1
    assert planner_app._PLANNING_CALENDAR_CACHE['version'] == 42
    assert planner_app._PLANNING_CALENDAR_CACHE['map'] == new_map

    monkeypatch.setattr(
        planner_app,
        'compute_schedule_map',
        lambda value: (_ for _ in ()).throw(AssertionError('unexpected full rebuild')),
    )
    assert planner_app.get_planning_calendar_map(saved[0]) == new_map
    assert len(compute_calls) == 1


def test_save_projects_without_map_invalidates_once(monkeypatch):
    monkeypatch.setattr(planner_app, '_save_projects_to_storage', lambda value: None)
    with planner_app._PLANNING_CALENDAR_CACHE_LOCK:
        planner_app._PLANNING_CALENDAR_CACHE.update(version=7, map={'old': []})

    planner_app.save_projects([])

    assert planner_app._PLANNING_CALENDAR_CACHE == {'version': 8, 'map': None}
