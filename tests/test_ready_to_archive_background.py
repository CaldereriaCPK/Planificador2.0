from pathlib import Path


def test_archived_shadow_uses_configured_background_variable():
    css = Path('static/style.css').read_text(encoding='utf-8')

    assert 'background: var(--frozen-background, #d9d9d9);' in css
