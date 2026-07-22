from pathlib import Path
import re


def test_index_applies_drag_drop_move_without_unconditional_reload():
    template = Path('templates/index.html').read_text(encoding='utf-8')

    assert 'function _applyMoveWithoutReload(data, targetWorker)' in template
    assert 'draggedTaskElement = t;' in template
    assert 'afterMove(data, moveData.date, moveData.worker);' in template

    after_move = re.search(r'function afterMove\(data, originalDate, targetWorker = \'\'\) \{(?P<body>.*?)\n  \}\n  const splitCancel', template, re.S)
    assert after_move, 'afterMove with targetWorker should be present before splitCancel'
    body = after_move.group('body')
    assert '_applyMoveWithoutReload(data, targetWorker)' in body
    assert 'location.reload();' in body
    assert body.index('_applyMoveWithoutReload(data, targetWorker)') < body.index('location.reload();')
