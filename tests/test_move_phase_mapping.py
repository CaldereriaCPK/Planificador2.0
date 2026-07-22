from pathlib import Path
import copy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_move_phase_split_reuses_initial_mapping(monkeypatch):
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'client': 'Cliente',
            'start_date': '2026-06-01',
            'due_date': '',
            'phases': {'montar': 4},
            'assigned': {'montar': 'Mikel'},
            'segment_starts': {'montar': ['2026-06-10']},
            'segment_start_hours': {'montar': [0]},
            'frozen_tasks': [],
        }
    ]
    calls = []

    def fake_compute_schedule_map(projects_arg):
        calls.append(copy.deepcopy(projects_arg))
        mapping = {}
        for project in projects_arg:
            entries = []
            for phase, hours in project.get('phases', {}).items():
                start = project.get('segment_starts', {}).get(phase, [None])[0]
                if not start:
                    start = project.get('start_date')
                worker = project.get('assigned', {}).get(phase, planner_app.UNPLANNED)
                entries.append((worker, start, phase, hours, None))
            mapping[project['id']] = entries
        return mapping

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

    planner_app.app.config['TESTING'] = True
    with planner_app.app.test_client() as client:
        response = client.post(
            '/move',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            json={
                'pid': 'p1',
                'phase': 'montar',
                'date': '2026-06-11',
                'worker': 'Mikel',
                'mode': 'split',
            },
        )

    assert response.status_code == 200
    assert response.get_json()['date'] == '2026-06-11'
    assert len(calls) == 1
