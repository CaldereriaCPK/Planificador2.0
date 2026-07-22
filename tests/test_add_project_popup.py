import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_phase_popup_can_open_shared_add_project_form_modal():
    add_page = Path('templates/add_project.html').read_text(encoding='utf-8')
    shared_form = Path('templates/_project_form_fields.html').read_text(encoding='utf-8')

    assert "{% include '_project_form_fields.html' %}" in add_page
    assert 'Nombre del proyecto' in shared_form
    assert 'Fecha límite' in shared_form

    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'id="add-project-inline-modal"' in template
        assert "{% include '_project_form_fields.html' %}" in template
        assert 'add-project-popup-btn popup-action-btn">Añadir proyecto</button>' not in template
        assert 'class="create-phase-btn popup-action-btn"' in template
        assert 'Crear fase</button>' in template
        assert 'add-phase-instance-btn popup-action-btn' in template
        assert 'Añadir fase de ${basePhaseName}</button>' in template
        assert 'class="create-phase-menu"' in template
        assert 'PHASES.map(phase =>' in template
        assert 'ADD_PROJECT_INLINE_URL' in template
        assert 'optional-phase-toggle planner-tool-btn" hidden' in template
        assert '.conflict-content:not(.add-project-inline-content)' in template


def test_add_project_modal_open_class_is_styled():
    css = Path('static/style.css').read_text(encoding='utf-8')

    assert '#add-project-inline-modal.open' in css
    assert 'align-items: center;' in css
    assert 'justify-content: center;' in css
    assert '.add-project-inline-content' in css
    assert 'cursor: default;' in css
    assert '.create-phase-menu' in css
    assert '.create-phase-actions' in css


def test_browser_title_includes_current_planner_tab():
    base = Path('templates/base.html').read_text(encoding='utf-8')

    assert "'complete': 'Completo'" in base
    assert "'calendar_pedidos': 'Calendario pedidos'" in base
    assert "'resources': 'Recursos'" in base
    assert '<title>Planificador{% if planner_tab_title %}: {{ planner_tab_title }}{% endif %}</title>' in base


def test_complete_generated_projects_heading_replaces_conflicts_label():
    complete = Path('templates/complete.html').read_text(encoding='utf-8')

    assert '<h3>Proyectos generados</h3>' in complete
    assert '<h3>Conflictos</h3>' not in complete


def test_phase_hours_updates_preserve_calendar_position_instead_of_highlighting_phase():
    index = Path('templates/index.html').read_text(encoding='utf-8')
    complete = Path('templates/complete.html').read_text(encoding='utf-8')

    for template in (index, complete):
        assert 'function reloadKeepingCalendarPosition()' in template
        assert "sessionStorage.removeItem('highlightPid');" in template
        assert 'function storeCalendarScrollPosition()' in template
        assert "wrapper.addEventListener('scroll', storeCalendarScrollPosition, { passive: true });" in template

    assert "url.searchParams.set('highlight', btn.dataset.pid);" not in index
    assert 'const phaseHoursChanged = Object.keys(data.phases).length > 0 || data.phase_parts.length > 0;' in complete
    assert "if (phaseHoursChanged) {\n              reloadKeepingCalendarPosition();" in complete


def test_split_base_phases_are_not_hidden_from_phase_popup():
    index = Path('templates/index.html').read_text(encoding='utf-8')
    complete = Path('templates/complete.html').read_text(encoding='utf-8')

    for template in (index, complete):
        assert 'splitBasePhases' not in template
        assert 'if (splitBasePhases.has(ph)) return;' not in template

    assert 'if (isActivePhase && t.dataset.hours)' in index


def test_complete_projects_table_has_columns_for_split_phase_parts():
    complete = Path('templates/complete.html').read_text(encoding='utf-8')
    app_source = Path('app.py').read_text(encoding='utf-8')

    assert 'complete_project_phase_columns = build_complete_project_phase_columns(filtered_projects)' in app_source
    assert "'label': phase if idx == 0 else f'{phase} ({idx + 1})'" in app_source
    assert 'extras_by_base = {}' in app_source
    assert 'seen_columns = set()' in app_source
    assert "{% for col in complete_project_phase_columns %}" in complete
    assert 'name="part" value="{{ part }}"' in complete
    assert 'data-project-phase-column="1"' in complete
    assert 'class="project-phase-cell"' in complete
    assert 'phase_parts:[]' in complete
    assert "data.phase_parts.push({phase: fd.get('phase'), part, hours: fd.get('hours')});" in complete


