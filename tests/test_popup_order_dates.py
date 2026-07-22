from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as planner_app


def test_build_project_order_popup_map_groups_order_dates_by_project(monkeypatch):
    links = [
        {
            'pid': 'pid-1',
            'links': ['Pedido sin detalle'],
            'link_details': [
                {'title': 'Pedido 100', 'order_date': '2026-05-11'},
                {'title': 'Pedido 101', 'order_date_raw': '12/05/2026'},
            ],
        },
        {
            'pid': 'pid-2',
            'links': ['Pedido 200'],
            'link_details': [{}],
        },
    ]

    monkeypatch.setattr(planner_app, 'load_compras_raw', lambda: ({}, {}))
    monkeypatch.setattr(planner_app, 'build_project_links', lambda compras_raw: [])
    monkeypatch.setattr(planner_app, 'attach_phase_starts', lambda raw_links, projects: links)
    monkeypatch.setattr(planner_app, 'annotate_order_details', lambda entries, today=None: entries)

    result = planner_app.build_project_order_popup_map([], today=date(2026, 5, 11))

    assert result['pid-1'] == [
        {'title': 'Pedido 100', 'order_date': '11/05/2026'},
        {'title': 'Pedido 101', 'order_date': '12/05/2026'},
    ]
    assert result['pid-2'] == [
        {'title': 'Pedido 200', 'order_date': 'Sin fecha pedido'},
    ]


def test_popup_templates_render_project_orders_section():
    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'const PROJECT_ORDERS = {{ project_orders|tojson }};' in template
        assert 'function renderProjectOrdersSection(pid)' in template
        assert 'html += renderProjectOrdersSection(t.dataset.pid);' in template
        assert 'popup-order-date' in template


def test_popup_kanban_extra_fields_use_requested_order():
    expected_order = "['lanzamiento', 'material', 'caldereria', 'tratamiento', 'mecanizado', 'pintado']"
    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert expected_order in template
        assert 'const normalizeKanbanFieldLabel = (label) => (label || \'\')' in template
        assert '.sort(([labelA, _valueA, indexA], [labelB, _valueB, indexB]) => {' in template
        assert "'horas preparacion'" in template


def test_complete_popup_filters_project_table_to_selected_phase_project():
    template = Path('templates', 'complete.html').read_text(encoding='utf-8')
    css = Path('static', 'style.css').read_text(encoding='utf-8')

    assert "const projectRows = document.querySelectorAll('.projects-table tbody tr[data-pid]');" in template
    assert 'function showOnlyProjectTableRow(pid)' in template
    assert 'showOnlyProjectTableRow(t.dataset.pid);' in template
    assert 'function resetProjectTableRows()' in template
    assert 'resetProjectTableRows();' in template
    assert '.projects-table tr.proj-hidden-by-task { display: none; }' in css


def test_pedidos_calendar_highlight_hides_non_matching_project_rows_and_is_resizable_both_axes():
    template = Path('templates', 'calendar_pedidos.html').read_text(encoding='utf-8')
    css = Path('static', 'style.css').read_text(encoding='utf-8')

    assert "row.classList.remove('hidden-by-highlight');" in template
    assert "row.classList.add('hidden-by-highlight');" in template
    assert '.pedidos-wrapper.highlight-active .columna-1 .project-row.hidden-by-highlight {' in css
    assert 'display: none;' in css
    assert '.pedidos-calendar-container {' in css
    assert 'resize: both;' in css


def test_pedidos_calendar_size_persists_across_tab_refreshes():
    template = Path('templates', 'calendar_pedidos.html').read_text(encoding='utf-8')

    assert "const CALENDAR_SIZE_STORAGE_KEY_PREFIX = 'pedidosCalendarSize:';" in template
    assert 'function resolveCalendarSizeStorageKey(wrapper)' in template
    assert 'function loadStoredCalendarSize(wrapper)' in template
    assert 'function applyStoredCalendarSize(wrapper)' in template
    assert 'function persistCalendarSize(wrapper)' in template
    assert "window.addEventListener('beforeunload', () => {" in template
    assert "event.key && event.key.startsWith(CALENDAR_SIZE_STORAGE_KEY_PREFIX)" in template
    assert 'wrapper.style.width = `${stored.width}px`;' in template
    assert 'wrapper.style.height = `${stored.height}px`;' in template
    assert "wrapperStyleObserver.observe(wrapper, { attributes: true, attributeFilter: ['style'] });" in template
    assert 'calendarWrappers.forEach((wrapper) => {' in template
    assert 'function ensureDefaultCalendarWidth(wrapper)' in template



