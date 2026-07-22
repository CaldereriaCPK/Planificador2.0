from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_material_status_ignores_tratamiento_columns(monkeypatch):
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'phases': {'montar': 2},
            'assigned': {'montar': planner_app.UNPLANNED},
        }
    ]

    raw_links = [
        {
            'pid': 'p1',
            'links': ['Pedido TAU'],
            'link_details': [
                {'title': 'Pedido TAU', 'column': 'Tratamiento'},
                {'title': 'Pedido TAU Final', 'column': 'Tratamiento final'},
            ],
        }
    ]

    monkeypatch.setattr(planner_app, 'load_compras_raw', lambda: ({}, {}))
    monkeypatch.setattr(planner_app, 'build_project_links', lambda compras_raw: raw_links)
    monkeypatch.setattr(planner_app, 'attach_phase_starts', lambda links, projects: links)

    status_map, missing_titles = planner_app.compute_material_status_map(
        projects, include_missing_titles=True
    )

    assert status_map['p1'] == 'complete'
    assert missing_titles.get('p1', []) == []



def test_tratamiento_columns_are_allowed_for_verify_status(monkeypatch):
    projects = [
        {
            'id': 'p1',
            'name': 'OF-1',
            'phases': {'montar': 2},
            'assigned': {'montar': planner_app.UNPLANNED},
        }
    ]

    raw_links = [
        {
            'pid': 'p1',
            'links': ['Pedido Verificación', 'Pedido TAU', 'Pedido TAU Final'],
            'link_details': [
                {'title': 'Pedido Verificación', 'column': 'Pdte. Verificación'},
                {'title': 'Pedido TAU', 'column': 'Tratamiento'},
                {'title': 'Pedido TAU Final', 'column': 'Tratamiento final'},
            ],
        }
    ]

    monkeypatch.setattr(planner_app, 'load_compras_raw', lambda: ({}, {}))
    monkeypatch.setattr(planner_app, 'build_project_links', lambda compras_raw: raw_links)
    monkeypatch.setattr(planner_app, 'attach_phase_starts', lambda links, projects: links)

    status_map, missing_titles = planner_app.compute_material_status_map(
        projects, include_missing_titles=True
    )

    assert status_map['p1'] == 'verify'
    assert missing_titles.get('p1', []) == []
