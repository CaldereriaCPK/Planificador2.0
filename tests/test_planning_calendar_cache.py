from pathlib import Path
import copy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def _simple_project():
    return {
        'id': 'p-cache',
        'name': 'OF-CACHE',
        'client': 'Cliente',
        'start_date': '2026-06-01',
        'due_date': '',
        'phases': {'montar': 4},
        'assigned': {'montar': 'Mikel'},
        'segment_starts': {'montar': ['2026-06-10']},
        'segment_start_hours': {'montar': [0]},
        'frozen_tasks': [],
    }


def test_cached_initial_calendar_reuses_schedule_without_changes(monkeypatch):
    projects = [_simple_project()]
    calls = []

    def fake_build(projects_arg, include_optional_phases=True):
        calls.append((copy.deepcopy(projects_arg), include_optional_phases))
        return {'Mikel': {'2026-06-10': []}}, [], [], {}

    monkeypatch.setattr(planner_app, 'build_schedule_with_archived', fake_build)
    planner_app.invalidate_planning_calendar_cache()

    first = planner_app.cached_build_schedule_with_archived(projects, include_optional_phases=True)
    second = planner_app.cached_build_schedule_with_archived(projects, include_optional_phases=True)

    assert first == second
    assert len(calls) == 1


def test_move_invalidates_planning_calendar_cache(monkeypatch):
    projects = [_simple_project()]
    build_calls = []

    def fake_build(projects_arg, include_optional_phases=True):
        build_calls.append(copy.deepcopy(projects_arg))
        return {'Mikel': {'2026-06-10': []}}, [], [], {}

    def fake_compute_schedule_map(projects_arg):
        mapping = {}
        for project in projects_arg:
            entries = []
            for phase, hours in project.get('phases', {}).items():
                start = project.get('segment_starts', {}).get(phase, [None])[0]
                worker = project.get('assigned', {}).get(phase, planner_app.UNPLANNED)
                entries.append((worker, start, phase, hours, None))
            mapping[project['id']] = entries
        return mapping

    monkeypatch.setattr(planner_app, 'build_schedule_with_archived', fake_build)
    monkeypatch.setattr(planner_app, 'get_projects', lambda: copy.deepcopy(projects))
    monkeypatch.setattr(planner_app, 'save_projects', lambda saved: None)
    monkeypatch.setattr(planner_app, 'compute_schedule_map', fake_compute_schedule_map)
    monkeypatch.setattr(planner_app, '_is_worker_deactivated_on_day', lambda worker, day: False)
    monkeypatch.setattr(planner_app._schedule_mod, '_build_vacation_map', lambda: {})
    monkeypatch.setattr(planner_app, 'manual_bucket_remove', lambda pid, phase, part: None)
    monkeypatch.setattr(planner_app, 'manual_bucket_add', lambda pid, phase, part, position=None: None)
    monkeypatch.setattr(planner_app, 'build_move_reason', lambda projects, pid, phase, part, mode, info: '')
    monkeypatch.setattr(planner_app, 'load_tracker', lambda: [])
    monkeypatch.setattr(planner_app, 'save_tracker', lambda logs: None)
    monkeypatch.setattr(planner_app, 'load_phase_history', lambda: {})
    monkeypatch.setattr(planner_app, 'save_phase_history', lambda history: None)
    monkeypatch.setattr(planner_app, 'material_blockers_for_project', lambda projects, pid, planned_day: [])
    planner_app.invalidate_planning_calendar_cache()

    planner_app.cached_build_schedule_with_archived(projects, include_optional_phases=True)
    planner_app.cached_build_schedule_with_archived(projects, include_optional_phases=True)
    assert len(build_calls) == 1

    planner_app.app.config['TESTING'] = True
    with planner_app.app.test_client() as client:
        response = client.post(
            '/move',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            json={
                'pid': 'p-cache',
                'phase': 'montar',
                'date': '2026-06-11',
                'worker': 'Mikel',
                'mode': 'split',
            },
        )

    assert response.status_code == 200
    planner_app.cached_build_schedule_with_archived(projects, include_optional_phases=True)
    assert len(build_calls) == 2