def test_pedidos_column_one_shows_client_and_project_order_dates_after_due_date():
    template = Path('templates', 'calendar_pedidos.html').read_text(encoding='utf-8')
    css = Path('static', 'style.css').read_text(encoding='utf-8')
    app_source = Path('app.py').read_text(encoding='utf-8')

    due_header = '<th scope="col" data-col-key="due" draggable="true">Entrega (Fecha tope)</th>'
    client_header = '<th scope="col" data-col-key="client-delivery" draggable="true">Fecha entrega cliente</th>'
    project_order_header = '<th scope="col" data-col-key="project-order-date" draggable="true">Fecha pedido proyecto</th>'
    start_header = '<th scope="col" data-col-key="start" draggable="true">Inicio planificado</th>'
    assert template.index(due_header) < template.index(client_header) < template.index(project_order_header) < template.index(start_header)
    assert 'data-col-key="client-delivery"' in template
    assert 'data-col-key="project-order-date"' in template
    assert 'class="client-delivery-cell"' in template
    assert 'class="project-order-date-cell"' in template
    assert 'item.client_delivery_date|format_due_date' in template
    assert 'item.project_order_date|format_due_date' in template
    assert 'const clientDeliveryValue = item.client_delivery_date' in template
    assert 'const projectOrderValue = item.project_order_date' in template
    assert '.columna-1-table .client-delivery-cell' in css
    assert '.columna-1-table .project-order-date-cell' in css
    assert "client_delivery_raw = _get_custom_field_text(card, 'Fecha Cliente', 'Fecha cliente')" in app_source
    assert "project_order_date_obj, project_order_raw = _resolve_order_custom_field(card)" in app_source
    assert "entry['project_order_date'] = info['project_order_date']" in app_source


def test_pedidos_column_one_column_order_is_draggable_and_persisted():
    template = Path('templates', 'calendar_pedidos.html').read_text(encoding='utf-8')
    css = Path('static', 'style.css').read_text(encoding='utf-8')

    assert "const COLUMN_ORDER_KEY = 'columna1ColumnOrder';" in template
    assert 'function initColumnReordering()' in template
    assert 'function applyColumnOrder(table, order = null)' in template
    assert 'localStorage.setItem(COLUMN_ORDER_KEY, JSON.stringify(order));' in template
    assert 'applyColumnOrder(columnaTable);' in template
    assert '.columna-1-table th[draggable="true"]' in css
    assert '.columna-1-table th.column-drop-target' in css

def test_pedidos_calendar_mode_toggle_filters_calendar_and_column_one():
    template = Path('templates', 'calendar_pedidos.html').read_text(encoding='utf-8')
    css = Path('static', 'style.css').read_text(encoding='utf-8')
    app_source = Path('app.py').read_text(encoding='utf-8')

    assert 'data-calendar-mode="pedidos"' in template
    assert 'data-calendar-mode="subcontrataciones"' in template
    assert 'data-calendar-section="{{ calendar.key }}"' in template
    assert "{% if calendar.key != 'pedidos' %} hidden{% endif %}" in template
    assert 'data-calendars="{{ (item.calendar_keys or [])|join(\',\') }}"' in template
    assert 'data-calendars="{{ (detail.calendar_keys or [])|join(\',\') }}"' in template
    assert 'let activeCalendarMode = \'pedidos\';' in template
    assert 'function applyCalendarModeVisibility()' in template
    assert "row.style.display = rowMatchesMode && rowMatchesFilter ? '' : 'none';" in template
    assert '.pedidos-mode-toggle' in css
    assert '.pedidos-mode-btn.active' in css
    assert "calendar_refs = {" in app_source
    assert "details[idx]['calendar_keys'] = detail_calendar_keys" in app_source
    assert "item['calendar_keys'] = sorted(item_calendar_keys)" in app_source
