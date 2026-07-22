from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_hiding_resource_moves_only_future_phases_to_manual_bucket(monkeypatch):
    today = date(2026, 6, 8)
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'client': 'Cliente',
            'start_date': '2026-06-01',
            'due_date': '2026-06-30',
            'phases': {'montar': 4, 'soldar': 4},
            'assigned': {'montar': 'Mikel', 'soldar': 'Mikel'},
            'frozen_tasks': [
                {'worker': 'Mikel', 'day': (today - timedelta(days=1)).isoformat(), 'phase': 'montar'},
                {'worker': 'Mikel', 'day': today.isoformat(), 'phase': 'soldar'},
            ],
        }
    ]
    mapping = {
        'p1': [
            ('Mikel', (today - timedelta(days=1)).isoformat(), 'montar', 4, None),
            ('Mikel', today.isoformat(), 'soldar', 4, None),
        ]
    }
    added = []

    monkeypatch.setattr(planner_app, 'compute_schedule_map', lambda projects_arg: mapping)
    monkeypatch.setattr(
        planner_app,
        'manual_bucket_add',
        lambda pid, phase, part, position=None: added.append((pid, phase, part)),
    )

    changed = planner_app._move_worker_future_phases_to_manual_bucket(
        projects, {'Mikel'}, today=today
    )

    assert changed is True
    assert projects[0]['assigned']['montar'] == 'Mikel'
    assert projects[0]['assigned']['soldar'] == planner_app.UNPLANNED
    assert projects[0]['frozen_tasks'] == [
        {'worker': 'Mikel', 'day': (today - timedelta(days=1)).isoformat(), 'phase': 'montar'}
    ]
    assert added == [('p1', 'soldar', None)]


def test_resource_template_renders_delete_button_and_visible_checkbox():
    template = Path('templates/resources.html').read_text(encoding='utf-8')

    assert '<th>Eliminar</th>' in template
    assert 'name="delete_worker"' in template
    assert '¿Eliminar el recurso {{ w }}?' in template
    assert 'name="worker"' in template
    assert 'name="visibility_effective__{{ loop.index0 }}"' in template
    assert 'id="resource-visibility-date"' in template
    assert 'type="date"' in template
    assert 'showPicker' in template


def test_resources_route_uses_selected_visibility_effective_date(monkeypatch):
    calls = []

    monkeypatch.setattr(planner_app, 'WORKERS', {'Mikel': [], 'Iban': [], planner_app.UNPLANNED: []})
    monkeypatch.setattr(planner_app, 'load_inactive_workers', lambda: [])
    monkeypatch.setattr(planner_app, 'load_worker_hours', lambda: {})
    monkeypatch.setattr(planner_app, 'get_projects', lambda: [])
    monkeypatch.setattr(planner_app, 'save_projects', lambda projects: None)
    saved_dates = []
    monkeypatch.setattr(planner_app, 'save_inactive_workers', lambda workers: None)
    monkeypatch.setattr(planner_app, 'load_inactive_worker_dates', lambda: {})
    monkeypatch.setattr(planner_app, 'save_inactive_worker_dates', lambda dates: saved_dates.append(dates.copy()))

    def fake_move(projects, workers, *, today=None):
        calls.append((set(workers), today))
        return False

    monkeypatch.setattr(planner_app, '_move_worker_future_phases_to_manual_bucket', fake_move)

    with planner_app.app.test_client() as client:
        response = client.post(
            '/resources',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            data={
                'worker': ['Iban'],
                'worker_original__0': 'Mikel',
                'worker_name__0': 'Mikel',
                'visibility_effective__0': '2026-06-15',
                'worker_original__1': 'Iban',
                'worker_name__1': 'Iban',
                'visibility_effective__1': '2026-06-08',
            },
        )

    assert response.status_code == 302
    assert calls == [({'Mikel'}, date(2026, 6, 15))]
    assert saved_dates[-1] == {'Mikel': '2026-06-15'}


def test_deactivate_worker_day_route_uses_clicked_cell_date(monkeypatch):
    calls = []
    saved_inactive = []
    saved_dates = []

    monkeypatch.setattr(planner_app, 'WORKERS', {'Mikel': [], planner_app.UNPLANNED: []})
    monkeypatch.setattr(planner_app, 'load_inactive_workers', lambda: [])
    monkeypatch.setattr(planner_app, 'save_inactive_workers', lambda workers: saved_inactive.append(workers))
    monkeypatch.setattr(planner_app, 'load_inactive_worker_dates', lambda: {})
    monkeypatch.setattr(planner_app, 'save_inactive_worker_dates', lambda dates: saved_dates.append(dates.copy()))
    monkeypatch.setattr(planner_app, 'get_projects', lambda: [])
    monkeypatch.setattr(planner_app, 'save_projects', lambda projects: None)

    def fake_move(projects, workers, *, today=None):
        calls.append((set(workers), today))
        return False

    monkeypatch.setattr(planner_app, '_move_worker_future_phases_to_manual_bucket', fake_move)

    with planner_app.app.test_client() as client:
        response = client.post(
            '/deactivate_worker_day',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            json={'worker': 'Mikel', 'date': '2026-06-10'},
        )

    assert response.status_code == 204
    assert saved_inactive == [['Mikel']]
    assert saved_dates == [{'Mikel': '2026-06-10'}]
    assert calls == [({'Mikel'}, date(2026, 6, 10))]


