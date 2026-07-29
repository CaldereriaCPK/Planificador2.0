from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_ready_to_archive_webhook_persists_column_before_get_projects(monkeypatch, tmp_path):
    cards_file = tmp_path / 'kanban_cards.json'
    cards_file.write_text(json.dumps([{
        'timestamp': '2026-07-28T09:00:00Z',
        'card': {
            'taskid': 'card-123',
            'lanename': 'Acero al Carbono',
            'columnname': 'En producción',
        },
        'last_column': 'En producción',
    }]), encoding='utf-8')
    projects = [{
        'id': 'project-123',
        'name': 'OF 123',
        'source': 'api',
        'kanban_id': 'card-123',
        'kanban_column': 'En producción',
        'phases': {},
        'assigned': {},
    }]

    monkeypatch.setattr(planner_app, 'KANBAN_CARDS_FILE', str(cards_file))
    monkeypatch.setattr(planner_app, 'load_projects', lambda: projects)
    monkeypatch.setattr(planner_app, 'save_projects', lambda saved: None)
    monkeypatch.setattr(planner_app, '_fetch_kanban_card', lambda *args, **kwargs: None)
    planner_app._KANBAN_CARD_FETCH_CACHE.clear()

    response = planner_app.app.test_client().post('/kanbanize-webhook', json={
        'timestamp': '2026-07-29T10:30:00Z',
        'card': {
            'taskid': 'card-123',
            'lanename': 'Acero al Carbono',
            'column': 'Ready to Archive',
        },
    })

    assert response.status_code == 200
    snapshot = json.loads(cards_file.read_text(encoding='utf-8'))[-1]
    assert snapshot['last_column'] == 'Ready to Archive'
    assert snapshot['card']['columnname'] == 'Ready to Archive'
    assert snapshot['card']['columnName'] == 'Ready to Archive'
    assert snapshot['card']['column'] == 'Ready to Archive'

    refreshed_project = planner_app.get_projects()[0]
    assert refreshed_project['kanban_column'] == 'Ready to Archive'


def test_ready_to_archive_project_tasks_are_marked_gray(monkeypatch):
    task = {
        'pid': 'ready-pid',
        'project': 'OF READY',
        'client': 'Cliente',
        'phase': 'montar',
        'hours': 1,
        'color': '#ff0000',
    }

    monkeypatch.setattr(
        planner_app,
        'inject_archived_tasks',
        lambda base_schedule: ([], {}),
    )
    monkeypatch.setattr(
        planner_app,
        'schedule_projects',
        lambda projects, base_schedule=None: ({'Mikel': {'2026-05-11': [task.copy()]}}, []),
    )

    schedule, _conflicts, _archived_entries, _archived_project_map = planner_app.build_schedule_with_archived(
        [{'id': 'ready-pid', 'kanban_column': 'Ready to Archive'}]
    )

    rendered_task = schedule['Mikel']['2026-05-11'][0]
    assert rendered_task['archived_shadow'] is True
    assert rendered_task['frozen'] is True
    assert rendered_task['color'] == planner_app.READY_TO_ARCHIVE_TASK_BACKGROUND
    assert rendered_task['frozen_background'] == planner_app.READY_TO_ARCHIVE_TASK_BACKGROUND


def test_archived_shadow_uses_configured_background_variable():
    css = Path('static/style.css').read_text(encoding='utf-8')

    assert 'background: var(--frozen-background, #d9d9d9);' in css
