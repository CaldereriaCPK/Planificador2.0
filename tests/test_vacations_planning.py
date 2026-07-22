import copy
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as planner_app
import schedule as schedule_mod


def test_vacations_are_visible_before_existing_frozen_tasks(monkeypatch):
    vacation_day = date(2026, 6, 29)
    monkeypatch.setattr(
        schedule_mod,
        'load_vacations',
        lambda: [
            {
                'id': 'vacation-1',
                'worker': 'Mikel',
                'start': vacation_day.isoformat(),
                'end': vacation_day.isoformat(),
            }
        ],
    )
    monkeypatch.setattr(schedule_mod, 'load_daily_hours', lambda: {})
    monkeypatch.setattr(schedule_mod, 'load_worker_day_hours', lambda: {})

    project = {
        'id': 'project-1',
        'name': 'Proyecto congelado',
        'client': 'Cliente',
        'start_date': vacation_day.isoformat(),
        'due_date': '',
        'color': '#cccccc',
        'phases': {'montar': 8},
        'assigned': {'montar': 'Mikel'},
        'frozen_tasks': [
            {
                'worker': 'Mikel',
                'day': vacation_day.isoformat(),
                'project': 'Proyecto congelado',
                'client': 'Cliente',
                'phase': 'montar',
                'hours': 8,
                'start': 0,
                'late': False,
                'color': '#cccccc',
                'due_date': '',
                'start_date': vacation_day.isoformat(),
                'pid': 'project-1',
            }
        ],
    }

    schedule, _ = schedule_mod.schedule_projects([copy.deepcopy(project)])
    day_tasks = schedule['Mikel'][vacation_day.isoformat()]

    assert day_tasks[0]['phase'] == 'vacaciones'
    assert any(task.get('phase') == 'montar' for task in day_tasks)


def test_vacation_form_rejects_reversed_dates(monkeypatch):
    saved = []
    monkeypatch.setattr(planner_app, 'load_vacations', lambda: [])
    monkeypatch.setattr(planner_app, 'save_vacations', lambda vacations: saved.extend(vacations))

    response = planner_app.app.test_client().post(
        '/vacations',
        data={
            'workers': ['Mikel'],
            'start': '2026-07-10',
            'end': '2026-07-09',
        },
        auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
    )

    assert response.status_code == 200
    assert 'La fecha de fin no puede ser anterior' in response.get_data(as_text=True)
    assert saved == []
