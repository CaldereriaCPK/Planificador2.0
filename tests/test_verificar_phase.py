from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_default_initial_phases_do_not_seed_verificar_or_lanzamiento():
    project = {'phases': {}, 'assigned': {}}

    changed = planner_app._ensure_default_initial_phases(project)

    assert changed is True
    assert project['phases'] == {}
    assert project['assigned'] == {}
    assert 'verificar' not in project['phases']
    assert 'verificar' not in project['assigned']


def test_manual_project_creation_does_not_add_verificar_by_default(monkeypatch):
    saved = {}

    monkeypatch.setattr(planner_app, 'get_projects', lambda: [])
    monkeypatch.setattr(planner_app, 'save_projects', lambda projects: saved.setdefault('projects', projects))

    project = planner_app._create_manual_project_from_request({
        'name': 'OF-1',
        'client': 'Cliente',
        'verificar': '4',
        'verificar_days': '1',
    })

    assert 'verificar' not in project['phases']
    assert 'verificar' not in project['assigned']
    assert 'lanzamiento' not in project['phases']
    assert 'lanzamiento' not in project['assigned']
    assert saved['projects'] == [project]


def test_remove_unplanned_verificar_button_is_not_rendered():
    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'remove-unplanned-verificar-btn' not in template
        assert 'Eliminar fases Verificar' not in template
        assert 'REMOVE_UNPLANNED_VERIFICAR_URL' not in template


def test_project_creation_forms_hide_verificar_phase_inputs():
    shared_form = Path('templates', '_project_form_fields.html').read_text(encoding='utf-8')
    assert "'verificar'" in shared_form
    for template_name in ('add_project.html', 'complete.html', 'index.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert "{% include '_project_form_fields.html' %}" in template
        combined = template + shared_form
        assert 'verificar (horas)' not in combined
        assert 'name="verificar"' not in combined


def test_create_phase_endpoint_can_add_lanzamiento_like_other_phases(monkeypatch):
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'client': 'Cliente',
            'start_date': '2026-06-29',
            'due_date': '',
            'phases': {},
            'assigned': {},
        }
    ]
    saved = {}
    manual_added = []
    monkeypatch.setattr(planner_app, 'get_projects', lambda: projects)
    monkeypatch.setattr(planner_app, 'save_projects', lambda value: saved.setdefault('projects', value))
    monkeypatch.setattr(planner_app, 'manual_bucket_add', lambda pid, phase, part: manual_added.append((pid, phase, part)))

    response = planner_app.app.test_client().post(
        '/add_phase_instance',
        json={'pid': 'p1', 'phase': 'lanzamiento', 'hours': '3'},
        auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS),
    )

    assert response.status_code == 201
    assert projects[0]['phases']['lanzamiento'] == 3
    assert projects[0]['assigned']['lanzamiento'] == planner_app.UNPLANNED
    assert manual_added == [('p1', 'lanzamiento', None)]
    assert saved['projects'] == projects