def test_complete_project_table_filters_zero_hour_phase_columns_when_task_selected():
    complete = Path('templates/complete.html').read_text(encoding='utf-8')

    assert 'function setProjectTablePhaseColumnVisibility(row)' in complete
    assert 'const visiblePhaseColumns = new Set();' in complete
    assert 'total > 0' in complete
    assert "cell.matches('[data-project-phase-column], .project-phase-cell')" in complete
    assert 'setProjectTablePhaseColumnVisibility(selectedRow);' in complete
    assert 'function resetProjectTablePhaseColumns()' in complete
    assert 'resetProjectTablePhaseColumns();' in complete


def test_complete_project_phase_columns_keep_parts_next_to_base_phase():
    import app

    labels = [
        col['label']
        for col in app.build_complete_project_phase_columns([
            {'phases': {'montar': 3, 'montar#2': 4, 'soldar': [2, 5], 'soldar#2': 9}},
        ])
    ]

    assert labels.index('montar (2)') == labels.index('montar') + 1
    assert labels.index('soldar (2)') == labels.index('soldar') + 1
    assert labels.count('soldar (2)') == 1


def test_phase_click_shows_direction_arrows_for_offscreen_phase_hours():
    css = Path('static/style.css').read_text(encoding='utf-8')

    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'function renderOffscreenPhaseIndicators(selectedTask)' in template
        assert "document.querySelectorAll('.schedule .task[data-pid][data-phase]')" in template
        assert 'renderOffscreenPhaseIndicators(t);' in template
        assert "Esta fase tiene horas fuera de la vista hacia la derecha" in template
        assert "Esta fase tiene horas fuera de la vista hacia la izquierda" in template
        assert 'function scheduleOffscreenPhaseIndicators()' in template
        assert "requestAnimationFrame(() => {" in template
        assert "wrapper.addEventListener('scroll', scheduleOffscreenPhaseIndicators, { passive: true });" in template

    assert '.phase-offscreen-arrow {' in css
    assert '.phase-offscreen-arrow--left' in css
    assert '.phase-offscreen-arrow--right' in css


def test_complete_project_table_uses_content_sized_columns():
    css = Path('static/style.css').read_text(encoding='utf-8')
    template = Path('templates/complete.html').read_text(encoding='utf-8')

    assert '.complete-projects table { width: max-content; }' in css
    assert '.complete-projects .projects-table {\n    table-layout: auto;' in css
    assert '.complete-projects .projects-table .project-delete-column' in css
    assert 'data-has-project-filter="{{ 1 if project_filter or client_filter else 0 }}"' in template
    assert 'function shouldApplyStoredWidths()' in template
    assert "table.dataset.hasProjectFilter === '1'" in template


def test_complete_project_delete_column_is_first():
    template = Path('templates/complete.html').read_text(encoding='utf-8')

    table_start = template.index('<table class="projects-table"')
    header_start = template.index('<tr>', table_start)
    header_end = template.index('</tr>', header_start)
    header = template[header_start:header_end]
    assert header.index('<th class="project-delete-column">X</th>') < header.index('<th>Nombre</th>')

    row_start = template.index('<tr data-pid="{{ p.id }}"')
    row_end = template.index('<td data-sort="{{ p.name|default', row_start)
    first_cell = template[row_start:row_end]
    assert 'class="project-delete-column"' in first_cell
    assert 'class="delete-form"' in first_cell


def test_base_template_removes_global_loading_overlay():
    base = Path('templates/base.html').read_text(encoding='utf-8')
    css = Path('static/style.css').read_text(encoding='utf-8')

    assert 'id="page-loading-overlay"' not in base
    assert 'PAGE_LOADING_DELAY_MS' not in base
    assert 'showPageLoadingOverlay' not in base
    assert 'hidePageLoadingOverlay' not in base
    assert "body_classes.append('page-loading')" not in base
    assert '.page-loading-overlay' not in css
    assert '.page-loading-spinner' not in css
    assert 'page-loading-spin' not in css
    assert 'body.page-loading > :not(#page-loading-overlay)' not in css
    assert 'function storeScroll()' in base
    assert "window.addEventListener('scroll', storeScroll, { passive: true });" in base
