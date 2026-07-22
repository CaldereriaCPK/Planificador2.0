from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


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
