from pathlib import Path


def test_move_is_applied_before_fetch_and_failure_restores_snapshot():
    template = Path('templates/index.html').read_text(encoding='utf-8')
    do_move = template.index('function doMove(moveData, mode, beforeMaterial)')
    optimistic = template.index('const optimisticSnapshot = beginOptimisticMove(moveData);', do_move)
    fetch = template.index('fetch(MOVE_URL', optimistic)

    assert optimistic < fetch
    assert 'task.classList.add(\'saving\')' in template
    assert "task.setAttribute('draggable', 'false')" in template
    assert template.count('restoreOptimisticMove(optimisticSnapshot);') >= 2
    assert '.catch(err =>' in template[fetch:]


def test_move_response_exposes_reconciliation_metadata():
    source = Path('app.py').read_text(encoding='utf-8')

    assert "'actual_day': actual_day" in source
    assert "'actual_worker': actual_worker" in source
    assert "'affected': affected_moves" in source
