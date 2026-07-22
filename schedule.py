from datetime import date, timedelta, datetime, time
import json
import os
import copy
import unicodedata

from localtime import local_today

DATA_DIR = os.environ.get('EFIMERO_DATA_DIR', 'data')
PROJECTS_FILE = os.path.join(DATA_DIR, 'projects.json')
DISMISSED_FILE = os.path.join(DATA_DIR, 'dismissed_conflicts.json')
EXTRA_CONFLICTS_FILE = os.path.join(DATA_DIR, 'conflicts.json')
NOTES_FILE = os.path.join(DATA_DIR, 'notes.json')
WORKER_NOTES_FILE = os.path.join(DATA_DIR, 'worker_notes.json')
VACATIONS_FILE = os.path.join(DATA_DIR, 'vacations.json')
DAILY_HOURS_FILE = os.path.join(DATA_DIR, 'daily_hours.json')
INACTIVE_WORKERS_FILE = os.path.join(DATA_DIR, 'inactive_workers.json')
INACTIVE_WORKER_DATES_FILE = os.path.join(DATA_DIR, 'inactive_worker_dates.json')
EXTRA_WORKERS_FILE = os.path.join(DATA_DIR, 'extra_workers.json')
WORKER_ORDER_FILE = os.path.join(DATA_DIR, 'worker_order.json')
WORKER_RENAMES_FILE = os.path.join(DATA_DIR, 'worker_renames.json')
DELETED_WORKERS_FILE = os.path.join(DATA_DIR, 'deleted_workers.json')
WORKER_HOURS_FILE = os.path.join(DATA_DIR, 'worker_hours.json')
WORKER_DAY_HOURS_FILE = os.path.join(DATA_DIR, 'worker_day_hours.json')
MANUAL_UNPLANNED_FILE = os.path.join(DATA_DIR, 'manual_unplanned.json')
PHASE_HISTORY_FILE = os.path.join(DATA_DIR, 'phase_history.json')

PHASE_ORDER = [
    'dibujo',
    'pedidos',
    'lanzamiento',
    'verificar',
    'preparar material',
    'montar',
    'soldar',
    'montar 2º',
    'soldar 2º',
    'trabajos en exterior',
    'mecanizar',
    'tratamiento',
    'pintar',
]

PHASE_DEADLINE_FIELDS = {
    'lanzamiento': 'LANZAMIENTO',
    'montar': 'CALDERERIA',
    'soldar': 'CALDERERIA',
    'montar 2º': 'CALDERERIA',
    'soldar 2º': 'CALDERERIA',
    'trabajos en exterior': 'CALDERERIA',
    'mecanizar': 'MECANIZADO',
    'tratamiento': 'TRATAMIENTO',
    'pintar': 'PINTADO',
}


def phase_base(phase_name):
    if not isinstance(phase_name, str):
        return phase_name
    return phase_name.split('#', 1)[0]


def iter_phase_keys(phases):
    keys = list((phases or {}).keys())

    def sort_key(ph):
        base = phase_base(ph)
        try:
            base_idx = PHASE_ORDER.index(base)
        except ValueError:
            base_idx = len(PHASE_ORDER)
        suffix = ph[len(base) :] if isinstance(ph, str) else ''
        return (base_idx, suffix)

    return sorted(keys, key=sort_key)

UNPLANNED = 'Sin planificar'