def test_planning_templates_render_deactivate_button_and_badges():
    css = Path('static/style.css').read_text(encoding='utf-8')
    assert '.task.deactivated' in css
    assert 'deactivated-day' in css
    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'id="day-menu-deactivate"' in template
        assert 'DEACTIVATE_WORKER_DAY_URL' in template
        assert 'REMOVE_DEACTIVATED_WORKER_URL' in template
        assert 'DESACTIVADO' in template
        assert 'class="deact-delete"' in template
        assert "document.querySelectorAll('.deact-delete')" in template
        assert 'd.isoformat() >= deactivated_from' in template
        assert "cell.classList.contains('deactivated-day')" in template
        assert 'No es posible planificar una fase porque ese día ese recurso está desactivado.' in template


def test_move_phase_rejects_deactivated_target_day(monkeypatch):
    monkeypatch.setattr(planner_app, 'load_inactive_workers', lambda: ['Mikel'])
    monkeypatch.setattr(
        planner_app,
        'load_inactive_worker_dates',
        lambda: {'Mikel': '2026-06-10'},
    )

    def fail_move(*args, **kwargs):
        raise AssertionError('move_phase_date should not be called for deactivated cells')

    monkeypatch.setattr(planner_app, 'move_phase_date', fail_move)

    with planner_app.app.test_client() as client:
        response = client.post(
            '/move',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            json={'pid': 'p1', 'phase': 'montar', 'date': '2026-06-10', 'worker': 'Mikel'},
        )

    assert response.status_code == 400
    assert response.get_json()['error'] == (
        'No es posible planificar una fase porque ese día ese recurso está desactivado.'
    )



def test_remove_deactivated_worker_route_clears_inactive_state(monkeypatch):
    saved_inactive = []
    saved_dates = []

    monkeypatch.setattr(planner_app, 'load_inactive_workers', lambda: ['Mikel', 'Iban'])
    monkeypatch.setattr(planner_app, 'save_inactive_workers', lambda workers: saved_inactive.append(workers))
    monkeypatch.setattr(
        planner_app,
        'load_inactive_worker_dates',
        lambda: {'Mikel': '2026-06-10', 'Iban': '2026-06-12'},
    )
    monkeypatch.setattr(planner_app, 'save_inactive_worker_dates', lambda dates: saved_dates.append(dates.copy()))

    with planner_app.app.test_client() as client:
        response = client.post(
            '/remove_deactivated_worker',
            auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
            data={'worker': 'Mikel'},
        )

    assert response.status_code == 204
    assert saved_inactive == [['Iban']]
    assert saved_dates == [{'Iban': '2026-06-12'}]


def test_inactive_resource_assignments_remain_on_original_worker(monkeypatch):
    import schedule as schedule_mod

    project = {
        'id': 'p1',
        'name': 'OF-1',
        'client': 'Cliente',
        'start_date': '2026-06-08',
        'due_date': '2026-06-30',
        'phases': {'montar': 2},
        'assigned': {'montar': 'Mikel'},
        'planned': True,
    }

    monkeypatch.setattr(schedule_mod, 'load_inactive_workers', lambda: ['Mikel'])
    monkeypatch.setattr(schedule_mod, 'load_daily_hours', lambda: {})
    monkeypatch.setattr(schedule_mod, 'load_worker_day_hours', lambda: {})
    monkeypatch.setattr(schedule_mod, '_build_vacation_map', lambda: {})

    scheduled, _conflicts = schedule_mod.schedule_projects([project])

    assert 'Mikel' in scheduled
    assert scheduled['Mikel']['2026-06-08'][0]['phase'] == 'montar'
    assert project['assigned']['montar'] == 'Mikel'
    assert schedule_mod.UNPLANNED not in scheduled or not scheduled[schedule_mod.UNPLANNED]


def test_planner_calendar_hides_deactivated_resource_when_effective_date_arrives(monkeypatch):
    monkeypatch.setattr(
        planner_app,
        'WORKERS',
        {'Mikel': [], 'Iban': [], 'Ane': [], planner_app.UNPLANNED: []},
    )
    monkeypatch.setattr(planner_app, 'load_inactive_workers', lambda: ['Mikel', 'Iban'])
    monkeypatch.setattr(
        planner_app,
        'load_inactive_worker_dates',
        lambda: {'Mikel': '2026-06-09', 'Iban': '2026-06-11'},
    )

    assert planner_app.planner_calendar_workers(date(2026, 6, 7)) == ['Mikel', 'Iban', 'Ane']
    assert planner_app.planner_calendar_workers(date(2026, 6, 9)) == ['Iban', 'Ane']
    assert planner_app.planner_calendar_workers(date(2026, 6, 11)) == ['Ane']


def test_deactivated_rows_keep_only_tasks_before_effective_day():
    schedule = {
        'Mikel': {
            '2026-06-09': [{'phase': 'montar'}],
            '2026-06-10': [{'phase': 'soldar'}],
            '2026-06-11': [{'phase': 'pintar'}],
        },
        'Iban': {'2026-06-11': [{'phase': 'montar'}]},
    }

    rows = planner_app._schedule_with_deactivated_rows(
        schedule,
        ['Mikel', 'Iban'],
        {'Mikel': '2026-06-10'},
    )

    assert rows['Mikel'] == {
        '2026-06-09': [{'phase': 'montar'}],
    }
    assert rows['Iban'] == {'2026-06-11': [{'phase': 'montar'}]}


def test_phase_popup_lists_hidden_resource_assignments_as_planned():
    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'Object.entries(assignedMap).forEach(([phaseKey, assigned])' in template
        assert "assigned !== 'Sin planificar'" in template
        assert "classes.push(assigned === 'Sin planificar' ? 'unplanned' : 'planned')" in template
