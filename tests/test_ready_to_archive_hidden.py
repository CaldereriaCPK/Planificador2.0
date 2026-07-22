from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_ready_to_archive_projects_are_hidden_from_visible_projects():
    projects = [
        {
            'id': 'ready-pid',
            'kanban_column': 'Ready to Archive',
            'phases': {'montar': 4},
        },
        {
            'id': 'active-pid',
            'kanban_column': 'En curso',
            'phases': {'montar': 4},
        },
    ]

    visible = planner_app.filter_visible_projects(projects)

    assert [project['id'] for project in visible] == ['active-pid']


def test_ready_to_archive_projects_are_not_scheduled(monkeypatch):
    scheduled_projects = []

    def fake_schedule_projects(projects, base_schedule=None):
        scheduled_projects.extend(projects)
        return {}, []

    monkeypatch.setattr(planner_app, 'inject_archived_tasks', lambda base_schedule: ([], {}))
    monkeypatch.setattr(planner_app, 'schedule_projects', fake_schedule_projects)

    planner_app.build_schedule_with_archived(
        [
            {'id': 'ready-pid', 'kanban_column': 'Ready to Archive', 'phases': {'montar': 4}},
            {'id': 'active-pid', 'kanban_column': 'En curso', 'phases': {'montar': 4}},
        ]
    )

    assert [project['id'] for project in scheduled_projects] == ['active-pid']
