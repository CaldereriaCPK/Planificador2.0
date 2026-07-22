from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def _point_calendar_cache_files(monkeypatch, tmp_path):
    file_names = {
        'PROJECTS_FILE': 'projects.json',
        'VACATIONS_FILE': 'vacations.json',
        'DAILY_HOURS_FILE': 'daily_hours.json',
        'WORKER_HOURS_FILE': 'worker_hours.json',
        'WORKER_DAY_HOURS_FILE': 'worker_day_hours.json',
        'MANUAL_UNPLANNED_FILE': 'manual_unplanned.json',
        'PHASE_HISTORY_FILE': 'phase_history.json',
        'INACTIVE_WORKERS_FILE': 'inactive_workers.json',
        'INACTIVE_WORKER_DATES_FILE': 'inactive_worker_dates.json',
        'EXTRA_WORKERS_FILE': 'extra_workers.json',
        'WORKER_ORDER_FILE': 'worker_order.json',
        'WORKER_RENAMES_FILE': 'worker_renames.json',
        'DELETED_WORKERS_FILE': 'deleted_workers.json',
    }
    for attr, name in file_names.items():
        path = tmp_path / name
        path.write_text('[]', encoding='utf-8')
        monkeypatch.setattr(planner_app._schedule_mod, attr, str(path), raising=False)

    for attr, name in (
        ('ARCHIVED_CALENDAR_FILE', 'archived_calendar.json'),
        ('PLANNER_SETTINGS_FILE', 'planner_settings.json'),
        ('KANBAN_CARDS_FILE', 'kanban_cards.json'),
    ):
        path = tmp_path / name
        path.write_text('[]', encoding='utf-8')
        monkeypatch.setattr(planner_app, attr, str(path))

    return tmp_path / 'projects.json'


def test_cached_calendar_schedule_reuses_data_until_project_file_changes(monkeypatch, tmp_path):
    projects_file = _point_calendar_cache_files(monkeypatch, tmp_path)
    planner_app.clear_calendar_schedule_cache()
    calls = {'get_projects': 0}

    def fake_get_projects():
        calls['get_projects'] += 1
        return [
            {
                'id': 'p1',
                'name': f"Project {calls['get_projects']}",
                'client': 'Client',
                'phases': {'montar': 1},
                'assigned': {'montar': 'Mikel'},
            }
        ]

    monkeypatch.setattr(planner_app, 'get_projects', fake_get_projects)
    monkeypatch.setattr(planner_app, 'archive_ready_to_archive_projects_if_due', lambda projects, today=None: False)
    monkeypatch.setattr(
        planner_app,
        'build_schedule_with_archived',
        lambda projects, include_optional_phases=True: (
            {'Mikel': {'2026-07-22': [{'pid': projects[0]['id'], 'project': projects[0]['name']}] }},
            [],
            [],
            {},
        ),
    )
    monkeypatch.setattr(planner_app, 'annotate_schedule_frozen_background', lambda schedule: None)

    first = planner_app.get_cached_calendar_schedule(True)
    second = planner_app.get_cached_calendar_schedule(True)

    assert calls['get_projects'] == 1
    assert first == second

    # Mutating returned objects must not pollute the cached value used by later requests.
    first[1]['Mikel']['2026-07-22'][0]['project'] = 'polluted'
    third = planner_app.get_cached_calendar_schedule(True)
    assert third[1]['Mikel']['2026-07-22'][0]['project'] == 'Project 1'

    time.sleep(0.001)
    projects_file.write_text('[{"id":"p1","phases":{"montar":2}}]', encoding='utf-8')

    invalidated = planner_app.get_cached_calendar_schedule(True)

    assert calls['get_projects'] == 2
    assert invalidated[0][0]['name'] == 'Project 2'


def test_cached_calendar_schedule_key_includes_optional_phase_mode(monkeypatch, tmp_path):
    _point_calendar_cache_files(monkeypatch, tmp_path)
    planner_app.clear_calendar_schedule_cache()
    calls = {'get_projects': 0}

    monkeypatch.setattr(
        planner_app,
        'get_projects',
        lambda: calls.__setitem__('get_projects', calls['get_projects'] + 1) or [
            {'id': 'p1', 'name': 'Project', 'phases': {'montar': 1}, 'assigned': {'montar': 'Mikel'}}
        ],
    )
    monkeypatch.setattr(planner_app, 'archive_ready_to_archive_projects_if_due', lambda projects, today=None: False)
    monkeypatch.setattr(planner_app, 'build_schedule_with_archived', lambda projects, include_optional_phases=True: ({}, [], [], {}))
    monkeypatch.setattr(planner_app, 'annotate_schedule_frozen_background', lambda schedule: None)

    planner_app.get_cached_calendar_schedule(True)
    planner_app.get_cached_calendar_schedule(False)

    assert calls['get_projects'] == 2
