from pathlib import Path

import app as planner_app


def test_backup_pdf_returns_clear_json_error_and_button_restores(monkeypatch):
    def fail_build_summary_pdf():
        raise RuntimeError('wkhtmltopdf roto para prueba')

    monkeypatch.setattr(planner_app, '_build_summary_pdf', fail_build_summary_pdf)
    monkeypatch.setattr(planner_app, 'ensure_daily_backup_thread', lambda: None)

    planner_app.app.config['TESTING'] = True
    with planner_app.app.test_client() as client:
        response = client.post('/backup-pdf', auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS))

    assert response.status_code == 500
    payload = response.get_json()
    assert payload is not None
    assert payload['error'] == 'wkhtmltopdf roto para prueba'

    for template_name in ('index.html', 'complete.html'):
        template = Path('templates', template_name).read_text(encoding='utf-8')
        assert 'PDF + Backup</button>' in template
        assert "data-backup-url=\"{{ url_for('backup_pdf') }}\"" in template
        assert "alert(err.message || 'No se pudo generar el PDF + backup.');" in template
        assert '.finally(() => {' in template
        assert 'button.disabled = false;' in template
        assert 'button.textContent = originalText;' in template


def test_backup_pdf_returns_pdf_download_and_creates_server_backup(monkeypatch, tmp_path):
    backup_path = tmp_path / 'planner_backup_test.zip'

    monkeypatch.setattr(planner_app, '_build_summary_pdf', lambda: b'%PDF fake')
    monkeypatch.setattr(planner_app, 'create_full_backup', lambda: str(backup_path))
    monkeypatch.setattr(planner_app, 'ensure_daily_backup_thread', lambda: None)

    planner_app.app.config['TESTING'] = True
    with planner_app.app.test_client() as client:
        response = client.post('/backup-pdf', auth=(planner_app.AUTH_USER, planner_app.AUTH_PASS))

    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.data == b'%PDF fake'
    assert response.headers['X-Backup-Filename'] == 'planner_backup_test.zip'
    assert response.headers['Content-Disposition'].startswith('attachment; filename="resumen_')
