import schedule


def test_mojibake_worker_name_resolves_to_configured_worker():
    assert "Oficina técnica." in schedule.WORKERS
    assert schedule._canonical_worker_name("Oficina tÃ©cnica.") == "Oficina técnica."