BASE_WORKERS = {
    'Pilar': ['dibujo'],
    'Joseba 1': ['dibujo'],
    'Irene': ['pedidos'],
    'Mikel': ['lanzamiento', 'verificar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Iban': ['lanzamiento', 'verificar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Joseba 2': ['lanzamiento', 'verificar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Naparra': ['lanzamiento', 'verificar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Unai': ['lanzamiento', 'verificar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Fabio': ['lanzamiento', 'verificar', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Beltxa': ['lanzamiento', 'verificar', 'soldar', 'soldar 2º', 'montar', 'montar 2º', 'trabajos en exterior'],
    'Igor': ['lanzamiento', 'verificar', 'soldar', 'soldar 2º', 'trabajos en exterior'],
    'Albi': ['lanzamiento', 'verificar', 'preparar material', 'soldar', 'soldar 2º', 'montar', 'montar 2º', 'trabajos en exterior'],
    'Eneko': ['lanzamiento', 'verificar', 'pintar', 'montar', 'montar 2º', 'soldar', 'soldar 2º', 'trabajos en exterior'],
}

TAIL_WORKERS = {
    'Mecanizar': ['mecanizar'],
    'Tratamiento': ['tratamiento'],
    UNPLANNED: PHASE_ORDER,
}


def load_extra_workers():
    if os.path.exists(EXTRA_WORKERS_FILE):
        with open(EXTRA_WORKERS_FILE, 'r') as f:
            return json.load(f)
    return []


def save_extra_workers(workers):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EXTRA_WORKERS_FILE, 'w') as f:
        json.dump(workers, f)


def load_worker_order():
    if os.path.exists(WORKER_ORDER_FILE):
        with open(WORKER_ORDER_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return []
        if isinstance(data, list):
            return [w for w in data if isinstance(w, str)]
    return []


def save_worker_order(order):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WORKER_ORDER_FILE, 'w') as f:
        json.dump(order, f)


def load_worker_renames():
    if os.path.exists(WORKER_RENAMES_FILE):
        with open(WORKER_RENAMES_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return {}
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                key = key.strip()
                value = value.strip()
                if not key or not value or key == value:
                    continue
                cleaned[key] = value
            return cleaned
    return {}


def save_worker_renames(renames):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WORKER_RENAMES_FILE, 'w') as f:
        json.dump(renames or {}, f)


def load_deleted_workers():
    if os.path.exists(DELETED_WORKERS_FILE):
        with open(DELETED_WORKERS_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return []
        if isinstance(data, list):
            return [w for w in data if isinstance(w, str) and w.strip()]
    return []


def save_deleted_workers(workers):
    cleaned = []
    seen = set()
    for worker in workers or []:
        if not isinstance(worker, str):
            continue
        name = worker.strip()
        if not name or name == UNPLANNED or name in seen:
            continue
        cleaned.append(name)
        seen.add(name)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DELETED_WORKERS_FILE, 'w') as f:
        json.dump(cleaned, f)
    return cleaned


def _normalize_display_key(value):
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if not text:
        return None
    decomposed = unicodedata.normalize('NFD', text)
    return ''.join(ch for ch in decomposed if unicodedata.category(ch) != 'Mn')


def _parse_phase_deadline(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    if not text or text == '0':
        return None
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(text).date()
    except (ValueError, TypeError):
        pass
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%d-%m-%Y', '%Y.%m.%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None



def _apply_worker_renames(workers, renames):
    if not renames:
        return workers
    cleaned = {}
    used_targets = set()
    for original, target in renames.items():
        if original not in workers:
            continue
        target = target.strip()
        if not target or target in used_targets:
            continue
        cleaned[original] = target
        used_targets.add(target)
    if not cleaned:
        return workers
    renamed = {}
    for name, phases in workers.items():
        target = cleaned.get(name)
        if target:
            renamed[target] = phases
        elif name not in cleaned:
            renamed[name] = phases
    return renamed


def _build_workers(extra=None, order=None, renames=None):
    workers = BASE_WORKERS.copy()
    if extra is None:
        extra = load_extra_workers()
    for w in extra:
        workers[w] = BASE_WORKERS['Eneko'][:]
    workers.update(TAIL_WORKERS)
    if renames is None:
        renames = load_worker_renames()
    workers = _apply_worker_renames(workers, renames)
    deleted = set(load_deleted_workers())
    if deleted:
        workers = {
            name: phases
            for name, phases in workers.items()
            if name not in deleted or name == UNPLANNED
        }
    if order is None:
        order = load_worker_order()
    if order:
        ordered = {}
        for name in order:
            if name in workers and name not in ordered:
                ordered[name] = workers[name]
        for name, phases in workers.items():
            if name not in ordered:
                ordered[name] = phases
        return ordered
    return workers


WORKERS = _build_workers()

# Igor deja de aparecer en el calendario a partir del 21 de julio
IGOR_END = date(2025, 7, 21)

HOURS_PER_DAY = 8
DEFAULT_FRIDAY_HOURS = 7
WORKDAY_START = 8
HOURS_LIMITS = {w: HOURS_PER_DAY for w in WORKERS}
HOURS_LIMITS['Irene'] = float('inf')
HOURS_LIMITS['Pilar'] = float('inf')
HOURS_LIMITS['Joseba 1'] = float('inf')
HOURS_LIMITS['Mecanizar'] = float('inf')
HOURS_LIMITS['Tratamiento'] = float('inf')
HOURS_LIMITS[UNPLANNED] = float('inf')
WEEKEND = {5, 6}  # Saturday=5, Sunday=6 in weekday()

DEFAULT_HOURS_LIMITS = {worker: limit for worker, limit in HOURS_LIMITS.items()}


def default_daily_hours(day):
    """Return default daily hours for *day* when no override exists."""
    if isinstance(day, date) and day.weekday() == 4:
        return DEFAULT_FRIDAY_HOURS
    return HOURS_PER_DAY


def resolve_daily_hours(day, hours_map):
    """Return daily hours for *day* using overrides when present."""
    if hours_map is None:
        hours_map = {}
    override = hours_map.get(day.isoformat())
    if override is None:
        return default_daily_hours(day)
    return override


def worker_daily_capacity(worker, day, hours_map=None, worker_day_map=None):
    """Return the effective daily capacity for ``worker`` on ``day``.

    Priority order:
    1. global day override from ``daily_hours``;
    2. Friday default (7h) or worker resource limit on other weekdays;
    3. worker/day override from ``worker_day_hours``.
    """
    if hours_map is None:
        hours_map = {}
    if worker_day_map is None:
        worker_day_map = {}

    day_str = day.isoformat()
    resource_limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
    if day_str in hours_map:
        limit = hours_map[day_str]
    elif day.weekday() == 4:
        limit = default_daily_hours(day)
    else:
        limit = resource_limit

    worker_overrides = worker_day_map.get(worker) or {}
    override_limit = worker_overrides.get(day_str)
    if override_limit is not None:
        limit = override_limit
    return limit


def _sanitize_worker_hours(data):
    """Return a mapping of worker -> hours limited to 1..12."""

    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for worker, value in data.items():
        if worker not in WORKERS:
            continue
        try:
            hours = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= hours <= 12:
            cleaned[worker] = hours
    return cleaned


def _apply_worker_hours(overrides):
    """Update ``HOURS_LIMITS`` with the provided overrides."""

    for worker, default in DEFAULT_HOURS_LIMITS.items():
        HOURS_LIMITS[worker] = default
    for worker, hours in overrides.items():
        if worker in HOURS_LIMITS:
            HOURS_LIMITS[worker] = hours


def load_worker_hours():
    """Return the persisted worker hour overrides."""

    if os.path.exists(WORKER_HOURS_FILE):
        with open(WORKER_HOURS_FILE, 'r') as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError:
                return {}
        return _sanitize_worker_hours(raw)
    return {}


def save_worker_hours(data):
    """Persist worker hour overrides and refresh the limits map."""

    overrides = _sanitize_worker_hours(data or {})
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WORKER_HOURS_FILE, 'w') as f:
        json.dump(overrides, f)
    _apply_worker_hours(overrides)


_apply_worker_hours(load_worker_hours())


def _sanitize_worker_day_hours(data):
    """Return mapping of worker -> {date -> hours} limited to 1..12."""

    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for worker, entries in data.items():
        if worker not in WORKERS:
            continue
        if not isinstance(entries, dict):
            continue
        worker_map = {}
        for day, value in entries.items():
            if not isinstance(day, str):
                continue
            try:
                date.fromisoformat(day)
            except (TypeError, ValueError):
                continue
            try:
                hours = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= hours <= 12:
                worker_map[day] = hours
        if worker_map:
            cleaned[worker] = worker_map
    return cleaned


def load_worker_day_hours():
    """Return persisted worker/day hour overrides."""

    if os.path.exists(WORKER_DAY_HOURS_FILE):
        with open(WORKER_DAY_HOURS_FILE, 'r') as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError:
                return {}
        return _sanitize_worker_day_hours(raw)
    return {}


def save_worker_day_hours(data):
    """Persist worker/day overrides after sanitizing them."""

    overrides = _sanitize_worker_day_hours(data or {})
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WORKER_DAY_HOURS_FILE, 'w') as f:
        json.dump(overrides, f)
    return overrides


def add_worker(name):
    """Add a new worker that behaves like Eneko."""
    name = name.strip()
    if not name or name in WORKERS:
        return
    inactive = load_inactive_workers()
    if name in inactive:
        save_inactive_workers([w for w in inactive if w != name])
    inactive_dates = load_inactive_worker_dates()
    if name in inactive_dates:
        inactive_dates.pop(name, None)
        save_inactive_worker_dates(inactive_dates)
    deleted = load_deleted_workers()
    if name in deleted:
        save_deleted_workers([w for w in deleted if w != name])
        if name in BASE_WORKERS or name in TAIL_WORKERS:
            WORKERS.clear()
            WORKERS.update(_build_workers())
            HOURS_LIMITS.setdefault(name, HOURS_PER_DAY)
            DEFAULT_HOURS_LIMITS.setdefault(name, HOURS_PER_DAY)
            _apply_worker_hours(load_worker_hours())
            return
    extras = load_extra_workers()
    if name not in extras:
        extras.append(name)
        save_extra_workers(extras)
    if os.path.exists(WORKER_ORDER_FILE):
        order = load_worker_order()
        if name not in order:
            order = order[:]
            order.append(name)
            save_worker_order(order)
    new_workers = _build_workers(extras)
    WORKERS.clear()
    WORKERS.update(new_workers)
    HOURS_LIMITS[name] = HOURS_PER_DAY
    DEFAULT_HOURS_LIMITS[name] = HOURS_PER_DAY
    _apply_worker_hours(load_worker_hours())


def delete_worker(name):
    """Delete a worker from the visible resource list and cleanup worker data."""

    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or name == UNPLANNED or name not in WORKERS:
        return False

    extras = load_extra_workers()
    if name in extras:
        save_extra_workers([w for w in extras if w != name])
    else:
        deleted = load_deleted_workers()
        if name not in deleted:
            deleted.append(name)
            save_deleted_workers(deleted)

    order = load_worker_order()
    if order:
        save_worker_order([w for w in order if w != name])

    inactive = load_inactive_workers()
    if inactive:
        save_inactive_workers([w for w in inactive if w != name])

    inactive_dates = load_inactive_worker_dates()
    if name in inactive_dates:
        inactive_dates.pop(name, None)
        save_inactive_worker_dates(inactive_dates)

    overrides = load_worker_hours()
    if name in overrides:
        overrides.pop(name, None)
        save_worker_hours(overrides)

    day_overrides = load_worker_day_hours()
    if name in day_overrides:
        day_overrides.pop(name, None)
        save_worker_day_hours(day_overrides)

    notes = load_worker_notes()
    if name in notes:
        notes.pop(name, None)
        save_worker_notes(notes)

    vacations = load_vacations()
    new_vacations = [vac for vac in vacations if vac.get('worker') != name]
    if new_vacations != vacations:
        save_vacations(new_vacations)

    WORKERS.clear()
    WORKERS.update(_build_workers())
    HOURS_LIMITS.pop(name, None)
    DEFAULT_HOURS_LIMITS.pop(name, None)
    _apply_worker_hours(load_worker_hours())
    return True


def rename_worker(old_name, new_name):
    """Rename an existing worker and update persisted data."""

    if not isinstance(old_name, str) or not isinstance(new_name, str):
        return False
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name or old_name == new_name:
        return False
    if old_name not in WORKERS or new_name in WORKERS or new_name == UNPLANNED:
        return False

    extras = load_extra_workers()
    renames = load_worker_renames()
    renames_changed = False

    if old_name in extras:
        new_extras = [new_name if w == old_name else w for w in extras]
        if new_extras != extras:
            save_extra_workers(new_extras)
        if renames.pop(old_name, None) is not None:
            renames_changed = True
    else:
        current = renames.get(old_name)
        if current != new_name:
            renames = renames.copy()
            renames[old_name] = new_name
            renames_changed = True
        # Ensure no conflicting aliases remain
        for key, value in list(renames.items()):
            if key != old_name and value == new_name:
                renames.pop(key)
                renames_changed = True

    if renames_changed:
        save_worker_renames(renames)

    order = load_worker_order()
    if order:
        updated_order = [new_name if w == old_name else w for w in order]
        if updated_order != order:
            save_worker_order(updated_order)

    inactive = load_inactive_workers()
    if inactive:
        updated_inactive = [new_name if w == old_name else w for w in inactive]
        if updated_inactive != inactive:
            save_inactive_workers(updated_inactive)

    inactive_dates = load_inactive_worker_dates()
    if old_name in inactive_dates:
        inactive_dates[new_name] = inactive_dates.pop(old_name)
        save_inactive_worker_dates(inactive_dates)

    overrides = load_worker_hours()
    if old_name in overrides and new_name not in overrides:
        overrides[new_name] = overrides.pop(old_name)
        save_worker_hours(overrides)
    elif old_name in overrides:
        overrides.pop(old_name, None)
        save_worker_hours(overrides)

    notes = load_worker_notes()
    if old_name in notes and new_name not in notes:
        notes[new_name] = notes.pop(old_name)
        save_worker_notes(notes)
    elif old_name in notes:
        notes.pop(old_name, None)
        save_worker_notes(notes)

    vacations = load_vacations()
    vac_changed = False
    for vac in vacations:
        if vac.get('worker') == old_name:
            vac['worker'] = new_name
            vac_changed = True
    if vac_changed:
        save_vacations(vacations)

    manual_entries = load_manual_unplanned()
    manual_changed = False
    for entry in manual_entries:
        if entry.get('worker') == old_name:
            entry['worker'] = new_name
            manual_changed = True
    if manual_changed:
        save_manual_unplanned(manual_entries)

    projects = load_projects()
    projects_changed = False
    for project in projects:
        assigned = project.get('assigned', {})
        for phase, worker in list(assigned.items()):
            if worker == old_name:
                assigned[phase] = new_name
                projects_changed = True
        seg_workers = project.get('segment_workers') or {}
        if isinstance(seg_workers, dict):
            for phase, worker_list in list(seg_workers.items()):
                if not isinstance(worker_list, list):
                    continue
                replaced = False
                new_list = []
                for worker in worker_list:
                    if worker == old_name:
                        worker = new_name
                        replaced = True
                    new_list.append(worker)
                if replaced:
                    seg_workers[phase] = new_list
                    projects_changed = True
        for task in project.get('frozen_tasks', []):
            if task.get('worker') == old_name:
                task['worker'] = new_name
                projects_changed = True
    if projects_changed:
        save_projects(projects)

    day_overrides = load_worker_day_hours()
    day_changed = False
    if old_name in day_overrides:
        existing = day_overrides.pop(old_name)
        if existing:
            if new_name in day_overrides:
                merged = day_overrides[new_name]
                merged.update(existing)
            else:
                day_overrides[new_name] = existing
            day_changed = True
    if day_changed:
        save_worker_day_hours(day_overrides)

    new_workers = _build_workers()
    WORKERS.clear()
    WORKERS.update(new_workers)

    if old_name in HOURS_LIMITS:
        HOURS_LIMITS[new_name] = HOURS_LIMITS.pop(old_name)
    else:
        HOURS_LIMITS.setdefault(new_name, HOURS_PER_DAY)
    if old_name in DEFAULT_HOURS_LIMITS:
        DEFAULT_HOURS_LIMITS[new_name] = DEFAULT_HOURS_LIMITS.pop(old_name)
    else:
        DEFAULT_HOURS_LIMITS.setdefault(new_name, HOURS_PER_DAY)
    _apply_worker_hours(load_worker_hours())

    return True


def set_worker_order(order):
    visible_workers = [w for w in WORKERS if w != UNPLANNED]
    cleaned = []
    seen = set()
    for name in order:
        if name in visible_workers and name not in seen:
            cleaned.append(name)
            seen.add(name)
    for name in visible_workers:
        if name not in seen:
            cleaned.append(name)
    save_worker_order(cleaned)
    new_workers = _build_workers()
    WORKERS.clear()
    WORKERS.update(new_workers)


def load_projects():
    if os.path.exists(PROJECTS_FILE):
        with open(PROJECTS_FILE, 'r') as f:
            data = json.load(f)
        filtered = [p for p in data if p.get('phases')]
        if len(filtered) != len(data):
            save_projects(filtered)
        return filtered
    return []


def save_projects(projects):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROJECTS_FILE, 'w') as f:
        json.dump(projects, f)


def load_dismissed():
    if os.path.exists(DISMISSED_FILE):
        with open(DISMISSED_FILE, 'r') as f:
            return json.load(f)
    return []


def save_dismissed(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DISMISSED_FILE, 'w') as f:
        json.dump(data, f)


def load_extra_conflicts():
    if os.path.exists(EXTRA_CONFLICTS_FILE):
        with open(EXTRA_CONFLICTS_FILE, 'r') as f:
            return json.load(f)
    return []


def save_extra_conflicts(conflicts):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EXTRA_CONFLICTS_FILE, 'w') as f:
        json.dump(conflicts, f)


def load_notes():
    if os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, 'r') as f:
            return json.load(f)
    return []


def save_notes(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NOTES_FILE, 'w') as f:
        json.dump(data, f)


def load_worker_notes():
    if os.path.exists(WORKER_NOTES_FILE):
        with open(WORKER_NOTES_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_worker_notes(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WORKER_NOTES_FILE, 'w') as f:
        json.dump(data, f)


def load_vacations():
    if os.path.exists(VACATIONS_FILE):
        with open(VACATIONS_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = []
    return data


def save_vacations(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(VACATIONS_FILE, 'w') as f:
        json.dump(data, f)


def load_inactive_workers():
    if os.path.exists(INACTIVE_WORKERS_FILE):
        with open(INACTIVE_WORKERS_FILE, 'r') as f:
            return json.load(f)
    return []


def save_inactive_workers(workers):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INACTIVE_WORKERS_FILE, 'w') as f:
        json.dump(workers, f)


def load_inactive_worker_dates():
    if os.path.exists(INACTIVE_WORKER_DATES_FILE):
        with open(INACTIVE_WORKER_DATES_FILE, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if k and v}
    return {}


def save_inactive_worker_dates(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    cleaned = {str(k): str(v) for k, v in (data or {}).items() if k and v}
    with open(INACTIVE_WORKER_DATES_FILE, 'w') as f:
        json.dump(cleaned, f)


def load_daily_hours():
    """Return mapping of day -> hours (1-9)."""
    if os.path.exists(DAILY_HOURS_FILE):
        with open(DAILY_HOURS_FILE, 'r') as f:
            return {k: int(v) for k, v in json.load(f).items()}
    return {}


def save_daily_hours(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DAILY_HOURS_FILE, 'w') as f:
        json.dump(data, f)


def _sanitize_manual_entries(entries):
    cleaned = []
    seen = set()
    if not isinstance(entries, list):
        return cleaned
    for item in entries:
        if not isinstance(item, dict):
            continue
        pid = item.get('pid')
        phase = item.get('phase')
        part = item.get('part')
        if pid in (None, '') or phase in (None, ''):
            continue
        pid_str = str(pid)
        part_val = None
        if part not in (None, '', 'None'):
            try:
                part_val = int(part)
            except Exception:
                continue
        key = (pid_str, str(phase), part_val)
        if key in seen:
            continue
        seen.add(key)
        entry = {'pid': pid_str, 'phase': str(phase)}
        if part_val is not None:
            entry['part'] = part_val
        cleaned.append(entry)
    return cleaned


def load_manual_unplanned():
    if os.path.exists(MANUAL_UNPLANNED_FILE):
        with open(MANUAL_UNPLANNED_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []
        return _sanitize_manual_entries(data)
    return []


def save_manual_unplanned(entries):
    cleaned = _sanitize_manual_entries(entries)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MANUAL_UNPLANNED_FILE, 'w') as f:
        json.dump(cleaned, f)
    return cleaned


def phase_history_key(pid, phase, part=None):
    part_value = ''
    if part not in (None, '', 'None'):
        part_value = str(part)
    return f"{str(pid)}|{phase}|{part_value}"


def load_phase_history():
    if not os.path.exists(PHASE_HISTORY_FILE):
        return {}
    try:
        with open(PHASE_HISTORY_FILE, 'r') as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, entries in data.items():
        if not isinstance(key, str) or not isinstance(entries, list):
            continue
        filtered = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            ts = item.get('timestamp')
            if not isinstance(ts, str):
                continue
            record = {
                'timestamp': ts,
                'from_day': item.get('from_day') if isinstance(item.get('from_day'), str) or item.get('from_day') is None else None,
                'to_day': item.get('to_day') if isinstance(item.get('to_day'), str) or item.get('to_day') is None else None,
                'from_worker': item.get('from_worker') if isinstance(item.get('from_worker'), str) or item.get('from_worker') is None else None,
                'to_worker': item.get('to_worker') if isinstance(item.get('to_worker'), str) or item.get('to_worker') is None else None,
            }
            filtered.append(record)
        if filtered:
            cleaned[key] = filtered
    return cleaned


def save_phase_history(data):
    if not isinstance(data, dict):
        data = {}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PHASE_HISTORY_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def next_workday(d):
    d += timedelta(days=1)
    while d.weekday() in WEEKEND:
        d += timedelta(days=1)
    return d


def _calc_datetimes(day, start, hours):
    """Return ISO start and end datetimes for a task."""
    start_dt = datetime.combine(day, time(hour=WORKDAY_START)) + timedelta(hours=start)
    end_dt = start_dt + timedelta(hours=hours)
    return start_dt.isoformat(), end_dt.isoformat()


def _build_vacation_map():
    """Return a mapping of worker to set of vacation days."""
    vac_map = {}
    vacations = load_vacations()
    for vac in vacations:
        worker = vac['worker']
        day = date.fromisoformat(vac['start'])
        end = date.fromisoformat(vac['end'])
        while day <= end:
            if day.weekday() not in WEEKEND:
                vac_map.setdefault(worker, set()).add(day)
            day += timedelta(days=1)
    return vac_map


def schedule_projects(projects, base_schedule=None):
    """Return schedule and conflicts after assigning all phases.

    If ``base_schedule`` is provided, its tasks are copied into the initial
    worker schedule so they block time slots while scheduling the remaining
    phases.
    """

    projects.sort(key=lambda p: p['start_date'])
    worker_schedule = {w: {} for w in WORKERS}

    for worker, days in (base_schedule or {}).items():
        target_days = worker_schedule.setdefault(worker, {})
        for day, tasks in (days or {}).items():
            if not isinstance(tasks, list):
                continue
            copied = [copy.deepcopy(t) for t in tasks if isinstance(t, dict)]
            if not copied:
                continue
            bucket = target_days.setdefault(day, [])
            bucket.extend(copied)
            bucket.sort(key=lambda t: t.get('start', 0))
    hours_map = load_daily_hours()
    worker_day_map = load_worker_day_hours()
    vac_map = _build_vacation_map()

    def add_vacation_blocks(schedule_map):
        """Add vacation blockers before project tasks so they remain visible."""
        for worker, days in vac_map.items():
            if worker not in schedule_map:
                continue
            for day in days:
                ds = schedule_map[worker].setdefault(day.isoformat(), [])
                existing_vacation = any(
                    task.get('phase') == 'vacaciones'
                    and task.get('pid') == f"vac-{worker}-{day.isoformat()}"
                    for task in ds
                )
                if existing_vacation:
                    continue
                start_time, end_time = _calc_datetimes(day, 0, HOURS_PER_DAY)
                ds.insert(0, {
                    'project': 'Vacaciones',
                    'client': '',
                    'phase': 'vacaciones',
                    'hours': HOURS_PER_DAY,
                    'start': 0,
                    'start_time': start_time,
                    'end_time': end_time,
                    'late': False,
                    'color': '#ff9999',
                    'due_date': '',
                    'start_date': '',
                    'pid': f"vac-{worker}-{day.isoformat()}"
                })

    add_vacation_blocks(worker_schedule)

    def preload_frozen(schedule_map):
        """Reserve the recorded slots for every frozen task before scheduling."""
        for proj in projects:
            for seg in proj.get('frozen_tasks', []) or []:
                worker = seg.get('worker')
                day = seg.get('day')
                if not worker or not day:
                    continue
                try:
                    day_obj = date.fromisoformat(day)
                    day_key = day_obj.isoformat()
                except Exception:
                    day_obj = local_today()
                    day_key = day_obj.isoformat()
                entry = seg.copy()
                entry.pop('worker', None)
                entry.pop('day', None)
                start = entry.get('start', 0) or 0
                hours = entry.get('hours', 0) or 0
                entry['start'] = start
                entry['hours'] = hours
                start_time, end_time = _calc_datetimes(day_obj, start, hours)
                entry['start_time'] = start_time
                entry['end_time'] = end_time
                entry['frozen'] = True
                tasks = schedule_map.setdefault(worker, {}).setdefault(day_key, [])
                duplicate = False
                for existing in tasks:
                    if (
                        existing.get('pid') == entry.get('pid')
                        and existing.get('phase') == entry.get('phase')
                        and existing.get('start') == entry.get('start')
                        and existing.get('hours') == entry.get('hours')
                        and existing.get('part') == entry.get('part')
                    ):
                        duplicate = True
                        break
                if duplicate:
                    continue
                tasks.append(entry)
                tasks.sort(key=lambda t: t.get('start', 0))

    preload_frozen(worker_schedule)

    def record_segment_start(project, phase_name, index, start_day, start_hour):
        if start_day is None:
            return
        seg_map = project.setdefault('segment_starts', {})
        hour_map = project.setdefault('segment_start_hours', {})
        seg_list = seg_map.setdefault(phase_name, [])
        hour_list = hour_map.setdefault(phase_name, [])
        while len(seg_list) <= index:
            seg_list.append(None)
        while len(hour_list) <= index:
            hour_list.append(None)
        if seg_list[index] is None:
            seg_list[index] = start_day.isoformat()
        if hour_list[index] is None:
            hour_list[index] = start_hour if start_hour is not None else 0

    conflicts = []
    for project in projects:
        planned = project.get('planned', True)
        if not planned:
            current = local_today()
            project['start_date'] = current.isoformat()
        else:
            try:
                current = date.fromisoformat(project['start_date'])
            except Exception:
                current = local_today()
                project['start_date'] = current.isoformat()
        hour = 0
        frozen_map = {}
        for t in project.get('frozen_tasks', []):
            frozen_map.setdefault(t.get('phase'), []).append(t)
        end_date = current
        assigned = project.get('assigned', {})
        display_fields = project.get('kanban_display_fields') or {}
        normalized_fields = {}
        if isinstance(display_fields, dict):
            for key, value in display_fields.items():
                normalized_key = _normalize_display_key(key)
                if not normalized_key:
                    continue
                normalized_fields[normalized_key] = value
        phase_deadlines = {}
        for phase_name, field_name in PHASE_DEADLINE_FIELDS.items():
            deadline_value = normalized_fields.get(field_name)
            deadline_date = _parse_phase_deadline(deadline_value)
            if deadline_date:
                phase_deadlines[phase_name] = deadline_date
        for phase in iter_phase_keys(project.get('phases')):
            base_phase_name = phase_base(phase)
            val = project['phases'].get(phase)
            if not val:
                continue
            if phase in frozen_map:
                segs = frozen_map[phase]
                last_day = None
                for seg in segs:
                    day = seg.get('day')
                    if not day:
                        continue
                    try:
                        d = date.fromisoformat(day)
                    except Exception:
                        d = local_today()
                    if last_day is None or d > last_day:
                        last_day = d
                if last_day is not None:
                    current = next_workday(last_day)
                    end_date = max(end_date, last_day)
                hour = 0
                continue

            if base_phase_name == 'pedidos' and isinstance(val, str) and '-' in val:
                start_overrides = project.get('segment_starts', {}).get(phase)
                if start_overrides and start_overrides[0]:
                    current = date.fromisoformat(start_overrides[0])
                days_needed = sum(
                    1
                    for i in range((date.fromisoformat(val) - current).days + 1)
                    if (current + timedelta(days=i)).weekday() not in WEEKEND
                )
                worker = assigned.get(phase) if planned else UNPLANNED
                if not worker:
                    worker = UNPLANNED
                current, hour, end_date, seg_start, seg_start_hour = assign_pedidos(
                    worker_schedule[worker],
                    current,
                    date.fromisoformat(val),
                    project['name'],
                    project['client'],
                    project['due_date'],
                    project.get('due_confirmed'),
                    project.get('color', '#ddd'),
                    project['start_date'],
                    project['id'],
                    worker,
                    project_blocked=project.get('blocked', False),
                    material_date=project.get('material_confirmed_date'),
                )
                record_segment_start(project, phase, 0, seg_start, seg_start_hour)
            else:
                segs = val if isinstance(val, list) else [val]
                seg_workers = project.get('segment_workers', {}).get(phase) if isinstance(val, list) else None
                start_overrides = project.get('segment_starts', {}).get(phase)
                start_hour_overrides = project.get('segment_start_hours', {}).get(phase)
                for idx, seg in enumerate(segs):
                    hours = int(seg)
                    days_needed = (hours + HOURS_PER_DAY - 1) // HOURS_PER_DAY
                    if not planned:
                        worker = UNPLANNED
                    else:
                        worker = None
                        if seg_workers and idx < len(seg_workers):
                            worker = seg_workers[idx]
                        if not worker:
                            worker = assigned.get(phase)
                        if not worker:
                            worker = UNPLANNED
                            if seg_workers:
                                if len(seg_workers) <= idx:
                                    seg_workers.extend([None] * (idx + 1 - len(seg_workers)))
                                seg_workers[idx] = UNPLANNED
                            else:
                                assigned[phase] = UNPLANNED

                    override = None
                    hour_override = None
                    if start_overrides and idx < len(start_overrides) and start_overrides[idx]:
                        override = date.fromisoformat(start_overrides[idx])
                    if start_hour_overrides and idx < len(start_hour_overrides):
                        hour_override = start_hour_overrides[idx]
                    test_start = override or current
                    test_end = test_start
                    for _ in range(days_needed - 1):
                        test_end = next_workday(test_end)
                    # Allow scheduling even if the phase exceeds a confirmed due date.
                    # Previously, phases moved past a client deadline were treated as
                    # unplanned and removed from the calendar. This prevented them
                    # from being visualized after the move. By removing the forced
                    # ``UNPLANNED`` assignment, phases remain in the schedule and are
                    # displayed in the selected cell while still triggering the
                    # appropriate deadline warning elsewhere.
                    manual = False
                    if override:
                        current = override
                        hour = hour_override or 0
                        manual = True
                    current, hour, end_date, seg_start, seg_start_hour = assign_phase(
                        worker_schedule[worker],
                        current,
                        hour,
                        phase,
                        project['name'],
                        project['client'],
                        hours,
                        project['due_date'],
                        project.get('color', '#ddd'),
                        worker,
                        project['start_date'],
                        project['id'],
                        hours_map,
                        worker_day_map,
                        part=idx if isinstance(val, list) else None,
                        manual=manual,
                        project_blocked=project.get('blocked', False),
                        material_date=project.get('material_confirmed_date'),
                        auto=project.get('auto_hours', {}).get(phase),
                        due_confirmed=project.get('due_confirmed'),
                    phase_deadline=phase_deadlines.get(base_phase_name),
                    )
                    record_segment_start(project, phase, idx, seg_start, seg_start_hour)
        project['end_date'] = end_date.isoformat()
        if project.get('due_date'):
            try:
                due_dt = date.fromisoformat(project['due_date'])
                if date.fromisoformat(project['end_date']) > due_dt:
                    msg = 'No se cumple la fecha de entrega'
                    conflicts.append({
                        'id': len(conflicts) + 1,
                        'project': project['name'],
                        'client': project['client'],
                        'message': msg,
                        'key': f"{project['name']}|{msg}",
                    })
            except ValueError:
                pass
    return worker_schedule, conflicts


def assign_phase(
    schedule,
    start_day,
    start_hour,
    phase,
    project_name,
    client,
    hours,
    due_date,
    color,
    worker,
    start_date,
    pid,
    hours_map,
    worker_day_map=None,
    part=None,
    *,
    manual=False,
    project_frozen=False,
    project_blocked=False,
    material_date=None,
    auto=False,
    due_confirmed=False,
    phase_deadline=None,
):
    if hours_map is None:
        hours_map = {}
    if worker_day_map is None:
        worker_day_map = {}

    # When scheduling 'montar', queue the task right after the worker finishes
    # the mounting phase of their previous project unless an explicit start was
    # requested. If there are free hours left that day, reuse them before moving
    # on to the next workday.
    if phase == 'montar' and not manual:
        last, end_hour = _last_phase_info(schedule, 'montar')
        if last and start_day <= last:
            limit = worker_daily_capacity(worker, last, hours_map, worker_day_map)
            if end_hour < limit:
                start_day = last
                start_hour = max(start_hour, end_hour)
            else:
                start_day = next_workday(last)
                start_hour = 0

    day = start_day
    hour = start_hour
    while day.weekday() in WEEKEND or (
        any(t['phase'] == 'vacaciones' for t in schedule.get(day.isoformat(), []))
    ):
        day = next_workday(day)
        hour = 0
    remaining = hours
    last_day = day
    phase_entries = []
    first_day = None
    first_hour = 0
    due_dt = None
    phase_deadline_dt = None
    if isinstance(phase_deadline, date):
        phase_deadline_dt = phase_deadline
    elif phase_deadline is not None:
        parsed = _parse_phase_deadline(phase_deadline)
        if parsed:
            phase_deadline_dt = parsed

    if due_date:
        try:
            due_dt = date.fromisoformat(due_date)
        except ValueError:
            due_dt = None
    # Unplanned tasks ignore the daily limit but still split into 8h blocks.
    if worker == UNPLANNED:
        while remaining > 0:
            if day.weekday() in WEEKEND or any(t['phase'] == 'vacaciones' for t in schedule.get(day.isoformat(), [])):
                day = next_workday(day)
                continue
            day_str = day.isoformat()
            tasks = schedule.get(day_str, [])
            used = max((t.get('start', 0) + t['hours'] for t in tasks), default=0)
            allocate = min(remaining, HOURS_PER_DAY)
            late = bool(due_dt and day > due_dt)
            start_time, end_time = _calc_datetimes(day, used, allocate)
            task = {
                'project': project_name,
                'client': client,
                'phase': phase,
                'hours': allocate,
                'start': used,
                'start_time': start_time,
                'end_time': end_time,
                'late': late,
                'color': color,
                'due_date': due_date,
                'start_date': start_date,
                'pid': pid,
                'part': part,
                'frozen': project_frozen,
                'blocked': project_blocked,
                'material_date': material_date,
                'auto': auto,
            }
            tasks.append(task)
            if first_day is None:
                first_day = day
                first_hour = used
            phase_entries.append((task, day))
            tasks.sort(key=lambda t: t.get('start', 0))
            schedule[day_str] = tasks
            remaining -= allocate
            last_day = day
            day = next_workday(day)
            hour = 0
        project_late = bool(due_dt and last_day > due_dt)
        for task, t_day in phase_entries:
            if due_dt:
                if project_late:
                    task['due_status'] = 'after' if t_day > due_dt else 'before'
                else:
                    task['due_status'] = 'met'
            else:
                task['due_status'] = None
        return day, hour, last_day, first_day, first_hour

    while remaining > 0:
        if day.weekday() in WEEKEND or (
            any(t['phase'] == 'vacaciones' for t in schedule.get(day.isoformat(), []))
        ):
            day = next_workday(day)
            continue
        day_str = day.isoformat()
        tasks = schedule.get(day_str, [])
        tasks.sort(key=lambda t: t.get('start', 0))
        limit = worker_daily_capacity(worker, day, hours_map, worker_day_map)

        if phase in ('tratamiento', 'mecanizar', 'dibujo', 'lanzamiento'):
            start = 0
            available = limit if limit != float('inf') else HOURS_PER_DAY
        else:
            start = hour
            next_task_start = None
            for t in tasks:
                if start + 1e-9 <= t.get('start', 0):
                    next_task_start = t.get('start', 0)
                    break
                start = max(start, t.get('start', 0) + t['hours'])
            if next_task_start is None:
                next_task_start = limit if limit != float('inf') else HOURS_PER_DAY
            if limit != float('inf') and start >= limit:
                day = next_workday(day)
                hour = 0
                continue
            available = next_task_start - start
            if limit != float('inf'):
                available = min(available, limit - start)
            if available <= 0:
                day = next_workday(day)
                hour = 0
                continue

        allocate = min(remaining, available)
        if phase in ('tratamiento', 'mecanizar', 'dibujo', 'lanzamiento'):
            # These phases can accumulate unlimited projects per day but
            # each project aporta como mucho ocho horas diarias. Tras asignar
            # un bloque de trabajo se pasa al siguiente día.
            allocate = min(allocate, HOURS_PER_DAY)
            late = bool(due_dt and day > due_dt)
            start_time, end_time = _calc_datetimes(day, start, allocate)
            task = {
                'project': project_name,
                'client': client,
                'phase': phase,
                'hours': allocate,
                'start': start,
                'start_time': start_time,
                'end_time': end_time,
                'late': late,
                'color': color,
                'due_date': due_date,
                'start_date': start_date,
                'pid': pid,
                'part': part,
                'frozen': project_frozen,
                'blocked': project_blocked,
                'material_date': material_date,
                'auto': auto,
            }
            tasks.append(task)
            if first_day is None:
                first_day = day
                first_hour = start
            phase_entries.append((task, day))
            tasks.sort(key=lambda t: t.get('start', 0))
            schedule[day_str] = tasks
            remaining -= allocate
            last_day = day
            day = next_workday(day)
            hour = 0
            continue

        late = bool(due_dt and day > due_dt)
        start_time, end_time = _calc_datetimes(day, start, allocate)
        task = {
            'project': project_name,
            'client': client,
            'phase': phase,
            'hours': allocate,
            'start': start,
            'start_time': start_time,
            'end_time': end_time,
            'late': late,
            'color': color,
            'due_date': due_date,
            'start_date': start_date,
            'pid': pid,
            'part': part,
            'frozen': project_frozen,
            'blocked': project_blocked,
            'material_date': material_date,
            'auto': auto,
        }
        tasks.append(task)
        if first_day is None:
            first_day = day
            first_hour = start
        phase_entries.append((task, day))
        tasks.sort(key=lambda t: t.get('start', 0))
        schedule[day_str] = tasks
        remaining -= allocate
        last_day = day
        hour = start + allocate
        if hour >= limit and limit != float('inf'):
            day = next_workday(day)
            hour = 0
    project_late = bool(due_dt and last_day > due_dt)
    for task, t_day in phase_entries:
        if due_dt:
            if project_late:
                task['due_status'] = 'after' if t_day > due_dt else 'before'
            else:
                task['due_status'] = 'met'
        else:
            task['due_status'] = None
    if phase_entries:
        if phase_deadline_dt and last_day:
            status = 'late' if last_day > phase_deadline_dt else 'met'
            for task, _ in phase_entries:
                task['phase_deadline_status'] = status
        else:
            for task, _ in phase_entries:
                task['phase_deadline_status'] = None
    next_day = day
    next_hour = hour
    return next_day, next_hour, last_day, first_day, first_hour


def assign_pedidos(
    schedule,
    start_day,
    end_day,
    project_name,
    client,
    due_date,
    due_confirmed,
    color,
    start_date,
    pid,
    worker=None,
    *,
    project_frozen=False,
    project_blocked=False,
    material_date=None,
):
    """Assign the 'pedidos' phase as a continuous range without hour limits."""
    day = start_day
    while day.weekday() in WEEKEND or (
        any(t['phase'] == 'vacaciones' for t in schedule.get(day.isoformat(), []))
    ):
        day = next_workday(day)
    last_day = day
    phase_entries = []
    first_day = None
    due_dt = None
    if due_date:
        try:
            due_dt = date.fromisoformat(due_date)
        except ValueError:
            due_dt = None
    while day <= end_day:
        if day.weekday() in WEEKEND or (
            any(t['phase'] == 'vacaciones' for t in schedule.get(day.isoformat(), []))
        ):
            day += timedelta(days=1)
            continue
        day_str = day.isoformat()
        tasks = schedule.get(day_str, [])
        late = bool(due_dt and day > due_dt)
        start_time, end_time = _calc_datetimes(day, 0, 0)
        task = {
            'project': project_name,
            'client': client,
            'phase': 'pedidos',
            'hours': 0,
            'start': 0,
            'start_time': start_time,
            'end_time': end_time,
            'late': late,
            'color': color,
            'due_date': due_date,
            'start_date': start_date,
            'pid': pid,
            'frozen': project_frozen,
            'blocked': project_blocked,
            'material_date': material_date,
        }
        tasks.append(task)
        if first_day is None:
            first_day = day
        phase_entries.append((task, day))
        schedule[day_str] = tasks
        last_day = day
        day += timedelta(days=1)
    project_late = bool(due_dt and last_day > due_dt)
    for task, t_day in phase_entries:
        if due_dt:
            if project_late:
                task['due_status'] = 'after' if t_day > due_dt else 'before'
            else:
                task['due_status'] = 'met'
        else:
            task['due_status'] = None
    return next_workday(last_day), 0, last_day, first_day, 0


def _last_phase_info(schedule, phase):
    """Return the day and hour when the last ``phase`` finished."""
    last_day = None
    end_hour = 0
    for d, tasks in schedule.items():
        for t in tasks:
            if t['phase'] == phase:
                dt = date.fromisoformat(d)
                finish = t.get('start', 0) + t['hours']
                if not last_day or dt > last_day or (dt == last_day and finish > end_hour):
                    last_day = dt
                    end_hour = finish
    if not last_day:
        return None, 0
    return last_day, end_hour


def _worker_load(schedule, worker):
    """Return total assigned hours for a worker."""
    return sum(
        t['hours']
        for day in schedule.get(worker, {}).values()
        for t in day
    )



def _continuous_free_start(schedule, worker, day, days_needed, vacations=None, hours_map=None):
    """Return the first day with ``days_needed`` consecutive free workdays."""
    vac = vacations.get(worker, set()) if vacations else set()
    sched = schedule.get(worker, {})
    d = day
    while True:
        test = d
        remaining = days_needed
        ok = True
        while remaining > 0:
            if test.weekday() in WEEKEND:
                test = next_workday(test)
                continue
            if test in vac:
                ok = False
                break
            used = sum(t['hours'] for t in sched.get(test.isoformat(), []))
            if used > 0:
                ok = False
                break
            remaining -= 1
            test = next_workday(test)
        if ok:
            return d
        d = next_workday(d)


def compute_schedule_map(projects):
    """Return a mapping of project id to scheduled tasks."""
    temp = copy.deepcopy(projects)
    schedule, _ = schedule_projects(temp)
    mapping = {}
    for worker, days in schedule.items():
        for day, tasks in days.items():
            for t in tasks:
                pid = t['pid']
                mapping.setdefault(pid, []).append((worker, day, t['phase'], t['hours'], t.get('part')))
    for lst in mapping.values():
        lst.sort(
            key=lambda item: (
                item[1],
                item[0],
                '' if item[4] in (None, '') else str(item[4]),
            )
        )
    return mapping


def phase_start_map(projects):
    """Return mapping of pid -> {phase: first_day}"""
    mapping = compute_schedule_map(projects)
    result = {}
    for pid, items in mapping.items():
        for worker, day, phase, hours, _ in items:
            phases = result.setdefault(pid, {})
            current = phases.get(phase)
            if current is None or day < current:
                phases[phase] = day
    return result
