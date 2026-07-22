from flask import Flask, render_template, render_template_string, request, redirect, url_for, jsonify, Response
try:
    import pdfkit
except ModuleNotFoundError:
    pdfkit = None

try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    PdfReader = None
    PdfWriter = None
from datetime import date, timedelta, datetime
from itertools import zip_longest, chain
from collections import defaultdict, Counter
import uuid
import os
import copy
import time
import json
import re
import unicodedata
import subprocess
import tempfile
import shutil
import zipfile
from werkzeug.routing import BuildError
from werkzeug.utils import secure_filename
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import urllib.parse
import sys
import importlib.util
import random
from queue import Queue
import threading
import math

from localtime import local_today, local_now

# Always load this repository's ``schedule.py`` regardless of the working
# directory or any installed package named ``schedule``.  After importing, pull
# the required symbols from the loaded module.  This approach prevents
# ``ImportError`` even if an unexpected third-party module shadows the local
# file.
_schedule_dir = os.path.dirname(os.path.abspath(__file__))
_schedule_path = os.path.join(_schedule_dir, "schedule.py")
if _schedule_dir not in sys.path:
    sys.path.insert(0, _schedule_dir)
_spec = importlib.util.spec_from_file_location("schedule", _schedule_path)
_schedule_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_schedule_mod)
sys.modules['schedule'] = _schedule_mod

# Expose schedule helpers as module-level names
load_projects = _schedule_mod.load_projects
save_projects = _schedule_mod.save_projects
schedule_projects = _schedule_mod.schedule_projects
load_dismissed = _schedule_mod.load_dismissed
save_dismissed = _schedule_mod.save_dismissed
load_extra_conflicts = _schedule_mod.load_extra_conflicts
save_extra_conflicts = _schedule_mod.save_extra_conflicts
load_notes = _schedule_mod.load_notes
save_notes = _schedule_mod.save_notes
load_worker_notes = _schedule_mod.load_worker_notes
save_worker_notes = _schedule_mod.save_worker_notes
load_vacations = _schedule_mod.load_vacations
save_vacations = _schedule_mod.save_vacations
load_daily_hours = _schedule_mod.load_daily_hours
save_daily_hours = _schedule_mod.save_daily_hours
_load_worker_hours_func = getattr(_schedule_mod, "load_worker_hours", None)
_save_worker_hours_func = getattr(_schedule_mod, "save_worker_hours", None)
_load_worker_day_hours_func = getattr(_schedule_mod, "load_worker_day_hours", None)
_save_worker_day_hours_func = getattr(_schedule_mod, "save_worker_day_hours", None)
load_manual_unplanned = getattr(_schedule_mod, "load_manual_unplanned", lambda: [])
save_manual_unplanned = getattr(_schedule_mod, "save_manual_unplanned", lambda entries: entries)
load_phase_history = getattr(_schedule_mod, "load_phase_history", lambda: {})
save_phase_history = getattr(_schedule_mod, "save_phase_history", lambda data: None)
phase_history_key = getattr(_schedule_mod, "phase_history_key", lambda pid, phase, part=None: f"{pid}|{phase}|{'' if part in (None, '', 'None') else part}")
load_inactive_workers = _schedule_mod.load_inactive_workers
save_inactive_workers = _schedule_mod.save_inactive_workers
load_inactive_worker_dates = getattr(_schedule_mod, 'load_inactive_worker_dates', lambda: {})
save_inactive_worker_dates = getattr(_schedule_mod, 'save_inactive_worker_dates', lambda data: None)
set_worker_order = _schedule_mod.set_worker_order
rename_worker = getattr(_schedule_mod, "rename_worker", lambda old, new: False)
delete_worker = getattr(_schedule_mod, "delete_worker", lambda name: False)
PHASE_ORDER = _schedule_mod.PHASE_ORDER
WORKERS = _schedule_mod.WORKERS
IGOR_END = _schedule_mod.IGOR_END
compute_schedule_map = _schedule_mod.compute_schedule_map
UNPLANNED = _schedule_mod.UNPLANNED
if hasattr(_schedule_mod, "phase_start_map"):
    phase_start_map = _schedule_mod.phase_start_map
else:
    def phase_start_map(projects):
        mapping = compute_schedule_map(projects)
        result = {}
        for pid, items in mapping.items():
            for worker, day, phase, hours, _ in items:
                result.setdefault(pid, {}).setdefault(phase, day)
        return result
WEEKEND = _schedule_mod.WEEKEND
HOURS_PER_DAY = _schedule_mod.HOURS_PER_DAY
DEFAULT_FRIDAY_HOURS = getattr(_schedule_mod, "DEFAULT_FRIDAY_HOURS", 7)
HOURS_LIMITS = _schedule_mod.HOURS_LIMITS
next_workday = _schedule_mod.next_workday
DEADLINE_MSG = 'Fecha cliente soprepasada.'
CLIENT_DEADLINE_MSG = 'FECHA TOPE SOBREPASADA.'
READY_TO_ARCHIVE_TASK_BACKGROUND = '#D9D9D9'

AUTO_RECEIVING_PHASE = 'preparar material'
DEFAULT_INITIAL_PHASE_HOURS = {}
NEW_PROJECT_EXCLUDED_PHASES = {'verificar'}


def _ensure_default_initial_phases(project):
    if not isinstance(project, dict):
        return False
    phases = project.setdefault('phases', {})
    assigned = project.setdefault('assigned', {})
    changed = False
    seeded = bool(project.get('initial_default_phases_seeded'))
    if not seeded:
        for phase_name, hours in DEFAULT_INITIAL_PHASE_HOURS.items():
            current = phases.get(phase_name)
            if not isinstance(current, list):
                try:
                    current_val = int(current) if current is not None else 0
                except Exception:
                    current_val = 0
                if current_val <= 0:
                    phases[phase_name] = hours
                    changed = True
            if phase_name in phases and not assigned.get(phase_name):
                assigned[phase_name] = UNPLANNED
                changed = True
        project['initial_default_phases_seeded'] = True
        changed = True
    else:
        for phase_name in DEFAULT_INITIAL_PHASE_HOURS:
            if phase_name in phases and not assigned.get(phase_name):
                assigned[phase_name] = UNPLANNED
                changed = True
    return changed


AUTO_RECEIVING_DEPENDENCIES = (
    'lanzamiento',
    'montar',
    'soldar',
    'montar 2º',
    'soldar 2º',
    'trabajos en exterior',
    'mecanizar',
    'tratamiento',
    'pintar',
)


def _default_day_hours(day_iso):
    try:
        day_obj = date.fromisoformat(str(day_iso)[:10])
    except Exception:
        return HOURS_PER_DAY
    if day_obj.weekday() == 4:
        return DEFAULT_FRIDAY_HOURS
    return HOURS_PER_DAY


MATERIAL_STATUS_ORDER = ['archived', 'complete', 'verify', 'missing', 'pending']
MATERIAL_STATUS_LABELS = {
    'missing': 'FALTA MATERIAL',
    'pending': 'MATERIAL POR PEDIR',
    'verify': 'SOLO FALTA VERIFICAR MATERIAL',
    'archived': 'MATERIAL ARCHIVADO',
    'complete': 'MATERIAL COMPLETO',
}


KANBAN_CARD_FETCH_COOLDOWN_SECONDS = 60
_KANBAN_CARD_FETCH_CACHE = {}
KANBAN_CARDS_LOCK = threading.RLock()


def _phase_total_hours(value):
    """Return the numeric hours stored for a phase entry."""

    if isinstance(value, list):
        total = 0
        for item in value:
            total += _phase_total_hours(item)
        return total
    if value in (None, ''):
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _remove_phase_references(project, phase):
    """Remove a phase and related bookkeeping from a project."""

    changed = False
    phases = project.get('phases')
    if phases and phase in phases:
        phases.pop(phase, None)
        changed = True
        if not phases:
            project.pop('phases', None)
    assigned = project.get('assigned')
    if assigned and phase in assigned:
        assigned.pop(phase, None)
        changed = True
        if not assigned:
            project.pop('assigned', None)
    seg_starts = project.get('segment_starts')
    if seg_starts and phase in seg_starts:
        seg_starts.pop(phase, None)
        changed = True
        if not seg_starts:
            project.pop('segment_starts', None)
    seg_workers = project.get('segment_workers')
    if seg_workers and phase in seg_workers:
        seg_workers.pop(phase, None)
        changed = True
        if not seg_workers:
            project.pop('segment_workers', None)
    auto = project.get('auto_hours')
    if auto and phase in auto:
        auto.pop(phase, None)
        changed = True
        if not auto:
            project.pop('auto_hours', None)
    frozen = project.get('frozen_tasks')
    if frozen:
        new_frozen = [t for t in frozen if t.get('phase') != phase]
        if len(new_frozen) != len(frozen):
            project['frozen_tasks'] = new_frozen
            changed = True
    return changed


def _cleanup_auto_receiving_placeholder(project):
    """Drop the automatic receiving phase if other phases gained hours."""

    auto = project.get('auto_hours') or {}
    if not auto.get(AUTO_RECEIVING_PHASE):
        return False
    phases = project.get('phases') or {}
    for phase in AUTO_RECEIVING_DEPENDENCIES:
        if _phase_total_hours(phases.get(phase)) > 0:
            return _remove_phase_references(project, AUTO_RECEIVING_PHASE)
    return False


def _normalize_part_index(value):
    """Return the integer index stored for a segmented phase part."""

    if value in (None, '', 'None'):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text or text.lower() == 'none':
        return None
    try:
        return int(text)
    except Exception:
        return None


def _rebalance_archived_phase_hours(entry, pid, phase, new_total, *, part_index=None):
    """Update archived calendar tasks so their hours add up to ``new_total``."""

    if not entry or new_total is None:
        return
    tasks = entry.get('tasks')
    if not isinstance(tasks, list):
        return
    pid_str = str(pid or '').strip()
    phase_key = str(phase or '').strip().lower()
    if not pid_str or not phase_key:
        return
    try:
        target_total = int(new_total)
    except Exception:
        try:
            target_total = int(float(new_total))
        except Exception:
            return
    if target_total < 0:
        target_total = 0
    matched_payloads = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        payload = item.get('task')
        if not isinstance(payload, dict):
            continue
        payload_pid = str(payload.get('pid') or '').strip()
        if payload_pid != pid_str:
            continue
        payload_phase = str(payload.get('phase') or '').strip().lower()
        if payload_phase != phase_key:
            continue
        payload_part = _normalize_part_index(payload.get('part'))
        if part_index is not None:
            if payload_part != part_index:
                continue
        else:
            if payload_part is not None:
                continue
        matched_payloads.append(payload)
    if not matched_payloads:
        return
    previous_total = sum(max(_phase_total_hours(p.get('hours')), 0) for p in matched_payloads)
    if previous_total <= 0:
        matched_payloads[0]['hours'] = target_total
        for payload in matched_payloads[1:]:
            payload['hours'] = 0
        return
    scaled = []
    floor_sum = 0
    for payload in matched_payloads:
        current = max(_phase_total_hours(payload.get('hours')), 0)
        scaled_value = (current * target_total) / previous_total if previous_total else 0
        floor_value = int(scaled_value)
        fractional = scaled_value - floor_value
        scaled.append([payload, floor_value, fractional, current])
        floor_sum += floor_value
    remainder = target_total - floor_sum
    if remainder > 0:
        scaled.sort(key=lambda item: (-item[2], -item[3]))
        idx = 0
        while remainder > 0 and scaled:
            payload, value, frac, current = scaled[idx]
            scaled[idx][1] = value + 1
            remainder -= 1
            idx = (idx + 1) % len(scaled)
    elif remainder < 0:
        scaled.sort(key=lambda item: (item[2], item[3]))
        idx = 0
        attempts = 0
        max_attempts = len(scaled) * 4 if scaled else 0
        while remainder < 0 and scaled and attempts < max_attempts:
            payload, value, frac, current = scaled[idx]
            if value > 0:
                scaled[idx][1] = value - 1
                remainder += 1
            idx = (idx + 1) % len(scaled)
            attempts += 1
        if remainder < 0:
            for i in range(len(scaled)):
                if remainder >= 0:
                    break
                if scaled[i][1] > 0:
                    scaled[i][1] -= 1
                    remainder += 1
    for payload, value, _fractional, _current in scaled:
        payload['hours'] = max(int(value), 0)


def _build_archived_frozen_tasks(entry, *, skip_phase=None, skip_part=None):
    """Return frozen-task payloads for an archived calendar entry."""

    if not isinstance(entry, dict):
        return []

    skip_phase_key = (skip_phase or '').strip().lower()
    skip_has_phase = bool(skip_phase_key)
    skip_part_index = _normalize_part_index(skip_part)
    project = entry.get('project') or {}
    pid = str(project.get('id') or entry.get('pid') or '').strip()
    if not pid:
        return []

    frozen = []
    for item in entry.get('tasks') or []:
        if not isinstance(item, dict):
            continue
        worker = item.get('worker')
        day = item.get('day')
        payload = item.get('task')
        if not worker or not day or not isinstance(payload, dict):
            continue
        phase = str(payload.get('phase') or '').strip().lower()
        part_value = _normalize_part_index(payload.get('part'))
        if skip_has_phase and phase == skip_phase_key:
            if skip_part_index is None:
                if part_value is None:
                    continue
            elif part_value == skip_part_index:
                continue
        hours = _phase_total_hours(payload.get('hours'))
        if hours <= 0:
            continue
        start = payload.get('start') or 0
        try:
            start = int(start)
        except Exception:
            start = 0
        frozen_task = copy.deepcopy(payload)
        frozen_task['pid'] = pid
        frozen_task['phase'] = payload.get('phase')
        frozen_task['hours'] = max(int(hours), 0)
        frozen_task['start'] = max(start, 0)
        frozen_task['worker'] = worker
        frozen_task['day'] = day
        frozen.append(frozen_task)

    frozen.sort(
        key=lambda t: (
            str(t.get('day') or ''),
            t.get('start', 0),
            str(t.get('worker') or ''),
        )
    )
    return frozen


def _extract_archived_tasks_from_schedule(schedule, pid):
    """Return archived task payloads for ``pid`` from ``schedule``."""

    pid_str = str(pid or '').strip()
    if not pid_str:
        return []

    tasks = []
    for worker, days in schedule.items():
        if not isinstance(days, dict):
            continue
        for day, entries in days.items():
            if not isinstance(entries, list):
                continue
            for task in entries:
                if str(task.get('pid') or '').strip() != pid_str:
                    continue
                task_copy = copy.deepcopy(task)
                task_copy['archived_shadow'] = True
                task_copy['frozen'] = True
                task_copy['color'] = '#d9d9d9'
                task_copy['frozen_background'] = 'rgba(128, 128, 128, 0.35)'
                task_copy['pid'] = pid_str
                tasks.append({
                    'worker': worker,
                    'day': day,
                    'task': task_copy,
                })

    tasks.sort(
        key=lambda item: (
            str(item.get('day') or ''),
            (item.get('task') or {}).get('start', 0),
            str(item.get('worker') or ''),
        )
    )
    return tasks


def _reschedule_archived_phase_hours(entries, pid, phase, *, part_index=None):
    """Reschedule archived tasks after updating ``phase`` hours."""

    if not entries:
        return False

    pid_str = str(pid or '').strip()
    phase_key = (phase or '').strip().lower()
    if not pid_str or not phase_key:
        return False

    dataset = []
    entry_map = {}
    for entry in entries:
        project = entry.get('project')
        if not isinstance(project, dict):
            continue
        project_pid = str(project.get('id') or entry.get('pid') or '').strip()
        if not project_pid:
            continue
        project_copy = copy.deepcopy(project)
        project_copy['id'] = project_pid
        project_copy.setdefault('phases', {})
        project_copy.setdefault('assigned', {})
        project_copy.setdefault('auto_hours', {})
        project_copy.setdefault('frozen_tasks', [])
        skip_phase = phase if project_pid == pid_str else None
        skip_part = part_index if project_pid == pid_str else None
        project_copy['frozen_tasks'] = _build_archived_frozen_tasks(
            entry,
            skip_phase=skip_phase,
            skip_part=skip_part,
        )
        preserved_starts = {}
        if project_pid == pid_str:
            for item in entry.get('tasks') or []:
                if not isinstance(item, dict):
                    continue
                day_value = item.get('day')
                payload = item.get('task')
                if not day_value or not isinstance(payload, dict):
                    continue
                phase_value = str(payload.get('phase') or '').strip().lower()
                if phase_value != phase_key:
                    continue
                part_value = _normalize_part_index(payload.get('part'))
                if part_index is not None and part_value != part_index:
                    continue
                try:
                    day_obj = date.fromisoformat(str(day_value)[:10])
                    day_iso = day_obj.isoformat()
                except Exception:
                    continue
                try:
                    start_val = int(payload.get('start', 0) or 0)
                except Exception:
                    start_val = 0
                key = part_value if part_value is not None else None
                existing = preserved_starts.get(key)
                if existing:
                    existing_day = existing[0]
                    existing_start = existing[1]
                    if day_obj > existing_day:
                        continue
                    if day_obj == existing_day and start_val >= existing_start:
                        continue
                preserved_starts[key] = (day_obj, start_val, day_iso)
        if project_pid == pid_str:
            seg_starts = project_copy.get('segment_starts')
            if isinstance(seg_starts, dict):
                for key in list(seg_starts.keys()):
                    normalized = str(key).strip().lower()
                    if normalized != phase_key:
                        continue
                    if part_index is None:
                        seg_starts.pop(key, None)
                    else:
                        parts = seg_starts.get(key)
                        if isinstance(parts, list) and part_index < len(parts):
                            parts[part_index] = None
            seg_hours = project_copy.get('segment_start_hours')
            if isinstance(seg_hours, dict):
                for key in list(seg_hours.keys()):
                    normalized = str(key).strip().lower()
                    if normalized != phase_key:
                        continue
                    if part_index is None:
                        seg_hours.pop(key, None)
                    else:
                        hours_list = seg_hours.get(key)
                        if isinstance(hours_list, list) and part_index < len(hours_list):
                            hours_list[part_index] = 0
            if preserved_starts:
                seg_starts_map = project_copy.setdefault('segment_starts', {})
                hour_starts_map = project_copy.setdefault('segment_start_hours', {})
                seg_list = seg_starts_map.setdefault(phase, [])
                hour_list = hour_starts_map.setdefault(phase, [])
                for part_key, info in preserved_starts.items():
                    _, start_hour, day_iso = info
                    idx = part_key if part_key is not None else 0
                    while len(seg_list) <= idx:
                        seg_list.append(None)
                    while len(hour_list) <= idx:
                        hour_list.append(0)
                    seg_list[idx] = day_iso
                    hour_list[idx] = start_hour
        project_copy['archived_shadow'] = True
        project_copy['kanban_archived'] = True
        project_copy['kanban_column'] = project_copy.get('kanban_column') or 'Ready to Archive'
        project_copy.setdefault('material_status', 'archived')
        project_copy.setdefault('material_missing_titles', [])
        dataset.append(project_copy)
        entry_map[project_pid] = {
            'entry': entry,
            'project_ref': project,
            'project_copy': project_copy,
        }

    if pid_str not in entry_map:
        return False

    try:
        schedule_map, _conflicts = schedule_projects(dataset)
    except Exception:
        return False

    for project_pid, info in entry_map.items():
        entry = info['entry']
        project_copy = info['project_copy']
        project_ref = info['project_ref']
        tasks = _extract_archived_tasks_from_schedule(schedule_map, project_pid)
        entry['tasks'] = tasks
        frozen_payloads = []
        for item in tasks:
            payload = copy.deepcopy(item.get('task') or {})
            payload['worker'] = item.get('worker')
            payload['day'] = item.get('day')
            frozen_payloads.append(payload)
        project_copy['frozen_tasks'] = frozen_payloads
        project_ref.clear()
        project_ref.update(project_copy)
        project_ref['id'] = project_pid
        project_ref['archived_shadow'] = True
        project_ref['kanban_archived'] = True
        project_ref['kanban_column'] = project_ref.get('kanban_column') or 'Ready to Archive'
        project_ref.setdefault('material_status', 'archived')
        project_ref.setdefault('material_missing_titles', [])

    return True


app = Flask(__name__)


def _resolve_wkhtmltopdf_path():
    """Resolve wkhtmltopdf from env, PATH, then the legacy Windows path."""

    for env_var in ('WKHTMLTOPDF_PATH', 'EFIMERO_WKHTMLTOPDF_PATH'):
        configured_path = os.environ.get(env_var)
        if configured_path:
            return configured_path

    path_binary = shutil.which('wkhtmltopdf')
    if path_binary:
        return path_binary

    return r"C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe"


def _build_pdfkit_configuration():
    if pdfkit is None:
        app.logger.warning('pdfkit no está instalado; la generación de PDF estará deshabilitada hasta instalarlo.')
        return None

    wkhtmltopdf_path = _resolve_wkhtmltopdf_path()
    try:
        configuration = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
    except OSError:
        app.logger.warning(
            'No se pudo inicializar wkhtmltopdf en %s; se intentará de nuevo al generar el PDF.',
            wkhtmltopdf_path,
            exc_info=True,
        )
        return None
    app.logger.info('Usando wkhtmltopdf en: %s', wkhtmltopdf_path)
    return configuration


config = _build_pdfkit_configuration()


def _safe_url(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return ''


@app.context_processor
def inject_optional_urls():
    return {'split_phase_url': _safe_url('split_phase_route')}
app.url_map.strict_slashes = False


@app.before_request
def _ensure_background_workers_started():
    ensure_daily_backup_thread()


@app.template_filter('format_due_date')
def format_due_date(value, include_year=True):
    """Return a human readable ``dd/mm`` or ``dd/mm/YYYY`` string."""

    if not value:
        return ''

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return ''
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            parsed = parse_input_date(value)
            if not parsed:
                return value
            value = parsed

    if not isinstance(value, date):
        return str(value)

    fmt = '%d/%m/%Y' if include_year else '%d/%m'
    return value.strftime(fmt)

# Basic HTTP authentication setup
AUTH_USER = os.environ.get("EFIMERO_USER", "admin")
AUTH_PASS = os.environ.get("EFIMERO_PASS", "secreto")


def _check_auth(user, password):
    return user == AUTH_USER and password == AUTH_PASS


def _authenticate():
    return Response(
        "Acceso denegado.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Login requerido"'},
    )


@app.before_request
def _require_auth():
    if request.path.startswith("/static") or request.path.startswith("/kanbanize-webhook"):
        return
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return _authenticate()


@app.route('/client-error', methods=['POST'])
def log_client_error():
    data = request.get_json(silent=True) or {}
    message = data.get('message')
    stack = data.get('stack')
    if message:
        app.logger.error('Client error: %s', message)
    if stack:
        app.logger.error(stack)
    return ('', 204)

COLORS = [
    '#ffd9e8', '#ffe4c4', '#e0ffff', '#d0f0c0', '#fef9b7', '#ffe8d6',
    '#dcebf1', '#e6d3f8', '#fdfd96', '#e7f5ff', '#ccffcc', '#e9f7fd',
    '#ffd8be', '#f8f0fb', '#f2ffde', '#fae1dd', '#fffff0', '#e8f0fe',
    '#ffcfd2', '#f0fff4', '#e7f9ea', '#fff2cc', '#e0e0ff', '#f0f8ff',
]

_last_api_color = None


def _next_api_color():
    """Return a light random color distinct from the last one."""
    global _last_api_color
    while True:
        r = random.randint(0, 255)
        g = random.randint(0, 255)
        b = random.randint(0, 255)
        # Relative luminance to avoid very dark colors
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance < 140:
            continue
        color = f"#{r:02x}{g:02x}{b:02x}"
        if color != _last_api_color:
            _last_api_color = color
            return color


def compute_frozen_background(color, alpha=0.25):
    """Return an ``rgba`` value that reuses ``color`` with transparency."""

    fallback = f'rgba(204, 229, 255, {alpha})'
    if not color or not isinstance(color, str):
        return fallback

    color = color.strip()
    if color.startswith('#'):
        hex_value = color[1:]
        if len(hex_value) == 3:
            hex_value = ''.join(ch * 2 for ch in hex_value)
        if len(hex_value) == 6:
            try:
                r = int(hex_value[0:2], 16)
                g = int(hex_value[2:4], 16)
                b = int(hex_value[4:6], 16)
            except ValueError:
                return fallback
            return f'rgba({r}, {g}, {b}, {alpha})'
        return fallback

    match = re.match(r'rgba?\(([^)]+)\)', color)
    if match:
        parts = [p.strip() for p in match.group(1).split(',')]
        if len(parts) >= 3:
            try:
                r = max(0, min(255, int(float(parts[0]))))
                g = max(0, min(255, int(float(parts[1]))))
                b = max(0, min(255, int(float(parts[2]))))
            except ValueError:
                return fallback
            return f'rgba({r}, {g}, {b}, {alpha})'

    return fallback


def annotate_frozen_background(task):
    if not isinstance(task, dict):
        return
    if task.get('frozen'):
        explicit_background = task.get('frozen_background')
        if explicit_background:
            return
        task['frozen_background'] = compute_frozen_background(task.get('color'))
    else:
        task.pop('frozen_background', None)


def annotate_schedule_frozen_background(schedule):
    for days in schedule.values():
        if not isinstance(days, dict):
            continue
        for tasks in days.values():
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                annotate_frozen_background(task)


def load_archived_calendar_entries():
    if not os.path.exists(ARCHIVED_CALENDAR_FILE):
        return []
    try:
        with open(ARCHIVED_CALENDAR_FILE, 'r') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    cleaned = []
    for entry in data:
        if isinstance(entry, dict):
            cleaned.append(entry)
    return cleaned


def save_archived_calendar_entries(entries):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ARCHIVED_CALENDAR_FILE, 'w') as fh:
        json.dump(entries or [], fh)


def store_archived_calendar_entry(entry):
    if not isinstance(entry, dict):
        return
    entries = load_archived_calendar_entries()

    def _same(existing):
        if not isinstance(existing, dict):
            return False
        pid = str(existing.get('pid') or '')
        kid = str(existing.get('kanban_id') or '')
        entry_pid = str(entry.get('pid') or '')
        entry_kid = str(entry.get('kanban_id') or '')
        if entry_pid and pid and entry_pid == pid:
            return True
        if entry_kid and kid and entry_kid == kid:
            return True
        return False

    filtered = [e for e in entries if not _same(e)]
    filtered.append(entry)
    save_archived_calendar_entries(filtered)


def remove_archived_calendar_entry(pid=None, kanban_id=None, names=None):
    entries = load_archived_calendar_entries()
    if not entries:
        return False
    pid = str(pid or '').strip()
    kid = str(kanban_id or '').strip()
    name_set = set()
    if names:
        for name in names:
            if isinstance(name, str) and name.strip():
                name_set.add(name.strip())

    def _matches(entry):
        if not isinstance(entry, dict):
            return False
        entry_pid = str(entry.get('pid') or '').strip()
        entry_kid = str(entry.get('kanban_id') or '').strip()
        entry_name = (entry.get('name') or '').strip()
        if pid and entry_pid and pid == entry_pid:
            return True
        if kid and entry_kid and kid == entry_kid:
            return True
        if name_set and entry_name and entry_name in name_set:
            return True
        return False

    filtered = [e for e in entries if not _matches(e)]
    if len(filtered) != len(entries):
        save_archived_calendar_entries(filtered)
        return True
    return False


def inject_archived_tasks(schedule):
    entries = load_archived_calendar_entries()
    project_infos = {}
    hours_map = load_daily_hours()
    worker_day_overrides = load_worker_day_hours()

    def _archived_day_limit(worker, day_iso):
        limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
        worker_override = (worker_day_overrides.get(worker) or {}).get(day_iso)
        if worker_override is not None:
            try:
                return float(worker_override)
            except Exception:
                return HOURS_PER_DAY
        day_override = hours_map.get(day_iso)
        if day_override is None:
            day_override = _default_day_hours(day_iso)
        if day_override is not None and limit != float('inf'):
            try:
                limit = min(limit, float(day_override))
            except Exception:
                pass
        if limit == float('inf') or limit is None:
            return float(_default_day_hours(day_iso))
        try:
            return float(limit)
        except Exception:
            return HOURS_PER_DAY

    def _available_archived_spans(tasks, limit, *, earliest):
        """Yield free (start, end) spans for *tasks* under the given *limit*."""

        tasks = sorted(tasks, key=lambda t: t.get('start', 0))
        cursor = max(0, earliest)
        for existing in tasks:
            try:
                start = float(existing.get('start') or 0)
                end = start + _phase_total_hours(existing.get('hours'))
            except Exception:
                continue
            start = max(start, 0)
            end = max(end, start)
            if limit != float('inf'):
                start = min(start, limit)
                end = min(end, limit)
            if start - cursor > 1e-9:
                yield (cursor, start)
            cursor = max(cursor, end)
            if limit != float('inf') and cursor >= limit:
                return

        if limit == float('inf'):
            # Provide a generous window so callers can allocate all pending hours
            # in a single day when limits are disabled for the worker.
            yield (cursor, cursor + HOURS_PER_DAY * 2)
        elif cursor < limit:
            yield (cursor, limit)

    def _place_archived_task(task, worker, day_str):
        try:
            day_obj = date.fromisoformat(str(day_str)[:10])
        except Exception:
            day_obj = local_today()

        try:
            raw_hours = _phase_total_hours(task.get('hours'))
            remaining = max(int(raw_hours), 0)
        except Exception:
            remaining = 0
        try:
            requested_start = int(task.get('start') or 0)
        except Exception:
            requested_start = 0

        template = copy.deepcopy(task)
        template['archived_shadow'] = True
        template['frozen'] = True
        template['color'] = '#d9d9d9'
        template['frozen_background'] = 'rgba(128, 128, 128, 0.35)'
        if 'pid' in template:
            template['pid'] = str(template['pid'])

        day_cursor = day_obj
        start_cursor = requested_start
        safety = 0
        while remaining > 0 and safety < 800:
            safety += 1
            day_iso = day_cursor.isoformat()
            limit = _archived_day_limit(worker, day_iso)
            if limit is None or limit <= 0:
                day_cursor = next_workday(day_cursor)
                start_cursor = 0
                continue

            tasks = schedule.setdefault(worker, {}).setdefault(day_iso, [])
            placed = False
            for span_start, span_end in _available_archived_spans(
                tasks, limit, earliest=start_cursor
            ):
                slot_start = max(span_start, start_cursor)
                available = max(span_end - slot_start, 0)
                if available <= 0:
                    continue
                allocate = min(remaining, available)
                seg = copy.deepcopy(template)
                seg['hours'] = allocate
                seg['start'] = slot_start
                try:
                    start_time, end_time = _schedule_mod._calc_datetimes(
                        day_cursor, slot_start, allocate
                    )
                    seg['start_time'] = start_time
                    seg['end_time'] = end_time
                except Exception:
                    pass

                duplicate = False
                for existing in tasks:
                    if (
                        existing.get('pid') == seg.get('pid')
                        and existing.get('phase') == seg.get('phase')
                        and existing.get('part') == seg.get('part')
                        and existing.get('start') == seg.get('start')
                        and existing.get('hours') == seg.get('hours')
                    ):
                        duplicate = True
                        break
                if duplicate:
                    remaining -= allocate
                    start_cursor = slot_start + allocate
                    placed = True
                    if remaining <= 0:
                        break
                    continue

                tasks.append(seg)
                tasks.sort(key=lambda t: t.get('start', 0))
                remaining -= allocate
                start_cursor = slot_start + allocate
                placed = True
                if remaining <= 0:
                    break

            if remaining <= 0:
                break
            if not placed:
                day_cursor = next_workday(day_cursor)
                start_cursor = 0
            else:
                day_cursor = next_workday(day_cursor)
                start_cursor = 0
        return remaining <= 0
    for entry in entries:
        tasks = entry.get('tasks') or []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            worker = item.get('worker')
            day = item.get('day')
            payload = item.get('task')
            if not worker or not day or not isinstance(payload, dict):
                continue
            _place_archived_task(payload, worker, day)

        info = entry.get('project')
        if isinstance(info, dict):
            info_copy = copy.deepcopy(info)
            info_copy['archived_shadow'] = True
            info_copy['kanban_archived'] = True
            info_copy['kanban_column'] = info_copy.get('kanban_column') or 'Ready to Archive'
            info_copy.setdefault('kanban_display_fields', {})
            info_copy.setdefault('kanban_attachments', [])
            phases = info_copy.setdefault('phases', {})
            info_copy.setdefault('assigned', {})
            info_copy.setdefault('auto_hours', {})
            info_copy.setdefault('frozen_tasks', [])
            info_copy['frozen_phases'] = sorted(
                {t.get('phase') for t in info_copy.get('frozen_tasks', []) if t.get('phase')}
            )
            info_copy['phase_sequence'] = list(phases.keys())
            info_copy['material_status'] = 'archived'
            info_copy.setdefault('material_missing_titles', [])
            pid = str(info_copy.get('id') or entry.get('pid') or '')
            if not pid:
                continue
            info_copy['id'] = pid
            project_infos[pid] = info_copy
    return entries, project_infos


def build_schedule_with_archived(projects, include_optional_phases=True):
    """Return schedule/conflicts with archived tasks preloaded."""

    base_schedule = {}
    archived_entries, archived_project_map = inject_archived_tasks(base_schedule)
    if not include_optional_phases:
        for worker, days in list(base_schedule.items()):
            for day, tasks in list(days.items()):
                filtered = [
                    t for t in tasks if phase_base(t.get('phase')) not in OPTIONAL_PHASES
                ]
                if filtered:
                    days[day] = filtered
                else:
                    days.pop(day, None)
            if not days:
                base_schedule.pop(worker, None)
    schedule, conflicts = schedule_projects(projects, base_schedule=base_schedule)

    ready_key = normalize_key('Ready to Archive')
    ready_pids = {
        str(p.get('id'))
        for p in (projects or [])
        if p.get('id') and normalize_key(p.get('kanban_column')) == ready_key
    }
    if ready_pids:
        for days in schedule.values():
            if not isinstance(days, dict):
                continue
            for tasks in days.values():
                if not isinstance(tasks, list):
                    continue
                for task in tasks:
                    if str(task.get('pid')) not in ready_pids:
                        continue
                    task['archived_shadow'] = True
                    task['frozen'] = True
                    task['color'] = READY_TO_ARCHIVE_TASK_BACKGROUND
                    task['frozen_background'] = READY_TO_ARCHIVE_TASK_BACKGROUND

    return schedule, conflicts, archived_entries, archived_project_map

MIN_DATE = date(2024, 1, 1)
MAX_DATE = date(2026, 12, 31)
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DATA_DIR = os.environ.get('EFIMERO_DATA_DIR', 'data')
BACKUP_DIR = os.environ.get('EFIMERO_BACKUP_DIR', os.path.join(_schedule_dir, 'backups'))
os.makedirs(BACKUP_DIR, exist_ok=True)
BACKUP_STATE_FILE = os.path.join(BACKUP_DIR, '.last_daily_backup')
BACKUP_CHECK_INTERVAL_SECONDS = 600
BACKUP_EXCLUDE_DIRS = {'backups', '.git', '__pycache__', '.pytest_cache', '.mypy_cache'}
BACKUP_EXCLUDE_FILES = {'.DS_Store', 'Thumbs.db'}
BACKUP_LOCK = threading.Lock()
_BACKUP_THREAD_STARTED = False
KANBAN_CARDS_FILE = os.path.join(DATA_DIR, 'kanban_cards.json')
KANBAN_PREFILL_FILE = os.path.join(DATA_DIR, 'kanban_prefill.json')
KANBAN_COLUMN_COLORS_FILE = os.path.join(DATA_DIR, 'kanban_column_colors.json')
TRACKER_FILE = os.path.join(DATA_DIR, 'tracker.json')
ARCHIVED_CALENDAR_FILE = os.path.join(DATA_DIR, 'archived_calendar.json')
PLANNER_SETTINGS_FILE = os.path.join(DATA_DIR, 'planner_settings.json')
TRANSPORT_OPTIONS_FILE = os.path.join(DATA_DIR, 'transport_options.json')
OPTIONAL_PHASES = {'mecanizar', 'tratamiento'}
WEEKLY_TRACKING_FILE = os.path.join(DATA_DIR, 'weekly_tracking.json')

SNAPSHOT_FILE = os.path.join(DATA_DIR, 'planning_snapshot.json')


def _should_include_backup_file(root_dir, file_name):
    if file_name in BACKUP_EXCLUDE_FILES:
        return False
    if file_name.endswith(('.pyc', '.pyo')):
        return False
    rel_path = os.path.relpath(os.path.join(root_dir, file_name), _schedule_dir)
    return not rel_path.startswith('backups' + os.sep)


def create_full_backup():
    """Create a timestamped zip backup with planner files and cache data."""
    with BACKUP_LOCK:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = local_now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"planner_backup_{timestamp}.zip"
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        with zipfile.ZipFile(backup_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(_schedule_dir):
                rel_root = os.path.relpath(root, _schedule_dir)
                dirs[:] = [d for d in dirs if d not in BACKUP_EXCLUDE_DIRS]
                for file_name in files:
                    if not _should_include_backup_file(root, file_name):
                        continue
                    abs_path = os.path.join(root, file_name)
                    arcname = os.path.relpath(abs_path, _schedule_dir)
                    zf.write(abs_path, arcname)
        with open(BACKUP_STATE_FILE, 'w', encoding='utf-8') as fh:
            fh.write(local_today().isoformat())
        return backup_path


def _load_last_backup_day():
    if not os.path.exists(BACKUP_STATE_FILE):
        return None
    try:
        with open(BACKUP_STATE_FILE, 'r', encoding='utf-8') as fh:
            raw = fh.read().strip()
        if not raw:
            return None
        return date.fromisoformat(raw)
    except Exception:
        return None


def _daily_backup_worker():
    while True:
        try:
            today = local_today()
            last_day = _load_last_backup_day()
            if last_day != today:
                path = create_full_backup()
                app.logger.info('Backup diario generado: %s', path)
        except Exception:
            app.logger.exception('Error generando backup diario')
        time.sleep(BACKUP_CHECK_INTERVAL_SECONDS)


def ensure_daily_backup_thread():
    global _BACKUP_THREAD_STARTED
    if _BACKUP_THREAD_STARTED:
        return
    _BACKUP_THREAD_STARTED = True
    thread = threading.Thread(target=_daily_backup_worker, daemon=True, name='daily-backup-worker')
    thread.start()


TRANSPORT_DEFAULT_LOCATIONS = [
    'Expedición',
    'Recepción',
    'Entrada',
    'Armario azul',
    'Frente a inox',
    'Frente a plegadora',
]
TRANSPORT_DEFAULT_CARRIERS = ['Zubiaurre', 'Langarri']


def _dedupe_text_options(values):
    seen = set()
    cleaned = []
    for value in values or []:
        text = str(value or '').strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def load_transport_options():
    payload = {}
    if os.path.exists(TRANSPORT_OPTIONS_FILE):
        try:
            with open(TRANSPORT_OPTIONS_FILE, 'r', encoding='utf-8') as fh:
                payload = json.load(fh) or {}
        except Exception:
            payload = {}
    locations = _dedupe_text_options((payload or {}).get('locations'))
    carriers = _dedupe_text_options((payload or {}).get('carriers'))
    for default in TRANSPORT_DEFAULT_LOCATIONS:
        if default.casefold() not in {v.casefold() for v in locations}:
            locations.append(default)
    for default in TRANSPORT_DEFAULT_CARRIERS:
        if default.casefold() not in {v.casefold() for v in carriers}:
            carriers.append(default)
    return {'locations': locations, 'carriers': carriers}


def load_weekly_tracking_data():
    if not os.path.exists(WEEKLY_TRACKING_FILE):
        return {}
    try:
        with open(WEEKLY_TRACKING_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, value in data.items():
        pid = str(key or '').strip()
        if not pid or not isinstance(value, dict):
            continue
        today_tasks, previous_tasks, _changed = _normalize_weekly_tasks(value)
        cleaned[pid] = {
            'origin': str(value.get('origin') or '').strip(),
            'phase': str(value.get('phase') or '').strip(),
            'action': str(value.get('action') or ''),
            'today_tasks': today_tasks,
            'previous_tasks': previous_tasks,
        }
    return cleaned


def save_weekly_tracking_data(data):
    payload = {}
    for key, value in (data or {}).items():
        pid = str(key or '').strip()
        if not pid or not isinstance(value, dict):
            continue
        today_tasks = []
        for task in (value.get('today_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                today_tasks.append(normalized)
        previous_tasks = []
        for task in (value.get('previous_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                previous_tasks.append(normalized)
        payload[pid] = {
            'origin': str(value.get('origin') or '').strip(),
            'phase': str(value.get('phase') or '').strip(),
            'action': str(value.get('action') or ''),
            'today_tasks': today_tasks,
            'previous_tasks': previous_tasks,
        }
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = f"{WEEKLY_TRACKING_FILE}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, WEEKLY_TRACKING_FILE)


def save_transport_options(options):
    payload = {
        'locations': _dedupe_text_options((options or {}).get('locations')),
        'carriers': _dedupe_text_options((options or {}).get('carriers')),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TRANSPORT_OPTIONS_FILE, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _extract_transport_of_and_client(project):
    name = str((project or {}).get('name') or '').strip()
    of_match = re.search(r'\bOF\s*(\d{4}(?:-\d+)?)\b', name, re.IGNORECASE)
    if not of_match:
        of_match = re.search(r'\b(\d{4}(?:-\d+)?)\b', name)
    of_digits = of_match.group(1) if of_match else ''
    pieces = [chunk.strip() for chunk in name.split('-') if chunk and chunk.strip()]
    client = pieces[1] if len(pieces) >= 2 else str((project or {}).get('client') or '').strip()
    return of_digits, client


def _normalize_custom_field_label(label):
    return normalize_key(str(label or ''))


def _get_custom_field_text(card, *labels):
    if not isinstance(card, dict):
        return ''
    raw_fields = card.get('customFields') or card.get('customfields')
    fields = _coerce_custom_field_map(raw_fields)
    if not isinstance(fields, dict) or not fields:
        return ''
    wanted = {_normalize_custom_field_label(label) for label in labels if label}
    if not wanted:
        return ''
    for key, value in fields.items():
        if _normalize_custom_field_label(key) not in wanted:
            continue
        if isinstance(value, dict) and 'value' in value:
            value = value['value']
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _project_kanban_card(project):
    kanban_id = project.get('kanban_id')
    if not kanban_id:
        return {}
    return last_kanban_card(kanban_id) or {}


def _parse_analysis_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_kanban_date(text)
    if isinstance(parsed, date):
        return parsed
    return parse_input_date(text)


def _format_analysis_date(value):
    if not isinstance(value, date):
        return ''
    return value.strftime('%d/%m/%Y')


def load_planning_snapshots():
    if not os.path.exists(SNAPSHOT_FILE):
        return []
    try:
        with open(SNAPSHOT_FILE, 'r') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def save_planning_snapshots(entries):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SNAPSHOT_FILE, 'w') as f:
        json.dump(entries, f)


def load_planner_settings():
    if not os.path.exists(PLANNER_SETTINGS_FILE):
        return {'import_optional_phases': True}
    try:
        with open(PLANNER_SETTINGS_FILE, 'r') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {'import_optional_phases': True}
    if not isinstance(data, dict):
        return {'import_optional_phases': True}
    enabled = data.get('import_optional_phases')
    return {'import_optional_phases': bool(enabled) if enabled is not None else True}


def save_planner_settings(settings):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {'import_optional_phases': bool(settings.get('import_optional_phases', True))}
    with open(PLANNER_SETTINGS_FILE, 'w') as f:
        json.dump(payload, f)


def optional_phases_enabled():
    return bool(load_planner_settings().get('import_optional_phases', True))


def _project_lookup_map(projects):
    mapping = {}
    for p in projects:
        pid = str(p.get('id') or '').strip()
        if pid:
            mapping[pid] = p
    return mapping


def _phase_hours_from_project(project, phase):
    phases = project.get('phases') or {}
    value = phases.get(phase)
    return _phase_total_hours(value)


def _build_manual_snapshot_entries(manual_raw, project_map):
    entries = []
    for item in manual_raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get('pid') or '').strip()
        phase = item.get('phase')
        part = item.get('part')
        project = project_map.get(pid, {})
        entries.append(
            {
                'pid': pid,
                'project': project.get('name') or pid,
                'client': project.get('client', ''),
                'phase': phase,
                'part': part,
                'hours': _phase_hours_from_project(project, phase),
                'location': 'Arrastrar aquí',
            }
        )
    return entries


def _build_unplanned_entries(schedule):
    entries = []
    for day, tasks in schedule.items():
        for item in tasks:
            if not isinstance(item, dict):
                continue
            entries.append(
                {
                    'pid': item.get('pid'),
                    'project': item.get('project'),
                    'client': item.get('client'),
                    'phase': item.get('phase'),
                    'part': item.get('part'),
                    'hours': item.get('hours'),
                    'day': day,
                    'location': 'Sin planificar',
                }
            )
    return entries


def _build_order_positions(projects):
    today = local_today()
    compras_raw, column_colors = load_compras_raw()
    soldar_lookup = build_phase_finish_lookup(projects, SOLDAR_PHASE_PRIORITY)
    payload = compute_pedidos_entries(
        compras_raw,
        column_colors,
        today,
        soldar_finish_lookup=soldar_lookup,
    )
    positions = []
    for calendar_key, calendar_data in (
        ('pedidos', payload['pedidos']),
        ('subcontrataciones', payload['subcontrataciones']),
    ):
        for day, items in calendar_data['scheduled'].items():
            day_text = day.isoformat() if isinstance(day, date) else str(day)
            for entry in items:
                positions.append(
                    {
                        'calendar': calendar_key,
                        'column': entry.get('column', ''),
                        'lane': entry.get('lane', ''),
                        'cid': entry.get('cid'),
                        'project': entry.get('project'),
                        'cell': day_text,
                        'simulated': bool(entry.get('simulated')),
                    }
                )
        for entry in calendar_data['unconfirmed']:
            positions.append(
                {
                    'calendar': calendar_key,
                    'column': entry.get('column', ''),
                    'lane': entry.get('lane', ''),
                    'cid': entry.get('cid'),
                    'project': entry.get('project'),
                    'cell': 'Sin fecha confirmada',
                    'simulated': bool(entry.get('simulated')),
                }
            )
    return positions


def _flatten_snapshot_rows(snapshot):
    today = local_today()
    rows = []
    columnname_by_pid = snapshot.get('project_columnname_by_pid') or {}
    columnname_by_project = snapshot.get('project_columnname_by_project') or {}

    def resolve_columnname(item):
        if not isinstance(item, dict):
            return ''
        pid = str(item.get('pid') or '').strip()
        if pid:
            value = columnname_by_pid.get(pid, '')
            if value:
                return value
        project = str(item.get('project') or '').strip()
        if project:
            return columnname_by_project.get(project, '')
        return ''

    for item in snapshot.get('planned_phases', []):
        rows.append(
            {
                'tipo': 'Fase planificada',
                'proyecto': item.get('project'),
                'fase': item.get('phase'),
                'parte': item.get('part'),
                'fecha_o_celda': item.get('day'),
                'horas': item.get('hours'),
                'recurso_o_columna': item.get('worker'),
                'columnname': resolve_columnname(item),
                'estado': 'Planificada',
            }
        )
    for item in snapshot.get('manual_bucket', []):
        rows.append(
            {
                'tipo': 'Arrastrar aquí',
                'proyecto': item.get('project'),
                'fase': item.get('phase'),
                'parte': item.get('part'),
                'fecha_o_celda': item.get('location'),
                'horas': item.get('hours'),
                'recurso_o_columna': '',
                'columnname': resolve_columnname(item),
                'estado': 'En espera',
            }
        )
    for item in snapshot.get('unplanned', []):
        rows.append(
            {
                'tipo': 'Sin planificar',
                'proyecto': item.get('project'),
                'fase': item.get('phase'),
                'parte': item.get('part'),
                'fecha_o_celda': item.get('day'),
                'horas': item.get('hours'),
                'recurso_o_columna': '',
                'columnname': resolve_columnname(item),
                'estado': 'Sin recurso',
            }
        )
    for item in snapshot.get('project_status', []):
        rows.append(
            {
                'tipo': 'Estado proyecto',
                'proyecto': item.get('project'),
                'fase': '',
                'parte': '',
                'fecha_o_celda': '',
                'horas': '',
                'recurso_o_columna': '',
                'columnname': resolve_columnname(item),
                'estado': item.get('plan_state'),
            }
        )
    for key, entries in snapshot.get('phase_history', {}).items():
        pid, phase, part = (list(key.split('|')) + ['', '', ''])[:3]
        normalized_part = part if part not in (None, '', 'None') else ''
        for entry in entries:
            rows.append(
                {
                    'tipo': 'Historial fase',
                    'proyecto': pid,
                    'fase': phase,
                    'parte': normalized_part,
                    'fecha_o_celda': entry.get('timestamp'),
                    'horas': '',
                    'recurso_o_columna': f"{entry.get('from_worker', '')} → {entry.get('to_worker', '')}",
                    'columnname': columnname_by_pid.get(str(pid), ''),
                    'estado': f"{entry.get('from_day', '')} → {entry.get('to_day', '')}",
                }
            )
    for item in snapshot.get('order_positions', []):
        rows.append(
            {
                'tipo': 'Pedido',
                'proyecto': item.get('project'),
                'fase': '',
                'parte': '',
                'fecha_o_celda': item.get('cell'),
                'horas': '',
                'recurso_o_columna': item.get('column'),
                'columnname': resolve_columnname(item),
                'estado': item.get('calendar'),
            }
        )
    return rows


def build_planning_snapshot():
    include_optional_phases = optional_phases_enabled()
    projects = get_visible_projects(include_optional_phases=include_optional_phases)
    schedule, _, _, _ = build_schedule_with_archived(
        copy.deepcopy(projects), include_optional_phases=include_optional_phases
    )

    planned_entries = []
    unplanned_entries = []
    unplanned_schedule = schedule.get(UNPLANNED, {})
    if isinstance(unplanned_schedule, dict):
        unplanned_entries = _build_unplanned_entries(unplanned_schedule)

    for worker, days in schedule.items():
        if worker == UNPLANNED:
            continue
        for day, tasks in days.items():
            for item in tasks:
                if not isinstance(item, dict):
                    continue
                planned_entries.append(
                    {
                        'pid': item.get('pid'),
                        'project': item.get('project'),
                        'client': item.get('client'),
                        'phase': item.get('phase'),
                        'part': item.get('part'),
                        'hours': item.get('hours'),
                        'day': day,
                        'worker': worker,
                    }
                )

    project_map = _project_lookup_map(projects)
    manual_entries = _build_manual_snapshot_entries(
        load_manual_bucket_entries(), project_map
    )

    snapshot = {
        'timestamp': local_now().isoformat(),
        'planned_phases': planned_entries,
        'manual_bucket': manual_entries,
        'unplanned': unplanned_entries,
        'phase_history': load_phase_history(),
        'project_status': [
            {
                'pid': str(p.get('id') or ''),
                'project': p.get('name'),
                'client': p.get('client'),
                'plan_state': p.get('plan_state'),
                'planned': p.get('planned'),
            }
            for p in projects
        ],
        'order_positions': _build_order_positions(projects),
        'project_columnname_by_pid': {
            str(p.get('id')): str(p.get('kanban_column') or '').strip()
            for p in projects
            if p.get('id')
        },
        'project_columnname_by_project': {
            str(p.get('name') or '').strip(): str(p.get('kanban_column') or '').strip()
            for p in projects
            if p.get('name')
        },
    }
    snapshot['table_rows'] = _flatten_snapshot_rows(snapshot)
    return snapshot


def create_planning_snapshot():
    snapshot = build_planning_snapshot()
    entries = load_planning_snapshots()
    entries.append(snapshot)
    save_planning_snapshots(entries)
    return snapshot

if _load_worker_hours_func and _save_worker_hours_func:
    load_worker_hours = _load_worker_hours_func
    save_worker_hours = _save_worker_hours_func
else:
    _WORKER_HOURS_FILE = os.path.join(DATA_DIR, 'worker_hours.json')
    _WORKER_DEFAULT_LIMITS = {worker: limit for worker, limit in HOURS_LIMITS.items()}

    def _sanitize_worker_hours_payload(data):
        """Return a mapping of workers to hour overrides (1..12)."""

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

    def _sync_worker_defaults():
        for worker in WORKERS:
            if worker not in _WORKER_DEFAULT_LIMITS:
                limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
                if isinstance(limit, (int, float)):
                    _WORKER_DEFAULT_LIMITS[worker] = limit
                else:
                    _WORKER_DEFAULT_LIMITS[worker] = HOURS_PER_DAY
        for worker in list(_WORKER_DEFAULT_LIMITS):
            if worker not in WORKERS:
                _WORKER_DEFAULT_LIMITS.pop(worker, None)

    def _apply_worker_hour_overrides(overrides):
        _sync_worker_defaults()
        for worker, default in _WORKER_DEFAULT_LIMITS.items():
            HOURS_LIMITS[worker] = default
        for worker, hours in overrides.items():
            if worker in HOURS_LIMITS:
                HOURS_LIMITS[worker] = hours

    def load_worker_hours():
        overrides = {}
        if os.path.exists(_WORKER_HOURS_FILE):
            try:
                with open(_WORKER_HOURS_FILE, 'r') as fh:
                    raw = json.load(fh)
            except (json.JSONDecodeError, OSError):
                raw = {}
            overrides = _sanitize_worker_hours_payload(raw)
        _apply_worker_hour_overrides(overrides)
        return overrides

    def save_worker_hours(data):
        overrides = _sanitize_worker_hours_payload(data or {})
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_WORKER_HOURS_FILE, 'w') as fh:
            json.dump(overrides, fh)
        _apply_worker_hour_overrides(overrides)

    # Ensure in-memory limits reflect any persisted overrides.
    try:
        load_worker_hours()
    except Exception:
        pass

if _load_worker_day_hours_func and _save_worker_day_hours_func:
    load_worker_day_hours = _load_worker_day_hours_func
    save_worker_day_hours = _save_worker_day_hours_func
else:
    def load_worker_day_hours():
        return {}

    def save_worker_day_hours(data):
        return {}

KANBAN_POPUP_FIELDS = [
    'Fecha Cliente',
    'Fecha cliente',
    'LANZAMIENTO',
    'MATERIAL',
    'CALDERERIA',
    'PINTADO',
    'MECANIZADO',
    'TRATAMIENTO',
]

PROJECT_TITLE_PATTERN = re.compile(r'\bOF\s*\d{4}\b', re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r'[^0-9a-z]+')


def normalize_key(text):
    """Return a case-insensitive token stripped of diacritics and punctuation."""

    if not text:
        return ''

    normalized = unicodedata.normalize('NFKD', str(text))
    normalized = ''.join(
        ch for ch in normalized if not unicodedata.category(ch).startswith('M')
    )
    normalized = normalized.casefold()
    return _NON_ALNUM_RE.sub('', normalized)


MATERIAL_ALERT_COLUMNS = {
    normalize_key('Plegado/Curvado'),
    normalize_key('Comerciales varios'),
    normalize_key('Tubo/perfil/llanta/chapa'),
    normalize_key('Oxicorte'),
    normalize_key('Laser'),
    normalize_key('Plegado/Curvado - fabricación'),
    normalize_key('Material Incompleto'),
    normalize_key('Material NO CONFORME'),
    normalize_key('Premecanizado'),
    normalize_key('Planif. Premec.'),
}


def _upload_folder_path():
    """Return the absolute path to the uploads directory."""

    if os.path.isabs(UPLOAD_FOLDER):
        return UPLOAD_FOLDER
    return os.path.join(app.root_path, UPLOAD_FOLDER)


def _extract_upload_filename(image_value):
    """Return the file name for a stored project image, if local."""

    if not image_value:
        return None
    value = str(image_value).strip().replace('\\', '/').lstrip('/')
    if not value:
        return None
    if value.startswith('static/'):
        value = value[len('static/'):]
    if not value.startswith('uploads/'):
        return None
    basename = os.path.basename(value[len('uploads/'):])
    return basename or None


def _remove_upload_file(image_value):
    """Delete the file associated with *image_value* if it lives in uploads."""

    filename = _extract_upload_filename(image_value)
    if not filename:
        return False
    directory = _upload_folder_path()
    path = os.path.join(directory, filename)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        app.logger.warning('No se pudo eliminar la imagen %s', path, exc_info=True)
        return False


def prune_orphan_uploads(projects):
    """Remove files in the uploads folder that no project references."""

    directory = _upload_folder_path()
    if not os.path.isdir(directory):
        return
    referenced = set()
    for project in projects or []:
        filename = _extract_upload_filename(project.get('image'))
        if filename:
            referenced.add(filename)
    try:
        entries = os.listdir(directory)
    except OSError:
        app.logger.warning('No se pudo listar la carpeta de imágenes', exc_info=True)
        return
    for name in entries:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if name in referenced:
            continue
        try:
            os.remove(path)
        except OSError:
            app.logger.warning('No se pudo eliminar la imagen huérfana %s', path, exc_info=True)

READY_TO_ARCHIVE_COLUMN = normalize_key('Ready to Archive')
PENDING_VERIFICATION_COLUMN = normalize_key('Pdte. Verificación')
MATERIAL_VERIFY_ALLOWED_COLUMNS = {
    normalize_key('Material Recepcionado'),
    READY_TO_ARCHIVE_COLUMN,
    normalize_key('Plegado/Curvado'),
    normalize_key('Planf. TAU'),
    normalize_key('Planif. TAU'),
    normalize_key('Planf. Bekola'),
    normalize_key('Planif. Bekola'),
    normalize_key('Planf. AZ'),
    normalize_key('Planif. AZ'),
    normalize_key('Planf. OTROS'),
    normalize_key('Planif. OTROS'),
    normalize_key('Tratamiento'),
    normalize_key('Tratamiento final'),
}

KANBAN_COLUMN_PHASE_TARGETS = {
    normalize_key('Pedidos pendiente generar OF'): 'pedidos',
    normalize_key('Administración'): 'pedidos',
    normalize_key('Oficina Técnica'): 'dibujo',
    normalize_key('Pendiente Por Recepcionar'): 'preparar material',
    normalize_key('Prep. Interno'): 'preparar material',
    normalize_key('Prep. Externo'): 'preparar material',
    normalize_key('Listo para iniciar'): 'preparar material',
    normalize_key('Planificado para montaje'): 'preparar material',
    normalize_key('Montaje'): 'montar',
    normalize_key('Soldadura'): 'soldar',
    normalize_key('Montaje 2º fase'): 'montar 2º',
    normalize_key('Soldadura final'): 'soldar 2º',
    normalize_key('Verificacion'): 'soldar 2º',
    normalize_key('Enderezado'): 'soldar 2º',
    normalize_key('Tratamiento'): 'tratamiento',
    normalize_key('Mec. Interno'): 'mecanizar',
    normalize_key('Mec. Externo'): 'mecanizar',
    normalize_key('Pintura'): 'pintar',
    normalize_key('Montaje final'): '__after_pintar__',
    normalize_key('Hacer Albaran'): '__after_pintar__',
    READY_TO_ARCHIVE_COLUMN: '__after_pintar__',
}

KANBAN_PHASE_EXCLUSIONS = {'dibujo', 'pedidos'}
PHASE_INDEX = {phase: idx for idx, phase in enumerate(PHASE_ORDER)}


def phase_base(phase_name):
    if not isinstance(phase_name, str):
        return phase_name
    return phase_name.split('#', 1)[0]


def sort_phase_keys(phases):
    keys = list((phases or {}).keys())

    def sort_key(ph):
        base = phase_base(ph)
        base_idx = PHASE_INDEX.get(base, len(PHASE_ORDER))
        suffix = ph[len(base) :] if isinstance(ph, str) else ''
        return (base_idx, suffix)

    return sorted(keys, key=sort_key)


COMPLETE_PROJECT_PHASE_EXCLUSIONS = {'dibujo', 'pedidos', 'mecanizar', 'tratamiento'}


def _complete_project_phase_column_label(phase):
    if isinstance(phase, str) and '#' in phase:
        base, suffix = phase.split('#', 1)
        try:
            index = int(suffix)
        except Exception:
            return phase
        if index > 1:
            return f'{base} ({index})'
    return phase


def build_complete_project_phase_columns(projects):
    """Return phase columns for the Complete projects table, including split parts."""

    base_phase_names = [
        ph for ph in PHASE_ORDER if phase_base(ph) not in COMPLETE_PROJECT_PHASE_EXCLUSIONS
    ]
    base_phase_set = set(base_phase_names)
    extra_names = set()
    max_parts_by_phase = {}
    for project in projects or []:
        phases = project.get('phases') or {}
        for phase, value in phases.items():
            base = phase_base(phase)
            if base in COMPLETE_PROJECT_PHASE_EXCLUSIONS:
                continue
            if phase not in base_phase_set:
                extra_names.add(phase)
            if isinstance(value, list):
                max_parts_by_phase[phase] = max(max_parts_by_phase.get(phase, 0), len(value))

    sorted_extra_names = sort_phase_keys({name: None for name in extra_names})
    extras_by_base = {}
    for name in sorted_extra_names:
        extras_by_base.setdefault(phase_base(name), []).append(name)

    phase_names = []
    seen_phase_names = set()

    def add_phase_name(name):
        if name in seen_phase_names:
            return
        seen_phase_names.add(name)
        phase_names.append(name)

    for base_phase in base_phase_names:
        add_phase_name(base_phase)
        if base_phase in max_parts_by_phase:
            continue
        for extra_name in extras_by_base.get(base_phase, []):
            add_phase_name(extra_name)

    for extra_name in sorted_extra_names:
        base = phase_base(extra_name)
        if base in max_parts_by_phase:
            continue
        add_phase_name(extra_name)

    columns = []
    seen_columns = set()
    for phase in phase_names:
        max_parts = max_parts_by_phase.get(phase, 0)
        if max_parts > 1:
            for idx in range(max_parts):
                key = (phase, idx)
                if key in seen_columns:
                    continue
                seen_columns.add(key)
                columns.append({
                    'phase': phase,
                    'label': phase if idx == 0 else f'{phase} ({idx + 1})',
                    'part': idx,
                })
        else:
            key = (phase, None)
            if key in seen_columns:
                continue
            seen_columns.add(key)
            columns.append({
                'phase': phase,
                'label': _complete_project_phase_column_label(phase),
                'part': None,
            })
    return columns


def compute_previous_kanban_phases(column):
    """Return planner phases considered previous to the current Kanban column."""

    if not column:
        return []
    column_key = normalize_key(column)
    target = KANBAN_COLUMN_PHASE_TARGETS.get(column_key)
    if not target:
        return []
    if target == '__after_pintar__':
        cutoff = len(PHASE_ORDER)
    else:
        cutoff = PHASE_INDEX.get(target)
        if cutoff is None:
            return []
    previous = []
    for phase in PHASE_ORDER[:cutoff]:
        if phase in KANBAN_PHASE_EXCLUSIONS:
            continue
        previous.append(phase)
    return previous

SSE_CLIENTS = []


def compute_material_status_map(projects, *, include_missing_titles=False):
    """Return a mapping pid->material availability status for planning views.

    When ``include_missing_titles`` is ``True`` the function also returns a
    second dictionary mapping each project id to the list of missing material
    card titles detected in alert columns.
    """

    status_map = {}
    missing_titles_map = defaultdict(list) if include_missing_titles else None
    try:
        compras_raw, _ = load_compras_raw()
        raw_links = attach_phase_starts(build_project_links(compras_raw), projects)
        projects_with_children = set()
        columns_by_pid = {}
        for project in projects or []:
            pid = project.get('id')
            if pid:
                status_map[str(pid)] = 'complete'
        for entry in raw_links:
            pid = entry.get('pid')
            if not pid:
                continue
            pid_key = str(pid)
            details = entry.get('link_details') or []
            if details or entry.get('links'):
                projects_with_children.add(pid_key)
            if details:
                project_columns = columns_by_pid.setdefault(pid_key, set())
            if status_map.get(pid_key) == 'missing':
                continue
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                column = detail.get('column')
                column_key = normalize_key(column)
                if column_key:
                    project_columns.add(column_key)
                if (
                    column_key in MATERIAL_ALERT_COLUMNS
                    and column_key not in MATERIAL_VERIFY_ALLOWED_COLUMNS
                ):
                    status_map[pid_key] = 'missing'
                    if missing_titles_map is not None:
                        title = (detail.get('title') or '').strip()
                        if title and title not in missing_titles_map[pid_key]:
                            missing_titles_map[pid_key].append(title)
                    continue
        for pid_key, columns in columns_by_pid.items():
            if not columns:
                continue
            if status_map.get(pid_key) == 'missing':
                continue
            if PENDING_VERIFICATION_COLUMN in columns:
                other_columns = {
                    column for column in columns if column != PENDING_VERIFICATION_COLUMN
                }
                if all(
                    column in MATERIAL_VERIFY_ALLOWED_COLUMNS for column in other_columns
                ):
                    status_map[pid_key] = 'verify'
                    continue
            if all(column == READY_TO_ARCHIVE_COLUMN for column in columns):
                status_map[pid_key] = 'archived'
        for pid_key, current in list(status_map.items()):
            if pid_key not in projects_with_children and current != 'missing':
                status_map[pid_key] = 'pending'
    except Exception:  # pragma: no cover - defensive logging
        app.logger.exception('Failed to compute material status from project links')
    for project in projects or []:
        pid = project.get('id')
        if not pid:
            continue
        tags = project.get('kanban_tags')
        if not tags:
            continue
        if isinstance(tags, str):
            tag_values = [tags]
        else:
            tag_values = list(tags)
        normalized_tags = set()
        for tag in tag_values:
            normalized = _normalize_tag_value(tag)
            if normalized:
                normalized_tags.add(normalized)
        if 'sin pedidos' in normalized_tags:
            status_map[str(pid)] = 'complete'
    if missing_titles_map is not None:
        for pid_key, status in status_map.items():
            if status == 'missing':
                missing_titles_map.setdefault(pid_key, [])
        return status_map, {k: list(v) for k, v in missing_titles_map.items()}
    return status_map


def material_status_label(status):
    text = MATERIAL_STATUS_LABELS.get(status)
    if text:
        return text
    if not status:
        return 'Sin estado'
    cleaned = str(status).replace('_', ' ').strip()
    return cleaned.title() or 'Sin estado'


def group_unplanned_by_status(unplanned_list, status_map):
    buckets = defaultdict(list)
    for entry in unplanned_list:
        pid = entry.get('pid')
        status = 'complete'
        if pid:
            status = status_map.get(str(pid), 'complete')
        entry['material_status'] = status
        buckets[status].append(entry)
    groups = []
    for status in MATERIAL_STATUS_ORDER:
        items = buckets.pop(status, [])
        if items:
            groups.append(
                {
                    'status': status,
                    'label': material_status_label(status),
                    'css_class': f"material-status-{status}",
                    'projects': items,
                }
            )
    for status in sorted(buckets.keys()):
        items = buckets[status]
        if not items:
            continue
        groups.append(
            {
                'status': status,
                'label': material_status_label(status),
                'css_class': f"material-status-{status}",
                'projects': items,
            }
        )
    return groups


def group_unplanned_by_phase(unplanned_due):
    phase_groups = defaultdict(list)
    for project_entry in unplanned_due or []:
        project_name = project_entry.get('project') or ''
        client_name = project_entry.get('client') or ''
        pid = project_entry.get('pid')
        due_date = project_entry.get('due_date') or ''
        material_status = project_entry.get('material_status', 'complete')
        for task in project_entry.get('tasks') or []:
            phase_name = str(task.get('phase') or '').strip()
            if not phase_name:
                continue
            phase_groups[phase_name].append(
                {
                    **task,
                    'pid': pid,
                    'project': project_name,
                    'client': client_name,
                    'due_date': due_date,
                    'material_status': material_status,
                    'material_label': material_status_label(material_status),
                    'material_css': f"material-status-{material_status}",
                }
            )

    def _phase_sort_key(name):
        base = phase_base(name)
        idx = PHASE_INDEX.get(base, len(PHASE_ORDER) + 100)
        return (idx, name.lower())

    groups = []
    for phase_name in sorted(phase_groups.keys(), key=_phase_sort_key):
        groups.append({'phase': phase_name, 'tasks': phase_groups[phase_name]})
    return groups


def _manual_entry_key(pid, phase, part):
    if pid in (None, '', 'None') or phase in (None, '', 'None'):
        return None
    pid_str = str(pid)
    phase_str = str(phase)
    part_val = None
    if part not in (None, '', 'None'):
        try:
            part_val = int(part)
        except Exception:
            return None
    return pid_str, phase_str, part_val


VERIFY_PHASE_REMOVAL_COLUMN_KEYS = {
    normalize_key('Soldadura final'),
    normalize_key('Verificación final'),
    normalize_key('Enderezado'),
    normalize_key('Tratamiento'),
    normalize_key('Mec. Interno'),
    normalize_key('Mec. Externo'),
    normalize_key('Pintura'),
    normalize_key('Montaje final'),
    normalize_key('Embalaje'),
    normalize_key('Revisar Conformidad técnica'),
    normalize_key('Hacer Albaran'),
}


def _manual_entry_dict(key):
    pid, phase, part = key
    entry = {'pid': pid, 'phase': phase}
    if part is not None:
        entry['part'] = part
    return entry


def load_manual_bucket_entries():
    entries = load_manual_unplanned()
    cleaned = []
    seen = set()
    for item in entries:
        key = _manual_entry_key(item.get('pid'), item.get('phase'), item.get('part'))
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(_manual_entry_dict(key))
    if cleaned != entries:
        save_manual_unplanned(cleaned)
    return cleaned


def manual_bucket_add(pid, phase, part, position=None):
    key = _manual_entry_key(pid, phase, part)
    if not key:
        return
    entries = load_manual_bucket_entries()
    filtered = [e for e in entries if (e['pid'], e['phase'], e.get('part')) != key]
    entry = _manual_entry_dict(key)
    if position is None:
        position = len(filtered)
    try:
        position = int(position)
    except Exception:
        position = len(filtered)
    if position < 0:
        position = 0
    if position > len(filtered):
        position = len(filtered)
    filtered.insert(position, entry)
    if filtered != entries:
        save_manual_unplanned(filtered)


def manual_bucket_remove(pid, phase, part):
    key = _manual_entry_key(pid, phase, part)
    if not key:
        return
    entries = load_manual_bucket_entries()
    filtered = [e for e in entries if (e['pid'], e['phase'], e.get('part')) != key]
    if filtered != entries:
        save_manual_unplanned(filtered)


def manual_bucket_reorder(order):
    if not isinstance(order, list):
        return False
    cleaned = []
    seen = set()
    for item in order:
        if isinstance(item, dict):
            key = _manual_entry_key(item.get('pid'), item.get('phase'), item.get('part'))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            key = _manual_entry_key(item[0], item[1], item[2] if len(item) > 2 else None)
        else:
            key = None
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(_manual_entry_dict(key))
    save_manual_unplanned(cleaned)
    return True


def get_card_custom_id(card):
    """Return the Kanban custom card identifier (e.g. ``OF 1234``) if present."""

    if not isinstance(card, dict):
        return ''

    for key in (
        'customCardId',
        'customcardid',
        'customId',
        'customid',
    ):
        value = card.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return ''


def broadcast_event(data):
    for q in list(SSE_CLIENTS):
        q.put(data)

@app.route('/events')
def event_stream():
    def gen():
        q = Queue()
        SSE_CLIENTS.append(q)
        try:
            while True:
                data = q.get()
                yield f"data: {json.dumps(data)}\n\n"
        except GeneratorExit:
            SSE_CLIENTS.remove(q)
    return Response(gen(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache'})



@app.route('/complete_last_movement')
def complete_last_movement():
    try:
        latest = os.path.getmtime(_schedule_mod.PHASE_HISTORY_FILE)
    except FileNotFoundError:
        latest = 0
    return jsonify({'timestamp': latest})


@app.route('/complete_last_update')
def complete_last_update():
    files = [
        _schedule_mod.PROJECTS_FILE,
        _schedule_mod.NOTES_FILE,
        _schedule_mod.WORKER_NOTES_FILE,
        _schedule_mod.EXTRA_CONFLICTS_FILE,
        _schedule_mod.VACATIONS_FILE,
        _schedule_mod.DAILY_HOURS_FILE,
        _schedule_mod.WORKER_DAY_HOURS_FILE,
        _schedule_mod.MANUAL_UNPLANNED_FILE,
        _schedule_mod.PHASE_HISTORY_FILE,
    ]
    latest = 0
    for path in files:
        try:
            latest = max(latest, os.path.getmtime(path))
        except FileNotFoundError:
            continue
    return jsonify({'timestamp': latest})


def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_tracker(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_move_reason(projects, pid, phase, part, mode, info):
    """Return a detailed explanation of how a phase was distributed."""
    schedule, _ = schedule_projects(projects)
    segments = []
    for worker, days in schedule.items():
        for day, tasks in days.items():
            for t in tasks:
                if t.get('pid') == pid and t['phase'] == phase and (
                    part is None or t.get('part') == part
                ):
                    segments.append((worker, day, t))
    segments.sort(key=lambda x: (x[1], x[2].get('start', 0)))
    explanations = []
    for idx, (worker, day, t) in enumerate(segments):
        start = t.get('start', 0)
        hours = t['hours']
        day_fmt = date.fromisoformat(day).strftime('%d/%m/%Y')
        day_tasks = sorted(schedule[worker][day], key=lambda x: x.get('start', 0))
        before = [
            x
            for x in day_tasks
            if x is not t and x['start'] + x['hours'] <= start
        ]
        if before:
            prev = ', '.join(
                f"{b['project']} - {b['phase']} ({b['hours']}h)" for b in before
            )
            msg = f"{day_fmt}: {hours}h tras {start}h ocupadas por {prev}"
        else:
            msg = f"{day_fmt}: {hours}h al inicio de la jornada"
        limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
        end = start + hours
        if idx < len(segments) - 1:
            remaining = limit - end
            if remaining > 0:
                msg += f"; quedaban {remaining}h libres y se continuó al día siguiente"
            else:
                msg += "; jornada completa, se continuó al día siguiente"
        explanations.append(msg)
    if mode == 'push' and info.get('affected'):
        explanations.append(
            'Se desplazaron fases posteriores para mantener la continuidad'
        )
    return '\n'.join(explanations)

# Kanbanize integration constants
KANBANIZE_BASE_URL = 'https://caldereriacpk.kanbanize.com'
KANBANIZE_BOARD_TOKEN = os.environ.get('KANBANIZE_BOARD_TOKEN', '682d829a0aafe44469o50acd')
KANBANIZE_API_KEY = "jpQfMzS8AzdyD70zLkilBjP0Uig957mOATuM0BOE"

# Lanes from Kanbanize that the webhook listens to for project events.
ARCHIVE_LANES = {'Acero al Carbono', 'Inoxidable - Aluminio'}

# Column and lane filters for the orders calendar and associated tables
PEDIDOS_ALLOWED_COLUMNS = {
    'Comerciales varios',
    'Tubo/perfil/llanta/chapa',
    'Oxicorte',
    'Laser',
    'Plegado/Curvado',
    'Plegado/curvado - Fabricación',
    'Material Incompleto',
    'Material NO CONFORME',
    'Premecanizado',
    'Planif. Premec.',
    'Listo para iniciar',
}

PEDIDOS_HIDDEN_COLUMNS = [
    "Ready to Archive",
    "Material recepcionado",
    "Pdte. Verificación",
]

PENDING_VERIFICATION_COLUMN_KEYS = {
    normalize_key('Pdte. Verificación'),
}

PEDIDOS_UNCONFIRMED_COLUMNS = set()

SUBCONTRATACIONES_BLUE_COLUMNS = {
    'Planf. TAU',
    'Planif. Bekola',
    'Planif. AZ',
    'Planif. OTROS',
    'Tratamiento',
}

SUBCONTRATACIONES_ORANGE_COLUMNS = {
    'Tau',
    'Bekola',
    'AZ',
    'OTROS',
    'Tratamiento final',
}

SUBCONTRATACIONES_ALLOWED_COLUMNS = (
    SUBCONTRATACIONES_BLUE_COLUMNS | SUBCONTRATACIONES_ORANGE_COLUMNS
)

SUBCONTRATACIONES_UNCONFIRMED_COLUMNS = {
    'Tau',
    'Bekola',
    'AZ',
    'OTROS',
    'Tratamiento',
    'Planf. TAU',
    'Planif. OTROS',
}

SUBCONTRATACIONES_TREATMENT_KEYS = {
    normalize_key('Tratamiento'),
    normalize_key('Tratamiento final'),
}

PEDIDOS_EXTRA_LANE_COLUMNS = {
    'Planif. Premec.',
    'Premecanizado',
}

PEDIDOS_OFFSET_TO_PLAN_END_COLUMNS = {
    'Tratamiento',
    'Tratamiento final',
    'Planf. TAU',
    'Planif. Bekola',
    'Planif. AZ',
    'Planif. OTROS',
    'Tau',
    'Bekola',
    'AZ',
    'OTROS',
}

PROJECT_LINK_LANES = {
    'acero al carbono',
    'inoxidable - aluminio',
    'seguimiento compras',
}

COLUMN1_ALLOWED_LANE_KEYS = {
    normalize_key('Acero al Carbono'),
    normalize_key('Inoxidable - Aluminio'),
}

PEDIDOS_ALLOWED_KEYS = {normalize_key(col) for col in PEDIDOS_ALLOWED_COLUMNS}
PEDIDOS_UNCONFIRMED_KEYS = {normalize_key(col) for col in PEDIDOS_UNCONFIRMED_COLUMNS}
PEDIDOS_HIDDEN_KEYS = {normalize_key(col) for col in PEDIDOS_HIDDEN_COLUMNS}
PEDIDOS_OFFSET_TO_PLAN_END_KEYS = {
    normalize_key(col) for col in PEDIDOS_OFFSET_TO_PLAN_END_COLUMNS
}
PEDIDOS_SEGUIMIENTO_LANE_KEY = normalize_key('Seguimiento compras')

SUBCONTRATACIONES_ALLOWED_KEYS = {
    normalize_key(col) for col in SUBCONTRATACIONES_ALLOWED_COLUMNS
}
SUBCONTRATACIONES_UNCONFIRMED_KEYS = {
    normalize_key(col) for col in SUBCONTRATACIONES_UNCONFIRMED_COLUMNS
}

SUBCONTRATACIONES_BLUE_COLOR = '#1f6bff'
SUBCONTRATACIONES_ORANGE_COLOR = '#ff8b3d'
SUBCONTRATACIONES_COLOR_OVERRIDES = {
    normalize_key(col): SUBCONTRATACIONES_BLUE_COLOR
    for col in SUBCONTRATACIONES_BLUE_COLUMNS
}
SUBCONTRATACIONES_COLOR_OVERRIDES.update(
    {
        normalize_key(col): SUBCONTRATACIONES_ORANGE_COLOR
        for col in SUBCONTRATACIONES_ORANGE_COLUMNS
    }
)

SOLDAR_PHASE_PRIORITY = (
    'soldar',
    'soldar 2º',
)

CALENDAR_MONTH_NAMES = [
    'Enero',
    'Febrero',
    'Marzo',
    'Abril',
    'Mayo',
    'Junio',
    'Julio',
    'Agosto',
    'Septiembre',
    'Octubre',
    'Noviembre',
    'Diciembre',
]

CALENDAR_CONFIGS = {
    'pedidos': {
        'allowed_keys': PEDIDOS_ALLOWED_KEYS,
        'unconfirmed_keys': PEDIDOS_UNCONFIRMED_KEYS,
        'color_overrides': {},
    },
    'subcontrataciones': {
        'allowed_keys': SUBCONTRATACIONES_ALLOWED_KEYS,
        'unconfirmed_keys': SUBCONTRATACIONES_UNCONFIRMED_KEYS,
        'color_overrides': SUBCONTRATACIONES_COLOR_OVERRIDES,
    },
}

MATERIAL_EXCLUDED_COLUMNS = {
    normalize_key('Ready to Archive'),
    normalize_key('Ready to archieve'),
    normalize_key('Material Recepcionado'),
    normalize_key('Material recepcionado'),
    normalize_key('Pdte. Verificación'),
    normalize_key('Tratamiento'),
    normalize_key('Tratamiento final'),
}


def active_workers(today=None):
    """Return the list of workers shown in the calendar."""
    if today is None:
        today = local_today()
    workers = [w for w in WORKERS.keys() if w != UNPLANNED]
    if today >= IGOR_END and 'Igor' in workers:
        workers.remove('Igor')
    inactive = set(load_inactive_workers())
    return [w for w in workers if w not in inactive]


def planner_calendar_workers(today=None):
    """Return planner rows, hiding deactivated resources once their date arrives."""
    if today is None:
        today = local_today()
    workers = [w for w in WORKERS.keys() if w != UNPLANNED]
    if today >= IGOR_END and 'Igor' in workers:
        workers.remove('Igor')
    inactive = set(load_inactive_workers())
    inactive_dates = load_inactive_worker_dates()
    visible = []
    for worker in workers:
        if worker not in inactive:
            visible.append(worker)
            continue
        try:
            effective_day = date.fromisoformat(
                inactive_dates.get(worker, today.isoformat())
            )
        except (TypeError, ValueError):
            effective_day = today
        if today < effective_day:
            visible.append(worker)
    return visible


def _inactive_worker_date_map():
    inactive = set(load_inactive_workers())
    stored = load_inactive_worker_dates()
    today_text = local_today().isoformat()
    return {worker: stored.get(worker, today_text) for worker in inactive if worker in WORKERS}


def _is_worker_deactivated_on_day(worker, day):
    if not worker or worker == UNPLANNED or worker not in set(load_inactive_workers()):
        return False
    inactive_dates = load_inactive_worker_dates()
    try:
        effective_day = date.fromisoformat(
            inactive_dates.get(worker, local_today().isoformat())
        )
    except (TypeError, ValueError):
        effective_day = local_today()
    return day >= effective_day


def _deactivate_worker_from_date(worker, effective_date):
    if not worker or worker == UNPLANNED or worker not in WORKERS:
        return False
    inactive = load_inactive_workers()
    if worker not in inactive:
        inactive.append(worker)
        save_inactive_workers(inactive)
    inactive_dates = load_inactive_worker_dates()
    inactive_dates[worker] = effective_date.isoformat()
    save_inactive_worker_dates(inactive_dates)
    projects = get_projects()
    moved = _move_worker_future_phases_to_manual_bucket(
        projects, {worker}, today=effective_date
    )
    if moved:
        save_projects(projects)
    return True


def _schedule_with_deactivated_rows(schedule, calendar_workers, deactivated_workers):
    """Keep deactivated resource rows visible while preserving work before the effective day."""

    rows = {}
    source = schedule or {}
    deactivated_workers = deactivated_workers or {}
    for worker in calendar_workers:
        worker_days = source.get(worker, {}) or {}
        effective_text = deactivated_workers.get(worker)
        if not effective_text:
            rows[worker] = worker_days
            continue
        try:
            effective_day = date.fromisoformat(effective_text)
        except (TypeError, ValueError):
            rows[worker] = {}
            continue
        kept_days = {}
        for day_text, tasks in worker_days.items():
            try:
                planned_day = date.fromisoformat(day_text)
            except (TypeError, ValueError):
                continue
            if planned_day < effective_day:
                kept_days[day_text] = tasks
        rows[worker] = kept_days
    return rows


def _build_taller_days(start_day, count=3):
    if count < 1:
        return []
    day = start_day
    if day.weekday() in WEEKEND:
        day = next_workday(day)
    days = []
    while len(days) < count:
        if day.weekday() not in WEEKEND:
            days.append(day)
        day = next_workday(day)
    return days


def parse_input_date(value):
    """Parse a date string that may omit the year.

    Accepts formats like 'dd-mm', 'dd/mm' or full ISO 'YYYY-MM-DD'.
    If the year is missing, the current year is used.
    Returns a ``date`` instance.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        parts = value.replace('/', '-').split('-')
        if len(parts) >= 2:
            try:
                day = int(parts[0])
                month = int(parts[1])
            except ValueError:
                return None
            year = local_today().year
            if len(parts) == 3:
                try:
                    year = int(parts[2])
                except ValueError:
                    pass
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def parse_kanban_date(value):
    """Parse Kanbanize date strings which may include time."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return parse_input_date(value)


def _coerce_custom_field_map(raw):
    """Return a dict mapping custom-field names to values."""

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, list):
        fields = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get('name')
            if not name:
                continue
            value = entry.get('value')
            if isinstance(value, dict) and 'value' in value:
                value = value['value']
            fields[name] = value
        return fields

    return {}


def _resolve_order_custom_field(card):
    """Extract the "Fecha pedido" value (parsed date and raw text) from *card*."""

    if not isinstance(card, dict):
        return None, None

    raw_fields = card.get('customFields') or card.get('customfields')
    fields = _coerce_custom_field_map(raw_fields)
    if not fields:
        return None, None

    for key, value in fields.items():
        if not isinstance(key, str):
            continue
        if key.strip().lower() != 'fecha pedido':
            continue
        if isinstance(value, dict) and 'value' in value:
            value = value['value']
        if value in (None, ''):
            continue
        text = str(value).strip()
        if not text:
            continue
        parsed = parse_kanban_date(text)
        return parsed, text

    return None, None


def _sort_cell_tasks(schedule):
    """Sort tasks in each day so started phases appear before unstarted ones."""
    for days in schedule.values():
        for tasks in days.values():
            tasks.sort(key=lambda t: (not t.get('start_date'), t.get('start', 0)))


def format_dd_mm(value):
    """Return 'dd-mm' string for a date or date string."""
    if not value:
        return ''
    if isinstance(value, str):
        value = parse_input_date(value)
    if isinstance(value, date):
        return f"{value.day:02d}-{value.month:02d}"
    return ''


def load_kanban_cards():
    """Return stored Kanban cards, skipping malformed entries."""
    if os.path.exists(KANBAN_CARDS_FILE):
        with open(KANBAN_CARDS_FILE, 'r') as f:
            try:
                data = json.load(f)
            except Exception:
                return []
        # Ensure we always return a list of dictionaries
        return [c for c in data if isinstance(c, dict)]
    return []


def save_kanban_cards(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = f"{KANBAN_CARDS_FILE}.tmp"
    with KANBAN_CARDS_LOCK:
        with open(tmp_path, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, KANBAN_CARDS_FILE)


def _normalize_tag_value(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None
    text = re.sub(r'\s+', ' ', value).strip()
    if not text:
        return None
    decomposed = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in decomposed if unicodedata.category(ch) != 'Mn')
    return text.lower()


def _extract_card_tags(card):
    tags = set()
    if not isinstance(card, dict):
        return tags
    for key in (
        'tags',
        'Tags',
        'tag',
        'Tag',
        'tagNames',
        'tag_names',
        'tagList',
        'tag_list',
    ):
        raw = card.get(key)
        if not raw:
            continue
        if isinstance(raw, str):
            parts = re.split(r'[;,]', raw)
            for part in parts:
                normalized = _normalize_tag_value(part)
                if normalized:
                    tags.add(normalized)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    candidate = (
                        item.get('name')
                        or item.get('tag')
                        or item.get('text')
                        or item.get('value')
                    )
                else:
                    candidate = item
                normalized = _normalize_tag_value(candidate)
                if normalized:
                    tags.add(normalized)
        else:
            normalized = _normalize_tag_value(raw)
            if normalized:
                tags.add(normalized)
    return tags


def last_kanban_card(cid):
    """Return the most recent stored Kanban card with the given *cid*.

    If no prior card is found, an empty dict is returned.  This helper lets the
    webhook determine which fields actually changed in Kanbanize so that the
    planner only updates those specific fields.
    """
    cid = str(cid)
    for entry in reversed(load_kanban_cards()):
        card = entry.get('card') or {}
        old_cid = card.get('taskid') or card.get('cardId') or card.get('id')
        if str(old_cid) == cid:
            return card
    return {}


def latest_kanban_card_meta():
    """Return the latest lane/column/archive metadata keyed by Kanban card id."""
    latest = {}
    for entry in reversed(load_kanban_cards()):
        if not isinstance(entry, dict):
            continue
        card = entry.get('card') or {}
        if not isinstance(card, dict):
            continue
        cid = card.get('taskid') or card.get('cardId') or card.get('id')
        cid_key = str(cid or '').strip()
        if not cid_key or cid_key in latest:
            continue
        column = (card.get('columnname') or card.get('columnName') or card.get('column') or '').strip()
        if not column:
            column = str(entry.get('last_column') or '').strip()
        lane = (card.get('lanename') or card.get('laneName') or card.get('lane') or '').strip()
        is_archived = False
        for key in ('isArchived', 'IsArchived', 'archived', 'Archived', 'is_archived'):
            if key in card:
                is_archived = bool(card.get(key))
                break
        latest[cid_key] = {
            'column': column,
            'lane': lane,
            'is_archived': is_archived,
        }
    return latest


def _collect_compras_entries(entries, allowed_lanes, column_colors):
    compras_raw = {}
    updated_colors = False
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        card = entry.get('card') or {}
        if not isinstance(card, dict):
            continue
        lane_name = (card.get('lanename') or card.get('laneName') or '').strip()
        if lane_name.lower() not in allowed_lanes:
            continue
        column = (card.get('columnname') or card.get('columnName') or '').strip()
        if not column:
            cached_column = entry.get('last_column')
            if isinstance(cached_column, str):
                column = cached_column.strip()
            elif cached_column:
                column = str(cached_column).strip()
            if column:
                # Make a shallow copy so downstream consumers (and future
                # saves) can rely on the normalized column value.
                card = dict(card)
                card.setdefault('columnname', column)
                card.setdefault('columnName', column)
        cid = card.get('taskid') or card.get('cardId') or card.get('id')
        if not cid:
            continue
        compras_raw[cid] = {
            'card': card,
            'stored_date': entry.get('stored_title_date'),
            'prev_date': entry.get('previous_title_date'),
        }
        if column and column not in column_colors:
            column_colors[column] = _next_api_color()
            updated_colors = True
    return compras_raw, column_colors, updated_colors


def load_compras_raw():
    allowed_lanes = {lane.lower() for lane in PROJECT_LINK_LANES}
    column_colors = load_column_colors()

    entries = load_kanban_cards()
    compras_raw, column_colors, updated_colors = _collect_compras_entries(
        entries, allowed_lanes, column_colors
    )

    if not compras_raw:
        refreshed = refresh_kanban_card_cache()
        if refreshed:
            column_colors = load_column_colors()
            compras_raw, column_colors, updated_colors = _collect_compras_entries(
                refreshed, allowed_lanes, column_colors
            )

    if updated_colors:
        save_column_colors(column_colors)

    return compras_raw, column_colors


def split_project_and_client(title):
    title = (title or '').strip()
    if not title:
        return '', ''

    match = PROJECT_TITLE_PATTERN.search(title)
    if not match:
        return title, ''

    start = match.start()
    end = match.end()
    tail = title[end:]
    separator = re.search(r'\s+-\s+', tail)
    if separator:
        end += separator.start()
    else:
        end = len(title)

    project = title[start:end].strip(' -')
    before = title[:start].strip(' -')
    after = title[end:].strip(' -')

    client_parts = [part for part in (before, after) if part]
    client = ' - '.join(client_parts)

    if not project:
        project = title

    return project, client


def build_project_links(compras_raw):
    children_by_parent = {}
    children_norms_by_parent = {}
    children_ids_by_parent = {}
    seguimiento_titles = {}
    seguimiento_by_id = {}
    candidate_cards = []
    excluded_columns = {
        normalize_key(col) for col in PEDIDOS_HIDDEN_COLUMNS
        if normalize_key(col) not in PENDING_VERIFICATION_COLUMN_KEYS
    }
    target_lanes = {normalize_key(lane) for lane in PROJECT_LINK_LANES}
    seguimiento_lane = normalize_key('Seguimiento compras')
    seen_candidates = set()

    card_index = {}
    def _attach_child_card_metadata(serialized, card_id):
        """Populate ``serialized`` with lane/column info from cached Kanban cards."""

        if not isinstance(serialized, dict):
            return serialized

        key = str(card_id).strip() if card_id not in (None, '') else ''
        if not key:
            return serialized

        card = card_index.get(key)
        if not isinstance(card, dict):
            return serialized

        if not serialized.get('column'):
            column = (card.get('columnname') or card.get('columnName') or '').strip()
            if column:
                serialized['column'] = column

        if not serialized.get('lane'):
            lane = (card.get('lanename') or card.get('laneName') or '').strip()
            if lane:
                serialized['lane'] = lane

        if 'order_date' not in serialized or 'order_date_raw' not in serialized:
            order_date_obj, order_raw = _resolve_order_custom_field(card)
            if order_raw and not serialized.get('order_date_raw'):
                serialized['order_date_raw'] = order_raw
            if order_date_obj and not serialized.get('order_date'):
                serialized['order_date'] = order_date_obj.isoformat()

        return serialized

    for data in compras_raw.values():
        card = data['card']
        if not isinstance(card, dict):
            continue
        card_id = card.get('taskid') or card.get('cardId') or card.get('id')
        if card_id:
            card_index[str(card_id)] = card

    for data in compras_raw.values():
        card = data['card']
        if not isinstance(card, dict):
            continue
        title = (card.get('title') or '').strip()
        description = (card.get('description') or '').strip()
        custom_id = get_card_custom_id(card)
        cid_value = card.get('taskid') or card.get('cardId') or card.get('id')
        cid = str(cid_value) if cid_value else None
        links_info = card.get('links') or {}

        parents = []
        children = []
        if isinstance(links_info, dict):
            parent_entries = links_info.get('parent')
            if isinstance(parent_entries, list):
                parents.extend(parent_entries)
            parent_entries = links_info.get('parents')
            if isinstance(parent_entries, list):
                parents.extend(parent_entries)

            child_entries = links_info.get('child')
            if isinstance(child_entries, list):
                children.extend(child_entries)
            child_entries = links_info.get('children')
            if isinstance(child_entries, list):
                children.extend(child_entries)

        normalized_child_title = normalize_key(title)
        if parents and normalized_child_title:
            for p in parents:
                if not isinstance(p, dict):
                    continue
                pid = p.get('taskid') or p.get('cardId') or p.get('id')
                if not pid:
                    continue
                key = str(pid)
                lst = children_by_parent.setdefault(key, [])
                norms = children_norms_by_parent.setdefault(key, set())
                ids = children_ids_by_parent.setdefault(key, set())
                if cid:
                    if cid in ids:
                        continue
                    ids.add(cid)
                elif normalized_child_title in norms:
                    continue
                lst.append((cid, title))
                norms.add(normalized_child_title)

        if children and cid:
            for ch in children:
                if isinstance(ch, dict):
                    child_id = ch.get('taskid') or ch.get('cardId') or ch.get('id')
                    child_title = (ch.get('title') or '').strip()
                    if child_id:
                        cached_card = card_index.get(str(child_id))
                        if cached_card:
                            canonical = (cached_card.get('title') or '').strip()
                            if canonical:
                                child_title = canonical
                    if child_id and not child_title:
                        fetched = _fetch_kanban_card(child_id)
                        if fetched:
                            child_title = (fetched.get('title') or '').strip()
                    norm_child = normalize_key(child_title)
                    child_key = str(child_id) if child_id else None
                    if (child_key or norm_child) and cid:
                        lst = children_by_parent.setdefault(cid, [])
                        norms = children_norms_by_parent.setdefault(cid, set())
                        ids = children_ids_by_parent.setdefault(cid, set())
                        if child_key:
                            if child_key in ids:
                                continue
                            ids.add(child_key)
                        elif not norm_child or norm_child in norms:
                            continue
                        lst.append((child_key, child_title))
                        if norm_child:
                            norms.add(norm_child)

        lane_name = (card.get('lanename') or card.get('laneName') or '').strip()
        board_name = (card.get('boardName') or card.get('boardname') or '').strip()
        lane_key = normalize_key(lane_name)
        column = (card.get('columnname') or card.get('columnName') or '').strip()
        column_key = normalize_key(column)

        if (
            title
            and normalized_child_title
            and lane_key == seguimiento_lane
            and column_key not in excluded_columns
        ):
            deadline = parse_kanban_date(card.get('deadline'))
            detail = {
                'id': cid or '',
                'title': title,
                'column': column,
                'deadline': deadline.isoformat() if deadline else None,
                'lane': lane_name,
            }
            order_date_obj, order_raw = _resolve_order_custom_field(card)
            if order_raw:
                detail['order_date_raw'] = order_raw
            if order_date_obj:
                detail['order_date'] = order_date_obj.isoformat()
            if detail['id']:
                seguimiento_by_id[detail['id']] = detail
            titles = seguimiento_titles.setdefault(normalized_child_title, [])
            if detail['id']:
                if not any(existing.get('id') == detail['id'] for existing in titles):
                    titles.append(detail)
            else:
                if not any(existing.get('title') == title for existing in titles):
                    titles.append(detail)

        if cid and lane_key in target_lanes and cid not in seen_candidates:
            project_name, client_name = split_project_and_client(title)
            due = parse_kanban_date(card.get('deadline'))
            client_delivery_raw = _get_custom_field_text(card, 'Fecha Cliente', 'Fecha cliente')
            client_delivery_date_obj = parse_kanban_date(client_delivery_raw)
            project_order_date_obj, project_order_raw = _resolve_order_custom_field(card)
            base_display = title or project_name or ''
            if custom_id and base_display:
                if base_display.lower().startswith(custom_id.lower()):
                    display_title = base_display
                else:
                    display_title = f"{custom_id} - {base_display}"
            else:
                display_title = base_display or custom_id
            candidate_cards.append({
                'cid': cid,
                'project': project_name,
                'title': title,
                'display_title': display_title,
                'client': client_name,
                'custom_card_id': custom_id,
                'due': due,
                'client_delivery_date': client_delivery_date_obj.isoformat() if client_delivery_date_obj else None,
                'client_delivery_date_raw': client_delivery_raw,
                'project_order_date': project_order_date_obj.isoformat() if project_order_date_obj else None,
                'project_order_date_raw': project_order_raw,
                'column': column,
                'lane': lane_name,
                'board': board_name,
                'description': description,
            })
            seen_candidates.add(cid)

    links_table = []
    for info in candidate_cards:
        child_links = children_by_parent.get(info['cid'], [])
        matches = []
        match_ids = []
        match_details = []
        seen_norms = set()
        seen_ids = set()
        for child_id, child_title in child_links:
            norm_child = normalize_key(child_title)
            child_id = str(child_id).strip() if child_id else ''
            if not norm_child and not child_id:
                continue
            if child_id and child_id in seen_ids:
                continue
            if norm_child and norm_child in seen_norms and not child_id:
                continue
            lane_entries = seguimiento_titles.get(norm_child) if norm_child else None
            appended = False
            if lane_entries:
                for lane_detail in lane_entries:
                    lane_id = str(lane_detail.get('id') or '').strip()
                    lane_title = (lane_detail.get('title') or '').strip() or child_title
                    if lane_id and lane_id in seen_ids:
                        continue
                    if lane_title in matches:
                        continue
                    matches.append(lane_title)
                    match_ids.append(lane_id)
                    serialized = {'title': lane_title}
                    if lane_id:
                        serialized['id'] = lane_id
                    order_value = lane_detail.get('order_date')
                    if order_value:
                        serialized['order_date'] = order_value
                    order_raw_value = lane_detail.get('order_date_raw')
                    if order_raw_value:
                        serialized['order_date_raw'] = order_raw_value
                    column_name = lane_detail.get('column')
                    if column_name:
                        serialized['column'] = column_name
                    deadline_value = lane_detail.get('deadline')
                    if deadline_value:
                        serialized['deadline'] = deadline_value
                    lane_value = lane_detail.get('lane')
                    if lane_value:
                        serialized['lane'] = lane_value
                    match_details.append(serialized)
                    if lane_id:
                        seen_ids.add(lane_id)
                    _attach_child_card_metadata(serialized, lane_id or child_id)
                    appended = True
                if norm_child:
                    seen_norms.add(norm_child)
            if not appended:
                if child_title not in matches:
                    matches.append(child_title)
                    match_ids.append(child_id)
                    serialized = {'title': child_title}
                    if child_id:
                        serialized['id'] = child_id
                    detail = seguimiento_by_id.get(child_id) if child_id else None
                    if detail:
                        order_value = detail.get('order_date')
                        if order_value:
                            serialized['order_date'] = order_value
                        order_raw_value = detail.get('order_date_raw')
                        if order_raw_value:
                            serialized['order_date_raw'] = order_raw_value
                        column_name = detail.get('column')
                        if column_name:
                            serialized['column'] = column_name
                        deadline_value = detail.get('deadline')
                        if deadline_value:
                            serialized['deadline'] = deadline_value
                        lane_value = detail.get('lane')
                        if lane_value:
                            serialized['lane'] = lane_value
                    _attach_child_card_metadata(serialized, child_id or detail and detail.get('id'))
                    match_details.append(serialized)
                    if child_id:
                        seen_ids.add(child_id)
                if norm_child:
                    seen_norms.add(norm_child)
        entry = {
            'project': info['project'],
            'title': info['title'],
            'display_title': info['display_title'],
            'client': info['client'],
            'custom_card_id': info['custom_card_id'],
            'links': matches,
            'description': info.get('description', ''),
        }
        if info.get('column'):
            entry['column'] = info['column']
        if info.get('lane'):
            entry['lane'] = info['lane']
        if info.get('board'):
            entry['board'] = info['board']
        if matches and any(match_ids):
            entry['link_ids'] = match_ids
        elif not matches:
            entry['link_ids'] = []
        if match_details:
            entry['link_details'] = match_details
        elif not matches:
            entry['link_details'] = []
        if info['due']:
            entry['due'] = info['due'].isoformat()
        else:
            entry['due'] = None
        if info.get('client_delivery_date'):
            entry['client_delivery_date'] = info['client_delivery_date']
        if info.get('client_delivery_date_raw'):
            entry['client_delivery_date_raw'] = info['client_delivery_date_raw']
        if info.get('project_order_date'):
            entry['project_order_date'] = info['project_order_date']
        if info.get('project_order_date_raw'):
            entry['project_order_date_raw'] = info['project_order_date_raw']
        links_table.append(entry)
    return links_table


def attach_phase_starts(links_table, projects=None):
    """Attach scheduled start information to each Columna 1 entry."""

    if projects is None:
        projects = load_projects()
    def _coerce_date(value):
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            parsed = _safe_iso_date(value)
            if parsed:
                return parsed
        return None

    def _serialize_date(value):
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, str):
            parsed = _safe_iso_date(value)
            if parsed:
                return parsed.isoformat()
            value = value.strip()
            return value or None
        return None

    projects = filter_visible_projects(projects)
    scheduled_projects = copy.deepcopy(projects)
    schedule_data, _ = schedule_projects(scheduled_projects)
    planned_phase_starts = {}
    earliest_planned = {}

    for worker, days in schedule_data.items():
        if worker == UNPLANNED:
            continue
        for day_key, tasks in days.items():
            day_fallback = _safe_iso_date(day_key)
            for task in tasks:
                pid = task.get('pid')
                if not pid:
                    continue
                start_value = task.get('start_time')
                if isinstance(start_value, datetime):
                    start_day = start_value.date()
                else:
                    start_day = None
                    if start_value:
                        start_day = _safe_iso_date(start_value)
                    if not start_day:
                        start_day = _coerce_date(task.get('start_date'))
                if not start_day:
                    start_day = day_fallback
                if not start_day:
                    continue
                phase = task.get('phase')
                phase_name = phase_base(phase)
                if phase:
                    phase_map = planned_phase_starts.setdefault(pid, {})
                    current = phase_map.get(phase)
                    if current is None or start_day < current:
                        phase_map[phase] = start_day
                if phase_name not in {AUTO_RECEIVING_PHASE, 'lanzamiento'}:
                    current_earliest = earliest_planned.get(pid)
                    if current_earliest is None or start_day < current_earliest:
                        earliest_planned[pid] = start_day

    montar_by_name = {}
    plan_by_name = {}
    ids_by_name = {}
    montar_by_key = {}
    plan_by_key = {}
    ids_by_key = {}

    for proj in projects:
        phase_starts = planned_phase_starts.get(proj['id'], {}) or {}
        montar_serialized = _serialize_date(phase_starts.get('montar'))
        first_phase_date = earliest_planned.get(proj['id'])
        plan_serialized = (
            first_phase_date.isoformat() if isinstance(first_phase_date, date) else None
        )

        name = (proj.get('name') or '').strip()
        if not name:
            continue
        if montar_serialized:
            montar_by_name[name] = montar_serialized
        if plan_serialized:
            plan_by_name[name] = plan_serialized
        ids_by_name[name] = proj['id']

        split_name, _ = split_project_and_client(name)
        if split_name and split_name != name:
            if montar_serialized and split_name not in montar_by_name:
                montar_by_name[split_name] = montar_serialized
            if plan_serialized and split_name not in plan_by_name:
                plan_by_name[split_name] = plan_serialized
            ids_by_name.setdefault(split_name, proj['id'])

        keys = {normalize_key(name)}
        if split_name:
            keys.add(normalize_key(split_name))

        custom_id = (proj.get('custom_card_id') or '').strip()
        if custom_id:
            ids_by_name.setdefault(custom_id, proj['id'])
            if montar_serialized and custom_id not in montar_by_name:
                montar_by_name[custom_id] = montar_serialized
            if plan_serialized and custom_id not in plan_by_name:
                plan_by_name[custom_id] = plan_serialized
            keys.add(normalize_key(custom_id))

        code_match = PROJECT_TITLE_PATTERN.search(name)
        if not code_match and custom_id:
            code_match = PROJECT_TITLE_PATTERN.search(custom_id)
        if code_match:
            code_key = code_match.group(0)
            ids_by_name.setdefault(code_key, proj['id'])
            if montar_serialized and code_key not in montar_by_name:
                montar_by_name[code_key] = montar_serialized
            if plan_serialized and code_key not in plan_by_name:
                plan_by_name[code_key] = plan_serialized
            keys.add(normalize_key(code_key))

        for key in keys:
            if not key:
                continue
            if montar_serialized and key not in montar_by_key:
                montar_by_key[key] = montar_serialized
            if plan_serialized and key not in plan_by_key:
                plan_by_key[key] = plan_serialized
            ids_by_key.setdefault(key, proj['id'])

    enriched = []
    for item in links_table:
        entry = dict(item)
        montar_start = entry.get('montar_start')
        plan_start = entry.get('plan_start')
        pid = entry.get('pid')

        candidate_values = []
        for field in ('project', 'title', 'display_title', 'custom_card_id'):
            value = (entry.get(field) or '').strip()
            if value:
                candidate_values.append(value)
                split_value, _ = split_project_and_client(value)
                if split_value and split_value != value:
                    candidate_values.append(split_value)
        for value in list(candidate_values):
            code_match = PROJECT_TITLE_PATTERN.search(value)
            if code_match:
                candidate_values.append(code_match.group(0))

        for value in candidate_values:
            if montar_start and plan_start and pid:
                break
            if not montar_start:
                montar_start = montar_by_name.get(value)
            if not plan_start:
                plan_start = plan_by_name.get(value)
            norm_key = normalize_key(value)
            if norm_key:
                if not montar_start:
                    montar_start = montar_by_key.get(norm_key)
                if not plan_start:
                    plan_start = plan_by_key.get(norm_key)
                if not pid:
                    pid = ids_by_key.get(norm_key)
            if not pid:
                pid = ids_by_name.get(value) or pid

        if montar_start:
            entry['montar_start'] = montar_start
        if plan_start:
            entry['plan_start'] = plan_start
        if pid:
            entry['pid'] = pid
        enriched.append(entry)
    return enriched


def _gather_identifier_candidates(*values):
    candidates = []
    for raw in values:
        if raw in (None, ''):
            continue
        text = str(raw).strip()
        if not text:
            continue
        candidates.append(text)
        split_value, _ = split_project_and_client(text)
        if split_value and split_value != text:
            candidates.append(split_value)
    for value in list(candidates):
        match = PROJECT_TITLE_PATTERN.search(value)
        if match:
            candidates.append(match.group(0))
    return candidates


def _collect_project_identifier_candidates(project):
    values = []
    name = project.get('name')
    if name:
        values.append(name)
        code = _normalize_order_code(name)
        if code and code != name:
            values.append(code)
    custom_id = project.get('custom_card_id')
    if custom_id:
        values.append(custom_id)
    display_fields = project.get('kanban_display_fields') or {}
    if isinstance(display_fields, dict):
        for key in (
            'ID personalizado',
            'ID personalizado de tarjeta',
            'ID personalizado tarjeta',
        ):
            value = display_fields.get(key)
            if value:
                values.append(value)
    return _gather_identifier_candidates(*values)


def build_phase_finish_lookup(projects, phase_names):
    if not projects or not phase_names:
        return {}

    ordered_keys = []
    target_keys = set()
    for phase in phase_names:
        norm = normalize_key(phase)
        if not norm or norm in target_keys:
            continue
        ordered_keys.append(norm)
        target_keys.add(norm)

    if not ordered_keys:
        return {}

    schedule_map = compute_schedule_map(projects)
    per_pid = {}
    for pid, items in schedule_map.items():
        pid_str = str(pid)
        for _, day, phase, *_ in items:
            norm_phase = normalize_key(phase)
            if norm_phase not in target_keys:
                continue
            if isinstance(day, date):
                day_obj = day
            else:
                try:
                    day_obj = date.fromisoformat(str(day)[:10])
                except Exception:
                    continue
            phases = per_pid.setdefault(pid_str, {})
            current = phases.get(norm_phase)
            if current is None or day_obj > current:
                phases[norm_phase] = day_obj

    if not per_pid:
        return {}

    finish_by_pid = {}
    for pid_str, phase_map in per_pid.items():
        for phase_key in ordered_keys:
            finish_day = phase_map.get(phase_key)
            if finish_day:
                finish_by_pid[pid_str] = finish_day
                break

    if not finish_by_pid:
        return {}

    by_norm = {}
    for project in projects:
        pid = project.get('id')
        if not pid:
            continue
        finish_day = finish_by_pid.get(str(pid))
        if not finish_day:
            continue
        for value in _collect_project_identifier_candidates(project):
            norm = normalize_key(value)
            if not norm:
                continue
            existing = by_norm.get(norm)
            if existing is None or finish_day > existing:
                by_norm[norm] = finish_day

    return {'by_pid': finish_by_pid, 'by_norm': by_norm}


def _resolve_phase_finish_for_card(card, title, custom_id, lookup):
    if not lookup:
        return None

    if isinstance(lookup, dict):
        by_norm = lookup.get('by_norm') or {}
    else:
        by_norm = lookup

    if not by_norm:
        return None

    candidates = []
    candidates.extend(_gather_identifier_candidates(title, custom_id))

    if isinstance(card, dict):
        values = []
        for key in (
            'customCardId',
            'customcardid',
            'customId',
            'customid',
        ):
            value = card.get(key)
            if value:
                values.append(value)
        fields_raw = card.get('customfields') or card.get('customFields')
        parsed_fields = _decode_json(fields_raw)
        if isinstance(parsed_fields, dict):
            for key, value in parsed_fields.items():
                key_text = str(key).casefold()
                if any(token in key_text for token in ('id', 'pedido', 'proyecto')):
                    values.append(value)
        elif isinstance(parsed_fields, list):
            for item in parsed_fields:
                if not isinstance(item, dict):
                    continue
                label = str(
                    item.get('name')
                    or item.get('label')
                    or item.get('text')
                    or ''
                ).casefold()
                if not label:
                    continue
                if any(token in label for token in ('id', 'pedido', 'proyecto')):
                    value = item.get('value') or item.get('text')
                    if value:
                        values.append(value)
        if values:
            candidates.extend(_gather_identifier_candidates(*values))

    seen = set()
    for candidate in candidates:
        norm = normalize_key(candidate)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        finish_day = by_norm.get(norm)
        if finish_day:
            return finish_day

    return None


def _parse_stored_pedido_date(stored_date, *, today):
    """Parse stored pedido deadlines, preferring explicit years when present."""

    parsed = parse_kanban_date(stored_date)
    if isinstance(parsed, date):
        return parsed

    if not stored_date:
        return None

    text = str(stored_date).strip()
    if not text:
        return None

    parts = [p for p in re.split(r"[/-]", text) if p]
    if len(parts) < 2:
        return None

    try:
        day = int(parts[0])
        month = int(parts[1])
        if len(parts) >= 3:
            year = int(parts[2])
            if year < 100:
                year += 2000
        else:
            year = today.year
        return date(year, month, day)
    except Exception:
        return None


def compute_pedidos_entries(
    compras_raw, column_colors, today, *, soldar_finish_lookup=None
):
    calendar_data = {
        key: {'scheduled': {}, 'unconfirmed': []}
        for key in ('pedidos', 'subcontrataciones')
    }
    calendar_titles = set()

    for data in compras_raw.values():
        card = data['card']
        stored_date = data.get('stored_date')
        prev_date = data.get('prev_date')
        title = (card.get('title') or '').strip()
        if not title:
            continue

        _, client_name = split_project_and_client(title)
        custom_id = get_card_custom_id(card)

        column = (card.get('columnname') or card.get('columnName') or '').strip()
        lane_name = (card.get('lanename') or card.get('laneName') or '').strip()
        column_key = normalize_key(column)
        lane_key = normalize_key(lane_name)

        if lane_key != PEDIDOS_SEGUIMIENTO_LANE_KEY:
            continue

        if column_key in PEDIDOS_HIDDEN_KEYS:
            continue

        target_key = None
        target_config = None
        for config_key in ('pedidos', 'subcontrataciones'):
            config = CALENDAR_CONFIGS[config_key]
            if (
                column_key in config['allowed_keys']
                or column_key in config['unconfirmed_keys']
            ):
                target_key = config_key
                target_config = config
                break

        if target_key is None:
            continue

        cid = card.get('taskid') or card.get('cardId') or card.get('id')

        raw_fields = card.get('customFields') or card.get('customfields')
        custom_fields = _coerce_custom_field_map(raw_fields)
        mecanizado_date = parse_kanban_date(
            custom_fields.get('Fecha Mec.') or custom_fields.get('Fecha Mec')
        )
        tratamiento_date = parse_kanban_date(
            custom_fields.get('Fecha Trat.') or custom_fields.get('Fecha Trat')
        )
        subcontr_date = mecanizado_date or tratamiento_date

        if stored_date:
            d = _parse_stored_pedido_date(stored_date, today=today)
            if not d:
                d = parse_kanban_date(card.get('deadline'))
        elif column_key in target_config['unconfirmed_keys']:
            d = None
        else:
            m = re.search(r"\((\d{2})/(\d{2})\)", title)
            if m:
                day, month = int(m.group(1)), int(m.group(2))
                try:
                    d = date(today.year, month, day)
                except ValueError:
                    d = parse_kanban_date(card.get('deadline'))
            else:
                d = parse_kanban_date(card.get('deadline'))

        if target_key == 'subcontrataciones' and subcontr_date:
            d = subcontr_date
        elif (
            target_key == 'subcontrataciones'
            and column_key in SUBCONTRATACIONES_TREATMENT_KEYS
        ):
            finish_day = _resolve_phase_finish_for_card(
                card, title, custom_id, soldar_finish_lookup
            )
            if isinstance(finish_day, date):
                d = finish_day

        color = column_colors.get(column, '#999999')
        override = target_config['color_overrides'].get(column_key)
        if override:
            color = override

        entry = {
            'project': title,
            'color': color,
            'hours': None,
            'lane': lane_name,
            'client': client_name,
            'column': column,
            'cid': cid,
            'prev_date': prev_date,
            'custom_card_id': custom_id,
            'kanban_date': stored_date or card.get('deadline') or '',
        }

        bucket = calendar_data[target_key]
        if d:
            bucket['scheduled'].setdefault(d, []).append(entry)
            if target_key == 'subcontrataciones' and subcontr_date:
                simulated_day = _add_business_days(subcontr_date, 7)
                if simulated_day:
                    simulated_entry = dict(entry)
                    simulated_entry['simulated'] = True
                    simulated_entry['simulated_parent'] = cid
                    simulated_entry['color'] = '#b0b0b0'
                    bucket['scheduled'].setdefault(simulated_day, []).append(
                        simulated_entry
                    )
        else:
            bucket['unconfirmed'].append(entry)

        calendar_titles.add(title)

    return {
        'pedidos': calendar_data['pedidos'],
        'subcontrataciones': calendar_data['subcontrataciones'],
        'titles': calendar_titles,
    }


def _format_popup_order_date(value):
    parsed = None
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, str) and value.strip():
        parsed = parse_kanban_date(value.strip())
    if isinstance(parsed, date):
        return parsed.strftime('%d/%m/%Y')
    return str(value or '').strip()


def build_project_order_popup_map(projects, *, today=None):
    """Return pedido titles and Fecha pedido values grouped by project pid."""

    if today is None:
        today = local_today()
    compras_raw, _column_colors = load_compras_raw()
    links_table = attach_phase_starts(build_project_links(compras_raw), projects)
    annotate_order_details(links_table, today=today)
    result = {}
    for entry in links_table:
        pid = entry.get('pid')
        if not pid:
            continue
        pid_key = str(pid)
        details = entry.get('link_details') or []
        raw_titles = entry.get('links') or []
        total = max(len(details), len(raw_titles))
        seen = set()
        for idx in range(total):
            detail = (
                details[idx]
                if idx < len(details) and isinstance(details[idx], dict)
                else {}
            )
            fallback_title = raw_titles[idx] if idx < len(raw_titles) else ''
            title = (detail.get('title') or fallback_title or '').strip()
            if not title:
                continue
            order_date = _format_popup_order_date(
                detail.get('order_date') or detail.get('order_date_raw')
            )
            dedupe_key = (title, order_date)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            result.setdefault(pid_key, []).append(
                {
                    'title': title,
                    'order_date': order_date or 'Sin fecha pedido',
                }
            )
    return result

def build_complete_orders_summary(today=None):
    """Return delayed/today/tomorrow pedido entries for the Completo header."""

    if today is None:
        today = local_today()

    compras_raw, column_colors = load_compras_raw()
    linked_projects = build_project_links(compras_raw)
    project_by_order_id = {}
    project_by_order_title = {}

    def remember_order_link(order_id, order_title, project_name):
        clean_project = (project_name or '').strip()
        if not clean_project:
            return
        clean_id = str(order_id or '').strip()
        if clean_id:
            project_by_order_id.setdefault(clean_id, clean_project)
        clean_title = (order_title or '').strip()
        if clean_title:
            title_key = normalize_key(clean_title)
            if title_key:
                project_by_order_title.setdefault(title_key, clean_project)

    for linked in linked_projects:
        project_name = (
            linked.get('display_title')
            or linked.get('project')
            or linked.get('title')
            or ''
        )
        details = linked.get('link_details') or []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            remember_order_link(detail.get('id'), detail.get('title'), project_name)
        for order_id, order_title in zip(
            linked.get('link_ids') or [], linked.get('links') or []
        ):
            remember_order_link(order_id, order_title, project_name)

    tomorrow = today + timedelta(days=1)
    summary = {
        'late': {'title': 'Atrasados', 'items': []},
        'today': {'title': 'Hoy', 'items': []},
        'tomorrow': {'title': 'Mañana', 'items': []},
    }
    pedidos_payload = compute_pedidos_entries(compras_raw, column_colors, today)
    scheduled = pedidos_payload['pedidos']['scheduled']

    for due_day, entries in scheduled.items():
        if not isinstance(due_day, date):
            continue
        if due_day < today:
            bucket_key = 'late'
        elif due_day == today:
            bucket_key = 'today'
        elif due_day == tomorrow:
            bucket_key = 'tomorrow'
        else:
            continue
        for entry in entries:
            order_title = (entry.get('project') or '').strip()
            order_id = str(entry.get('cid') or '').strip()
            order_title_key = normalize_key(order_title)
            project_name = (
                project_by_order_id.get(order_id)
                or project_by_order_title.get(order_title_key)
                or 'Sin proyecto vinculado'
            )
            summary[bucket_key]['items'].append(
                {
                    'title': order_title,
                    'project': project_name,
                    'date': due_day.isoformat(),
                }
            )

    for bucket in summary.values():
        bucket['items'].sort(
            key=lambda item: (item.get('date') or '', item.get('title') or '')
        )

    return summary


def build_calendar_weeks(scheduled, today):
    scheduled_dates = [
        d for d, items in scheduled.items() if isinstance(d, date) and items
    ]

    if scheduled_dates:
        first_day = min(scheduled_dates)
        last_day = max(scheduled_dates)
        start = first_day - timedelta(days=first_day.weekday())
        end_week = last_day - timedelta(days=last_day.weekday())
        visible_end = end_week + timedelta(days=4)
    else:
        start = today - timedelta(weeks=3)
        start -= timedelta(days=start.weekday())

        current_month_start = date(today.year, today.month, 1)
        months_ahead = 2
        end_month = current_month_start
        for _ in range(months_ahead):
            end_month = (end_month.replace(day=28) + timedelta(days=4)).replace(day=1)
        visible_end = (
            (end_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1)
        )
        end_week = visible_end - timedelta(days=visible_end.weekday())

    if end_week < start:
        end_week = start
        visible_end = start + timedelta(days=4)

    weeks = []
    current = start
    while current <= end_week:
        week = {'number': current.isocalendar()[1], 'days': []}
        for i in range(5):
            day = current + timedelta(days=i)
            month_label = ''
            first_weekday = date(day.year, day.month, 1).weekday()
            if first_weekday < 5:
                if day.day == 1:
                    month_label = CALENDAR_MONTH_NAMES[day.month - 1]
            else:
                if day.weekday() == 0 and 1 < day.day <= 7:
                    month_label = CALENDAR_MONTH_NAMES[day.month - 1]
            tasks = scheduled.get(day, []) if start <= day <= visible_end else []
            week['days'].append(
                {
                    'date': day,
                    'day': f"{day.day:02d}",
                    'month': month_label,
                    'tasks': tasks,
                }
            )
        weeks.append(week)
        current += timedelta(weeks=1)

    return weeks


def annotate_order_details(entries, *, today=None):
    """Add formatted order dates and elapsed days to link details."""

    if today is None:
        today = local_today()

    if not entries:
        return entries

    for entry in entries:
        details = entry.get('link_details')
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, dict):
                continue
            order_date_obj = None
            order_iso = detail.get('order_date')
            if isinstance(order_iso, str) and order_iso.strip():
                text = order_iso.strip()
                try:
                    order_date_obj = date.fromisoformat(text[:10])
                except ValueError:
                    parsed = parse_kanban_date(text)
                    if isinstance(parsed, date):
                        order_date_obj = parsed
                else:
                    detail['order_date'] = order_date_obj.isoformat()
            if order_date_obj is None:
                raw_value = detail.get('order_date_raw')
                if isinstance(raw_value, str) and raw_value.strip():
                    parsed = parse_kanban_date(raw_value.strip())
                    if isinstance(parsed, date):
                        order_date_obj = parsed
                        detail['order_date'] = parsed.isoformat()
            if order_date_obj is not None:
                elapsed = business_days_since(order_date_obj, today=today)
                if elapsed is not None:
                    detail['order_days'] = elapsed
            elif 'order_days' in detail:
                detail.pop('order_days', None)

    return entries


def filter_project_links_by_titles(
    links_table, valid_titles, valid_ids=None, kanban_cards=None
):
    if not links_table:
        return []

    valid_norm_map = {}
    for raw_title in valid_titles or []:
        if not raw_title:
            continue
        norm = normalize_key(raw_title)
        if not norm:
            continue
        valid_norm_map.setdefault(norm, []).append(raw_title)

    valid_norms = set(valid_norm_map.keys())

    if not valid_norms:
        return []

    card_by_id = {}
    cards_by_norm = {}
    if kanban_cards:
        for cid, payload in kanban_cards.items():
            if not isinstance(payload, dict):
                continue
            card = payload.get('card') if isinstance(payload.get('card'), dict) else None
            if not card:
                continue
            card_id = card.get('taskid') or card.get('cardId') or card.get('id') or cid
            card_id = str(card_id).strip() if card_id not in (None, '') else ''
            title = (card.get('title') or '').strip()
            if card_id:
                card_by_id[card_id] = payload
            if title:
                norm = normalize_key(title)
                if norm:
                    cards_by_norm.setdefault(norm, []).append(payload)

    filtered = []
    covered_norms = set()
    for item in links_table:
        link_titles = list(item.get('links') or [])
        link_ids = list(item.get('link_ids') or [])
        if len(link_ids) < len(link_titles):
            link_ids.extend([''] * (len(link_titles) - len(link_ids)))
        link_details = list(item.get('link_details') or [])

        matches = []
        kept_ids = []
        kept_details = []
        seen_norms = set()
        for idx, (title, cid) in enumerate(zip(link_titles, link_ids)):
            text = (title or '').strip()
            cid_str = str(cid).strip() if cid not in (None, '') else ''

            detail = link_details[idx] if idx < len(link_details) else None
            detail_copy = {}
            detail_title = ''
            if isinstance(detail, dict):
                detail_copy = {k: v for k, v in detail.items() if v not in (None, '')}
                detail_title = (detail.get('title') or '').strip()

            payload = card_by_id.get(cid_str) if cid_str else None
            card_title = ''
            if payload:
                card = payload.get('card') if isinstance(payload.get('card'), dict) else None
                if card:
                    card_title = (card.get('title') or '').strip()

            candidate_titles = []
            if text:
                candidate_titles.append(text)
            if detail_title and detail_title not in candidate_titles:
                candidate_titles.append(detail_title)
            if card_title and card_title not in candidate_titles:
                candidate_titles.append(card_title)

            matched_norm = None
            display_title = ''
            for candidate in candidate_titles:
                norm_candidate = normalize_key(candidate)
                if not norm_candidate or norm_candidate in seen_norms:
                    continue
                if norm_candidate in valid_norms:
                    matched_norm = norm_candidate
                    titles = valid_norm_map.get(matched_norm)
                    display_title = titles[0] if titles else candidate
                    break

            if not matched_norm:
                continue

            if not display_title:
                display_title = text or detail_title or card_title

            matches.append(display_title)

            if cid_str:
                kept_ids.append(cid_str)

            if not detail_copy:
                detail_copy = {}
            if 'title' not in detail_copy or not detail_copy['title']:
                detail_copy['title'] = display_title
            if cid_str and ('id' not in detail_copy or not detail_copy['id']):
                detail_copy['id'] = cid_str

            if payload and isinstance(payload.get('card'), dict):
                card = payload['card']
                column = (card.get('columnname') or card.get('columnName') or '').strip()
                lane = (card.get('lanename') or card.get('laneName') or '').strip()
                deadline = parse_kanban_date(card.get('deadline'))
                if column and not detail_copy.get('column'):
                    detail_copy['column'] = column
                if lane and not detail_copy.get('lane'):
                    detail_copy['lane'] = lane
                if deadline and not detail_copy.get('deadline'):
                    detail_copy['deadline'] = deadline.isoformat()
                order_date_obj, order_raw = _resolve_order_custom_field(card)
                if order_raw and not detail_copy.get('order_date_raw'):
                    detail_copy['order_date_raw'] = order_raw
                if order_date_obj and not detail_copy.get('order_date'):
                    detail_copy['order_date'] = order_date_obj.isoformat()

            kept_details.append(detail_copy)
            seen_norms.add(matched_norm)
            covered_norms.add(matched_norm)

        if not matches:
            continue
        entry = dict(item)
        entry['links'] = matches
        if any(kept_ids):
            entry['link_ids'] = kept_ids
        else:
            entry.pop('link_ids', None)
        if kept_details:
            entry['link_details'] = kept_details
        else:
            entry.pop('link_details', None)
        filtered.append(entry)

    if kanban_cards:
        seen_payloads = set()

        def _payload_identity(payload):
            return id(payload)

        def _build_fallback_entry(payload):
            card = payload.get('card') if isinstance(payload.get('card'), dict) else None
            if not card:
                return None
            title = (card.get('title') or '').strip()
            if not title:
                return None
            norm_title = normalize_key(title)
            if norm_title not in valid_norms:
                return None
            project_name, client_name = split_project_and_client(title)
            custom_id = get_card_custom_id(card)
            column = (card.get('columnname') or card.get('columnName') or '').strip()
            lane = (card.get('lanename') or card.get('laneName') or '').strip()
            board = (card.get('boardName') or card.get('boardname') or '').strip()
            description = (card.get('description') or '').strip()
            due = parse_kanban_date(card.get('deadline'))
            client_delivery_raw = _get_custom_field_text(card, 'Fecha Cliente', 'Fecha cliente')
            client_delivery_date_obj = parse_kanban_date(client_delivery_raw)
            cid_value = card.get('taskid') or card.get('cardId') or card.get('id')
            cid_str = str(cid_value).strip() if cid_value not in (None, '') else ''
            detail = {'title': title}
            if cid_str:
                detail['id'] = cid_str
            if column:
                detail['column'] = column
            if lane:
                detail['lane'] = lane
            if due:
                detail['deadline'] = due.isoformat()
            order_date_obj, order_raw = _resolve_order_custom_field(card)
            if order_raw:
                detail['order_date_raw'] = order_raw
            if order_date_obj:
                detail['order_date'] = order_date_obj.isoformat()
            entry = {
                'project': project_name or title,
                'title': title,
                'display_title': title,
                'client': client_name,
                'custom_card_id': custom_id,
                'links': [title],
                'link_details': [detail],
                'due': due.isoformat() if due else None,
                'client_delivery_date': client_delivery_date_obj.isoformat() if client_delivery_date_obj else None,
                'client_delivery_date_raw': client_delivery_raw,
                'project_order_date': order_date_obj.isoformat() if order_date_obj else None,
                'project_order_date_raw': order_raw,
                'description': description,
            }
            if column:
                entry['column'] = column
            if lane:
                entry['lane'] = lane
            if board:
                entry['board'] = board
            if cid_str:
                entry['link_ids'] = [cid_str]
            stored_date = payload.get('stored_date')
            if stored_date:
                entry['stored_date'] = stored_date
            prev_date = payload.get('prev_date')
            if prev_date:
                entry['prev_date'] = prev_date
            return entry

        missing_norms = [
            norm for norm in valid_norms if norm and norm not in covered_norms
        ]
        for norm in missing_norms:
            for payload in cards_by_norm.get(norm, []):
                ident = _payload_identity(payload)
                if ident in seen_payloads:
                    continue
                entry = _build_fallback_entry(payload)
                if not entry:
                    continue
                filtered.append(entry)
                seen_payloads.add(ident)
                if entry.get('links'):
                    for link in entry['links']:
                        norm_link = normalize_key(link)
                        if norm_link:
                            covered_norms.add(norm_link)

    return filtered


def _extract_blocking_material_titles(entry, planned_day):
    """Return titles that block scheduling because of material deadlines."""

    if not entry or not isinstance(planned_day, date):
        return []

    details = entry.get('link_details')
    raw_titles = entry.get('links')
    if not details and not raw_titles:
        return []

    blocking = []
    seen = set()
    for detail, fallback in zip_longest(details or [], raw_titles or []):
        if isinstance(detail, dict):
            title = (detail.get('title') or fallback or '').strip()
            column = detail.get('column')
            deadline_str = detail.get('deadline')
        else:
            title = (fallback or '').strip()
            column = None
            deadline_str = None
        if not title:
            continue
        norm_title = normalize_key(title)
        if norm_title in seen:
            continue
        if column and normalize_key(column) in MATERIAL_EXCLUDED_COLUMNS:
            continue
        if not deadline_str:
            continue
        deadline = parse_kanban_date(deadline_str)
        if not isinstance(deadline, date):
            continue
        if deadline > planned_day:
            blocking.append(title)
            seen.add(norm_title)
    return blocking


def _find_link_entry_for_project(project, links_table):
    """Return the Columna 1 entry that corresponds to *project*."""

    if not project:
        return None

    keys = set()
    name = (project.get('name') or '').strip()
    if name:
        keys.add(normalize_key(name))
        proj_name, _ = split_project_and_client(name)
        if proj_name:
            keys.add(normalize_key(proj_name))
        code_match = re.search(r'OF\s*\d{4}', name, re.IGNORECASE)
        if code_match:
            keys.add(normalize_key(code_match.group(0)))

    custom = (project.get('custom_card_id') or '').strip()
    if custom:
        keys.add(normalize_key(custom))

    if not keys:
        return None

    for item in links_table or []:
        for field in ('project', 'title', 'display_title', 'custom_card_id'):
            value = item.get(field)
            if not value:
                continue
            if normalize_key(value) in keys:
                return item
    return None


def material_blockers_for_project(projects, pid, planned_day):
    """Return Kanban Seguimiento Compras titles that block *pid* on *planned_day*."""

    if not pid or not planned_day:
        return []

    if isinstance(planned_day, str):
        try:
            planned_date = date.fromisoformat(planned_day)
        except ValueError:
            return []
    elif isinstance(planned_day, date):
        planned_date = planned_day
    else:
        return []

    str_pid = str(pid)
    project = next((p for p in projects if str(p.get('id')) == str_pid), None)
    if not project:
        return []

    compras_raw, _ = load_compras_raw()
    base_links = build_project_links(compras_raw)
    enriched_links = attach_phase_starts(base_links, projects)
    entry = next(
        (item for item in enriched_links if str(item.get('pid')) == str_pid),
        None,
    )
    if not entry:
        entry = _find_link_entry_for_project(project, enriched_links)
    if not entry:
        entry = _find_link_entry_for_project(project, base_links)
    if not entry:
        return []

    return _extract_blocking_material_titles(entry, planned_date)


def load_column_colors():
    if os.path.exists(KANBAN_COLUMN_COLORS_FILE):
        with open(KANBAN_COLUMN_COLORS_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_column_colors(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KANBAN_COLUMN_COLORS_FILE, 'w') as f:
        json.dump(data, f)


def load_prefill_project():
    """Return the pending Kanbanize project data, if any."""
    if os.path.exists(KANBAN_PREFILL_FILE):
        with open(KANBAN_PREFILL_FILE, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def save_prefill_project(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KANBAN_PREFILL_FILE, 'w') as f:
        json.dump(data, f)


def clear_prefill_project():
    if os.path.exists(KANBAN_PREFILL_FILE):
        try:
            os.remove(KANBAN_PREFILL_FILE)
        except Exception:
            pass

def normalize_card(c):
    return {
        "taskid": c.get("card_id"),
        "title": c.get("title"),
        "customid": c.get("custom_id"),
        "columnid": c.get("column_id"),
        "laneid": c.get("lane_id"),
        "boardid": c.get("board_id"),
        "workflowid": c.get("workflow_id"),
        "columnname": c.get("column_name"),
        "lanename": c.get("lane_name"),
    }

def _decode_json(value):
    """Try to parse *value* as JSON, ignoring trailing text."""
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8', errors='ignore')
        except Exception:
            return value
    if isinstance(value, str):
        # Quita posibles %encoding y espacios
        value = urllib.parse.unquote(value).strip()
        try:
            obj, _ = json.JSONDecoder().raw_decode(value)
            return obj
        except Exception:
            return value
    return value


def _parse_kanban_payload(req):
    """Return payload dict from a webhook request."""
    data = req.get_json(silent=True)
    if isinstance(data, str):
        data = _decode_json(data)
    if not data:
        payload = (
            req.form.get('kanbanize_payload')
            or req.form.get('payload')
            or req.form.get('data')
        )
        if not payload:
            raw = req.get_data(as_text=True)
            payload = raw.strip() if raw else None
        if payload:
            if isinstance(payload, str):
                payload = urllib.parse.unquote(payload)
            data = _decode_json(payload)
    if isinstance(data, dict):
        return data
    return None
def build_calendar(start, end):
    """Return full days list, collapsed columns and week spans."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    cols = []
    i = 0
    while i < len(days):
        d = days[i]
        if d.weekday() == 5:
            wk = [d]
            if i + 1 < len(days) and days[i + 1].weekday() == 6:
                wk.append(days[i + 1])
            cols.append({"type": "weekend", "dates": wk})
            i += len(wk)
        else:
            cols.append({"type": "day", "dates": [d]})
            i += 1

    def wnum(day: date):
        iso_year, iso_week, _ = day.isocalendar()
        return iso_year, iso_week

    week_spans = []
    current_week_key = wnum(cols[0]["dates"][0])
    span = 0
    for c in cols:
        week_key = wnum(c["dates"][0])
        if week_key != current_week_key:
            week_spans.append({"week": current_week_key[1], "year": current_week_key[0], "span": span})
            current_week_key = week_key
            span = 1
        else:
            span += 1
    week_spans.append({"week": current_week_key[1], "year": current_week_key[0], "span": span})
    return days, cols, week_spans


def move_phase_date(
    projects,
    pid,
    phase,
    new_date,
    worker=None,
    part=None,
    *,
    save=True,
    mode="split",
    push_from=None,
    unblock=False,
    skip_block=False,
    start_hour=None,
    track=None,
    initial_mapping=None,
):
    """Move ``phase`` of project ``pid`` so it starts on ``new_date``.

    ``mode`` controls how existing work is handled:

    * ``"split"`` (default) keeps other tasks in place, allowing the phase to
      be divided around them.
    * ``"push"`` shifts subsequent tasks for the same worker so the phase can
      remain continuous.

    Return tuple ``(day, warning, info)`` where ``day`` is the first day of the
    phase after rescheduling (``None`` when it could not be moved), ``warning``
    carries deadline or blocking messages, and ``info`` aggregates metadata
    about the operation.
    """
    def _fail(warn, info=None):
        return None, warn, info

    if part in (None, '', 'None'):
        part = None
    else:
        try:
            part = int(part)
        except Exception:
            part = None

    mapping = (
        initial_mapping
        if initial_mapping is not None
        else compute_schedule_map(projects)
    )
    tasks = [t for t in mapping.get(pid, []) if t[2] == phase]
    if not tasks:
        return _fail('Fase no encontrada')
    if part is not None:
        tasks = [t for t in tasks if t[4] == part]
        if not tasks:
            return _fail('Fase no encontrada')
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return _fail('Proyecto no encontrado')
    if phase != 'pedidos' and any(
        t['phase'] == phase and (part is None or t.get('part') == part)
        for t in proj.get('frozen_tasks', [])
    ):
        return _fail('Fase congelada')

    vac_map = _schedule_mod._build_vacation_map()
    if (
        phase != 'pedidos'
        and worker
        and worker != 'Irene'
        and new_date in vac_map.get(worker, set())
    ):
        return _fail('Vacaciones en esa fecha')

    # Remember the originally requested day.  When ``start_hour`` exceeds the
    # daily limit we normally roll the phase to the next workday, but in push
    # mode we instead keep the phase on the requested day and start at hour 0 so
    # subsequent tasks can be shifted after it.
    target_day = new_date
    sched_day = new_date
    sched_hour = start_hour if start_hour is not None else 0
    limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
    office_worker = normalize_key(worker) in {
        normalize_key('Oficina técnica'),
        normalize_key('Oficina técnica.'),
        normalize_key('Oficina Tecnica'),
        normalize_key('Oficina Tecnica.'),
    }
    unlimited_launch_slot = office_worker and phase == 'lanzamiento'
    push_same_day = False
    if start_hour is not None and start_hour >= limit and not unlimited_launch_slot:
        if mode == "push":
            sched_hour = 0
            push_same_day = True
        else:
            sched_day = next_workday(sched_day)
            sched_hour = 0

    warning = None
    affected = track if track is not None else []
    if proj.get('due_date'):
        try:
            due_dt = date.fromisoformat(proj['due_date'])
            phase_hours = proj['phases'].get(phase)
            if isinstance(phase_hours, list):
                if part is None or part >= len(phase_hours):
                    return _fail('Fase no encontrada')
                hours = int(phase_hours[part])
            else:
                hours = int(phase_hours)
            days_needed = (hours + HOURS_PER_DAY - 1) // HOURS_PER_DAY
            test_end = sched_day
            for _ in range(days_needed - 1):
                test_end = next_workday(test_end)
            if test_end > due_dt:
                msg = CLIENT_DEADLINE_MSG if proj.get('due_confirmed') else DEADLINE_MSG
                warning = f"{msg}\n{proj['name']} - {proj['client']} - {due_dt.strftime('%Y-%m-%d')}"
        except Exception:
            pass
    if 'hours' not in locals():
        phase_val = proj['phases'].get(phase)
        if isinstance(phase_val, list):
            if part is None or part >= len(phase_val):
                return _fail('Fase no encontrada')
            hours = int(phase_val[part])
        else:
            hours = int(phase_val)
    # Apply the change to the real project list
    if part is None and not isinstance(proj['phases'].get(phase), list):
        seg_starts = proj.setdefault('segment_starts', {}).setdefault(phase, [None])
        seg_starts[0] = sched_day.isoformat()
        seg_hours = proj.setdefault('segment_start_hours', {}).setdefault(phase, [None])
        seg_hours[0] = sched_hour
        if worker:
            proj.setdefault('assigned', {})[phase] = worker
    else:
        seg_starts = proj.setdefault('segment_starts', {}).setdefault(
            phase, [None] * len(proj['phases'][phase])
        )
        idx = part if part is not None else 0
        while len(seg_starts) <= idx:
            seg_starts.append(None)
        seg_starts[idx] = sched_day.isoformat()
        seg_hours = proj.setdefault('segment_start_hours', {}).setdefault(
            phase, [None] * len(proj['phases'][phase])
        )
        if idx >= len(seg_hours):
            seg_hours.extend([None] * (idx + 1 - len(seg_hours)))
        seg_hours[idx] = sched_hour
        if worker:
            seg_workers = proj.setdefault('segment_workers', {}).setdefault(
                phase, [None] * len(proj['phases'][phase])
            )

            if idx >= len(seg_workers):
                seg_workers.extend([None] * (idx + 1 - len(seg_workers)))
            seg_workers[idx] = worker
    if worker:
        proj['planned'] = worker != UNPLANNED

    if mode == "push" and worker and worker != UNPLANNED:
        # Move other tasks for the worker after this phase so it stays
        # contiguous without splitting. Only phases starting from ``push_from``
        # (if provided) are shifted; otherwise, all tasks from ``new_date``
        # onward are moved.
        phase_val = proj['phases'].get(phase)
        if isinstance(phase_val, list):
            hours = int(phase_val[part or 0])
        else:
            hours = int(phase_val)

        # Determine the exact end of the moved phase taking into account
        # the worker's daily limit and vacations so subsequent phases can be
        # appended immediately afterwards.
        vac_days = _schedule_mod._build_vacation_map().get(worker, set())
        limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
        end_day = sched_day
        end_hour = sched_hour
        remaining = hours
        while remaining > 0:
            if end_day in vac_days:
                end_day = next_workday(end_day)
                end_hour = 0
                continue
            free = limit - end_hour
            if remaining <= free:
                end_hour += remaining
                remaining = 0
            else:
                remaining -= free
                end_day = next_workday(end_day)
                end_hour = 0
        if end_hour >= limit:
            end_day = next_workday(end_day)
            end_hour = 0

        # `current_day`/`current_hour` mark the next free slot after inserting
        # the new phase. Pushed phases will be placed sequentially here.
        current_day = end_day
        current_hour = end_hour

        # Push mode needs a fresh map after inserting the moved phase so the
        # subsequent tasks can be discovered in their updated order. Split mode
        # does not need this second full schedule calculation.
        mapping = compute_schedule_map(projects)
        start_push = sched_day if push_same_day else next_workday(target_day)
        if push_from:
            pf_pid, pf_phase, pf_part = push_from
            selected = None
            for w, day_str, ph, hrs, prt in mapping.get(pf_pid, []):
                if (
                    w == worker
                    and ph == pf_phase
                    and (pf_part is None or prt == pf_part)
                ):
                    d = date.fromisoformat(day_str)
                    if d >= start_push and (selected is None or d < selected):
                        selected = d
            if selected is not None:
                start_push = selected
        seen = {}
        for opid, items in mapping.items():
            for w, day_str, ph, hrs, prt in items:
                if w != worker:
                    continue
                d = date.fromisoformat(day_str)
                if d < start_push:
                    continue
                if opid == pid and ph == phase and (part is None or prt == part):
                    continue
                key = (opid, ph, prt)
                if key not in seen or d < seen[key]:
                    seen[key] = d

        for start, opid, oph, oprt in sorted(
            (v, k[0], k[1], k[2]) for k, v in seen.items()
        ):
            other_proj = next((p for p in projects if p['id'] == opid), None)
            if not other_proj:
                while current_day in vac_days:
                    current_day = next_workday(current_day)
                    current_hour = 0
                continue
            if oph != 'pedidos' and any(
                t['phase'] == oph and (oprt is None or t.get('part') == oprt)
                for t in other_proj.get('frozen_tasks', [])
            ):
                if not unblock and not skip_block:
                    return _fail(
                        {
                            'pid': opid,
                            'phase': oph,
                            'part': oprt,
                            'name': other_proj.get('name', ''),
                        }
                    )
                if skip_block:
                    val = other_proj['phases'][oph]
                    if isinstance(val, list):
                        h = int(val[oprt])
                    else:
                        h = int(val)
                    rem = h
                    day = start
                    hour = 0
                    while rem > 0:
                        if day in vac_days:
                            day = next_workday(day)
                            hour = 0
                            continue
                        free = limit - hour
                        if rem <= free:
                            hour += rem
                            rem = 0
                        else:
                            rem -= free
                            day = next_workday(day)
                            hour = 0
                    current_day = day
                    current_hour = hour
                    if current_hour >= limit:
                        current_day = next_workday(current_day)
                        current_hour = 0
                    continue
                # unblock
                other_proj['frozen_tasks'] = [
                    t
                    for t in other_proj.get('frozen_tasks', [])
                    if not (t['phase'] == oph and (oprt is None or t.get('part') == oprt))
                ]
            while current_day in vac_days:
                current_day = next_workday(current_day)
                current_hour = 0
            move_phase_date(
                projects,
                opid,
                oph,
                current_day,
                worker,
                oprt,
                save=False,
                mode="split",
                start_hour=current_hour,
                track=affected,
            )
            affected.append({'pid': opid, 'phase': oph, 'part': oprt})
            val = other_proj['phases'][oph]
            if isinstance(val, list):
                h = int(val[oprt])
            else:
                h = int(val)
            rem = h
            day = current_day
            hour = current_hour
            while rem > 0:
                if day in vac_days:
                    day = next_workday(day)
                    hour = 0
                    continue
                free = limit - hour
                if rem <= free:
                    hour += rem
                    rem = 0
                else:
                    rem -= free
                    day = next_workday(day)
                    hour = 0
            current_day = day
            current_hour = hour
            if current_hour >= limit:
                current_day = next_workday(current_day)
                current_hour = 0

    if save:
        save_projects(projects)
    # Determine end of this phase for logging purposes. When ``mode`` was
    # ``push`` the values may already be available from the push calculation
    # above; otherwise compute them now.
    if "hours" in locals():
        vac_days = vac_map.get(worker, set())
        end_day = sched_day if 'end_day' not in locals() or end_day is None else end_day
        end_hour = sched_hour if 'end_hour' not in locals() or end_hour is None else end_hour
        if end_day == sched_day and end_hour == sched_hour:
            remaining = hours
            day = end_day
            hour = end_hour
            limit = HOURS_LIMITS.get(worker, HOURS_PER_DAY)
            while remaining > 0:
                if day in vac_days:
                    day = next_workday(day)
                    hour = 0
                    continue
                free = limit - hour
                if remaining <= free:
                    hour += remaining
                    remaining = 0
                else:
                    remaining -= free
                    day = next_workday(day)
                    hour = 0
            end_day = day
            end_hour = hour
            if end_hour >= limit:
                end_day = next_workday(end_day)
                end_hour = 0
    actual_worker = worker
    if not actual_worker:
        if part is None and not isinstance(proj['phases'].get(phase), list):
            actual_worker = proj.get('assigned', {}).get(phase)
        else:
            workers = proj.get('segment_workers', {}).get(phase, [])
            idx = part if part is not None else 0
            if idx < len(workers):
                actual_worker = workers[idx]
            if not actual_worker:
                actual_worker = proj.get('assigned', {}).get(phase)

    info = {
        'start_hour': sched_hour,
        'actual_day': sched_day.isoformat(),
        'actual_worker': actual_worker,
        'end_day': end_day.isoformat() if 'end_day' in locals() else sched_day.isoformat(),
        'end_hour': end_hour if 'end_hour' in locals() else sched_hour,
        'affected': track or [],
    }

    return sched_day.isoformat(), warning, info


def get_projects():
    projects = load_projects()
    changed = False
    color_index = 0
    latest_kanban_meta = latest_kanban_card_meta()
    for p in projects:
        if p.get('source') == 'api':
            kanban_id = str(p.get('kanban_id') or '').strip()
            meta = latest_kanban_meta.get(kanban_id) if kanban_id else None
            if meta:
                latest_column = str(meta.get('column') or '').strip()
                if latest_column and p.get('kanban_column') != latest_column:
                    p['kanban_column'] = latest_column
                    changed = True
                latest_lane = str(meta.get('lane') or '').strip()
                if latest_lane and p.get('kanban_lane') != latest_lane:
                    p['kanban_lane'] = latest_lane
                    changed = True
                latest_archived = bool(meta.get('is_archived'))
                if p.get('kanban_archived') != latest_archived:
                    p['kanban_archived'] = latest_archived
                    changed = True

        if p.get('source') == 'api':
            color = p.get('color')
            if not color or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                p['color'] = _next_api_color()
                changed = True
        else:
            if not p.get('color') or p.get('color') not in COLORS:
                p['color'] = COLORS[color_index % len(COLORS)]
                color_index += 1
                changed = True

        p.pop('frozen', None)
        p.setdefault('frozen_tasks', [])
        p.setdefault('blocked', False)
        p.setdefault('material_confirmed_date', '')
        p.setdefault('kanban_attachments', [])
        if not isinstance(p.get('kanban_display_fields'), dict):
            p['kanban_display_fields'] = {}
            changed = True
        column_value = p.get('kanban_column')
        if column_value is None:
            p['kanban_column'] = ''
            changed = True
        elif not isinstance(column_value, str):
            p['kanban_column'] = str(column_value)
            changed = True
        archived_value = p.get('kanban_archived')
        if isinstance(archived_value, bool):
            pass
        elif isinstance(archived_value, str):
            normalized = archived_value.strip().lower() in (
                'true', '1', 'yes', 'si', 'sí'
            )
            p['kanban_archived'] = normalized
            changed = True
        else:
            normalized = bool(archived_value)
            if archived_value is None:
                normalized = False
            p['kanban_archived'] = normalized
            changed = True
        p.setdefault('observations', '')
        p.setdefault('due_confirmed', False)
        p.setdefault('due_warning', False)
        if 'kanban_image' in p and not p['kanban_attachments']:
            old = p.pop('kanban_image')
            if isinstance(old, str) and old:
                p['kanban_attachments'] = [{'name': old, 'url': old}]
        for att in p['kanban_attachments']:
            url = att.get('url', '')
            if url and (url.startswith('/') or not re.match(r'https?://', url)):
                att['url'] = f"{KANBANIZE_BASE_URL.rstrip('/')}/{url.lstrip('/')}"
        if 'source' not in p:
            p['source'] = 'manual'

        if not p.get('planned', True):
            today_str = local_today().isoformat()
            if p.get('start_date') != today_str:
                p['start_date'] = today_str
                changed = True

        phases = p.get('phases') or {}
        segs = p.get('segment_starts')
        seg_hours_map = p.get('segment_start_hours')
        seg_workers_map = p.get('segment_workers')
        for ph, val in list(phases.items()):
            if isinstance(val, list):
                phases[ph] = _phase_total_hours(val)
                changed = True
                if segs is not None:
                    segs.pop(ph, None)
                if seg_hours_map is not None:
                    seg_hours_map.pop(ph, None)
                if seg_workers_map is not None:
                    seg_workers_map.pop(ph, None)
        if segs:
            for ph, val in list(segs.items()):
                if val and not isinstance(val, list):
                    segs[ph] = [val]
        if segs is not None and not segs:
            p.pop('segment_starts', None)
            changed = True
        if seg_hours_map is not None and not seg_hours_map:
            p.pop('segment_start_hours', None)
            changed = True
        if seg_workers_map is not None and not seg_workers_map:
            p.pop('segment_workers', None)
            changed = True

        assigned = p.setdefault('assigned', {})

        # Update planned flag based on assigned workers
        if any(w != UNPLANNED for w in assigned.values()):
            if not p.get('planned', False):
                p['planned'] = True
                changed = True
        else:
            if p.get('planned', False):
                p['planned'] = False
                changed = True

        missing = [ph for ph in p['phases'] if ph not in p['assigned']]
        for ph in missing:
            p['assigned'][ph] = UNPLANNED
            changed = True
        total = len(p.get('phases', {}))
        planned = sum(
            1
            for ph in p.get('phases', {})
            if p['assigned'].get(ph) and p['assigned'][ph] != UNPLANNED
        )
        if planned == 0:
            p['plan_state'] = 'none'
        elif planned == total:
            p['plan_state'] = 'all'
        else:
            p['plan_state'] = 'partial'
    if archive_ready_to_archive_projects_if_due(projects, today=local_today()):
        changed = True
    if changed:
        save_projects(projects)
    prune_orphan_uploads(projects)
    return projects


def _phase_value_has_hours(value):
    """Return ``True`` if *value* represents a positive amount of hours."""

    if isinstance(value, list):
        return any(_phase_value_has_hours(v) for v in value)
    if isinstance(value, dict):
        return any(_phase_value_has_hours(v) for v in value.values())
    if isinstance(value, bool):
        return bool(value)
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def project_has_hours(project):
    """Return ``True`` if any project phase has a positive number of hours."""

    phases = project.get('phases') or {}
    return any(_phase_value_has_hours(v) for v in phases.values())


def _strip_optional_phases(projects, include_optional=True):
    if include_optional:
        return projects
    cleaned = []
    for p in projects:
        p_copy = copy.deepcopy(p)
        phases = p_copy.get('phases') or {}
        kept_phases = {
            ph: val for ph, val in phases.items() if phase_base(ph) not in OPTIONAL_PHASES
        }
        p_copy['phases'] = kept_phases
        assigned = p_copy.get('assigned') or {}
        p_copy['assigned'] = {ph: worker for ph, worker in assigned.items() if ph in kept_phases}
        auto = p_copy.get('auto_hours') or {}
        p_copy['auto_hours'] = {ph: flag for ph, flag in auto.items() if ph in kept_phases}
        seq = p_copy.get('phase_sequence')
        if seq:
            p_copy['phase_sequence'] = [ph for ph in seq if ph in kept_phases]
        frozen_tasks = p_copy.get('frozen_tasks') or []
        p_copy['frozen_tasks'] = [
            t for t in frozen_tasks if phase_base(t.get('phase')) not in OPTIONAL_PHASES
        ]
        for key in ('segment_starts', 'segment_start_hours', 'segment_workers'):
            segs = p_copy.get(key)
            if segs:
                p_copy[key] = {ph: val for ph, val in segs.items() if ph in kept_phases}
        cleaned.append(p_copy)
    return cleaned


def filter_visible_projects(projects):
    """Filter *projects* down to those with at least one phase with hours."""

    visible = []
    for p in projects:
        if not project_has_hours(p):
            continue
        if p.get('kanban_archived'):
            continue
        visible.append(p)
    return visible


def get_visible_projects(include_optional_phases=None):
    """Return the list of projects that should appear in the UI tabs."""

    if include_optional_phases is None:
        include_optional_phases = optional_phases_enabled()
    projects = get_projects()
    projects = _strip_optional_phases(projects, include_optional=include_optional_phases)
    return filter_visible_projects(projects)


def expand_for_display(projects):
    """Return a list of project rows including extra ones for split phases."""
    rows = []
    for p in projects:
        base = copy.deepcopy(p)
        extras = []
        for ph, val in p.get('phases', {}).items():
            if isinstance(val, list) and len(val) > 1:
                base['phases'][ph] = val[0]
                for seg in val[1:]:
                    extra = copy.deepcopy(p)
                    extra['phases'] = {ph: seg}
                    if p.get('assigned'):
                        extra['assigned'] = {ph: p['assigned'].get(ph)}
                    else:
                        extra['assigned'] = {}
                    extras.append(extra)
        rows.append(base)
        rows.extend(extras)
    return rows


def split_markers(schedule):
    """Return set of tuples identifying split boundaries."""
    parts = {}
    for worker, days in schedule.items():
        for day, tasks in days.items():
            for t in tasks:
                if t.get('part') is None:
                    continue
                key = (t['pid'], t['phase'], t['part'])
                parts.setdefault(key, []).append(date.fromisoformat(day))
    starts = set()
    ends = set()
    grouped = {}
    for (pid, phase, part), days in parts.items():
        days.sort()
        grouped.setdefault((pid, phase), {})[part] = days
    for (pid, phase), segs in grouped.items():
        for idx, lst in segs.items():
            if idx > 0 and lst:
                starts.add(f"{pid}|{phase}|{lst[0].isoformat()}")
            if lst:
                ends.add(f"{pid}|{phase}|{lst[-1].isoformat()}")
    return starts.union(ends)


def _kanban_card_to_project(card):
    """Convert a Kanbanize card payload into a project dict."""
    fields_raw = card.get('customFields') or card.get('customfields')
    fields_raw = _decode_json(fields_raw) or {}
    if isinstance(fields_raw, list):
        fields = {f.get('name'): f.get('value') for f in fields_raw if isinstance(f, dict)}
    elif isinstance(fields_raw, dict):
        fields = dict(fields_raw)
    else:
        fields = {}
    fields.setdefault('CALDERERIA', fields.get('CALDERERÍA'))
    popup_raw = {field: fields.get(field) for field in KANBAN_POPUP_FIELDS}
    for k in ['Horas', 'MATERIAL', 'CALDERERIA', 'CALDERERÍA']:
        fields.pop(k, None)

    project_name = (
        card.get('customCardId')
        or fields.get('ID personalizado de tarjeta')
        or fields.get('ID personalizado')
        or card.get('customId')
        or card.get('taskid')
    )
    if not project_name:
        return None
    client = card.get('title', '')
    due = parse_input_date(fields.get('Fecha Cliente') or fields.get('Fecha cliente'))

    def h(name):
        try:
            return int(fields.get(name, 0))
        except Exception:
            return 0

    prep = h('Horas Preparación')
    mont = h('Horas Montaje')
    sold2 = h('Horas Soldadura 2º') or h('Horas Soldadura 2°')
    sold = h('Horas Soldadura')
    pint = h('Horas Acabado')
    mont2 = h('Horas Montaje 2º') or h('Horas Montaje 2°')

    def _bool_flag(value):
        if isinstance(value, str):
            return value.strip().lower() not in ('', '0', 'false', 'no')
        return bool(value)

    mecan_flag = _bool_flag(popup_raw.get('MECANIZADO'))
    trat_flag = _bool_flag(popup_raw.get('TRATAMIENTO'))

    phases = {}
    auto_hours = {}
    if (
        prep <= 0
        and mont <= 0
        and sold2 <= 0
        and sold <= 0
        and pint <= 0
        and mont2 <= 0
    ):
        phases['preparar material'] = 1
        auto_hours['preparar material'] = True
    else:
        if prep > 0:
            phases['preparar material'] = prep
        if mont:
            phases['montar'] = mont
        if sold2:
            phases['soldar 2º'] = sold2
        if pint:
            phases['pintar'] = pint
        if mont2:
            phases['montar 2º'] = mont2
        if sold:
            phases['soldar'] = sold

    def _clean_display_value(value):
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, bool):
            return 'Sí' if value else None
        if isinstance(value, (int, float)):
            return str(value) if value != 0 else None
        text = str(value).strip()
        return text or None

    display_fields = {}
    fecha_cliente_display = _clean_display_value(
        popup_raw.get('Fecha cliente') or popup_raw.get('Fecha Cliente')
    )
    if fecha_cliente_display:
        display_fields['Fecha cliente'] = fecha_cliente_display
    for field in ('LANZAMIENTO', 'MATERIAL', 'CALDERERIA', 'PINTADO'):
        cleaned = _clean_display_value(popup_raw.get(field))
        if cleaned:
            display_fields[field] = cleaned
    if mecan_flag:
        display_fields['MECANIZADO'] = _clean_display_value(popup_raw.get('MECANIZADO')) or 'Sí'
    if trat_flag:
        display_fields['TRATAMIENTO'] = _clean_display_value(popup_raw.get('TRATAMIENTO')) or 'Sí'

    kanban_hour_fields = [
        ('Horas Preparación', prep),
        ('Horas Montaje', mont),
        ('Horas Soldadura', sold),
        ('Horas Montaje 2º', mont2),
        ('Horas Soldadura 2º', sold2),
        ('Horas Acabado', pint),
    ]
    for label, value in kanban_hour_fields:
        if isinstance(value, (int, float)) and int(value) != 0:
            display_fields[label] = str(int(value))

    temp_project = {'phases': phases, 'assigned': {ph: UNPLANNED for ph in phases}}
    _ensure_default_initial_phases(temp_project)
    phases = temp_project['phases']

    project = {
        'id': str(uuid.uuid4()),
        'name': project_name,
        'client': client,
        'start_date': local_today().isoformat(),
        'due_date': due.isoformat() if due else '',
        'color': None,
        'phases': phases,
        # Ensure each phase is explicitly set to the unplanned worker so the
        # calendar always displays the tasks as soon as the project is created.
        'assigned': {ph: UNPLANNED for ph in phases},
        'auto_hours': auto_hours,
        'image': None,
        'kanban_attachments': [],
        'kanban_display_fields': display_fields,
        'planned': False,
        'source': 'api',
    }
    return project


def _normalize_card_id(value):
    """Return a trimmed string identifier for a Kanban card."""

    if value in (None, ''):
        return ''
    text = str(value).strip()
    return text or ''


def _get_phase_source(project, phase):
    if not isinstance(project, dict):
        return None
    sources = project.get('phase_sources')
    if not isinstance(sources, dict):
        return None
    return sources.get(phase)


def _set_phase_source(project, phase, source):
    if not isinstance(project, dict):
        return
    sources = project.get('phase_sources')
    if not isinstance(sources, dict):
        sources = {}
        project['phase_sources'] = sources
    sources[phase] = source


def _iter_card_link_entries(card):
    """Yield dictionaries describing cards linked to *card*."""

    if not isinstance(card, dict):
        return

    links = card.get('links')
    if isinstance(links, dict):
        values = links.values()
    elif isinstance(links, list):
        values = links
    else:
        return

    for group in values:
        if isinstance(group, dict):
            group_values = group.values()
        else:
            group_values = group
        if not isinstance(group_values, (list, tuple, set)):
            group_values = [group_values]
        for entry in group_values:
            if isinstance(entry, dict):
                yield entry


def refresh_kanban_card_cache():
    """Rebuild the local Kanban card cache by calling the Kanbanize API."""

    projects = load_projects() or []
    initial_ids = []
    for project in projects:
        card_id = (
            project.get('kanban_id')
            or project.get('kanbanId')
            or project.get('kanban_card_id')
        )
        normalized = _normalize_card_id(card_id)
        if normalized:
            initial_ids.append(normalized)

    if not initial_ids:
        return []

    seen = set()
    queue = list(dict.fromkeys(initial_ids))
    refreshed = []

    while queue:
        current_id = queue.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        card = _fetch_kanban_card(current_id, with_links=True, force=True)
        if not card:
            continue

        refreshed.append(
            {
                'timestamp': local_now().isoformat(),
                'card': card,
                'stored_title_date': None,
                'previous_title_date': None,
            }
        )

        for entry in _iter_card_link_entries(card):
            linked_id = (
                entry.get('taskid')
                or entry.get('cardId')
                or entry.get('id')
                or entry.get('linkedCardId')
                or entry.get('linkedcardid')
            )
            normalized_link = _normalize_card_id(linked_id)
            if normalized_link and normalized_link not in seen:
                queue.append(normalized_link)

    if refreshed:
        save_kanban_cards(refreshed)
    return refreshed


def _normalize_board_token(board_id):
    """Return the board identifier to use in Kanbanize API requests."""

    if board_id in (None, ""):
        return str(KANBANIZE_BOARD_TOKEN)
    return str(board_id)


def _fetch_kanban_card(card_id, with_links=False, force=False, board_id=None):
    """Retrieve card details from Kanbanize via the REST API.

    A short-lived in-memory cache limits how often the same card is requested
    from Kanbanize.  Pass ``force=True`` to bypass the cooldown and issue a new
    request immediately.
    """

    normalized_id = _normalize_card_id(card_id)
    if not normalized_id:
        return None

    board_token = _normalize_board_token(board_id)
    cache_key = (board_token, normalized_id, bool(with_links))
    now = time.monotonic()

    if not force:
        cached = _KANBAN_CARD_FETCH_CACHE.get(cache_key)
        if cached:
            timestamp, payload = cached
            if now - timestamp < KANBAN_CARD_FETCH_COOLDOWN_SECONDS:
                return copy.deepcopy(payload)
        if not with_links:
            cached = _KANBAN_CARD_FETCH_CACHE.get((board_token, normalized_id, True))
            if cached:
                timestamp, payload = cached
                if now - timestamp < KANBAN_CARD_FETCH_COOLDOWN_SECONDS:
                    return copy.deepcopy(payload)

    url = f"{KANBANIZE_BASE_URL}/api/v2/boards/{board_token}/cards/{normalized_id}"
    if with_links:
        url += "?withLinks=1"
    req = Request(url, headers={'apikey': KANBANIZE_API_KEY})
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.load(resp)
                if isinstance(data, dict):
                    payload = data.get('data') or data
                    if isinstance(payload, dict):
                        stored = copy.deepcopy(payload)
                        fetched_at = time.monotonic()
                        _KANBAN_CARD_FETCH_CACHE[cache_key] = (fetched_at, stored)
                        if with_links:
                            _KANBAN_CARD_FETCH_CACHE[(board_token, normalized_id, False)] = (
                                fetched_at,
                                stored,
                            )
                        return copy.deepcopy(stored)
    except HTTPError as e:
        body = None
        try:
            body = e.read().decode(errors='replace')
        except Exception:
            body = None
        print(
            'Kanbanize API HTTPError:',
            f"status={getattr(e, 'code', '?')}",
            f"reason={getattr(e, 'reason', '?')}",
            f"url={getattr(e, 'url', '?')}",
        )
        if e.headers:
            filtered_headers = {
                k: v for k, v in e.headers.items() if k.lower() != 'content-security-policy'
            }
            if filtered_headers:
                formatted_headers = "\n".join(f"{k}: {v}" for k, v in filtered_headers.items())
                print('Headers:', formatted_headers)
        if body:
            print('Body:', body)
    except Exception as e:
        print('Kanbanize API error:', e)
    return None


def _card_is_archived(card):
    if not isinstance(card, dict):
        return False
    for key in ('isArchived', 'IsArchived', 'archived', 'Archived', 'is_archived'):
        if key in card:
            return bool(card.get(key))
    return False


def _normalize_kanban_card_payload(card):
    if not isinstance(card, dict):
        return {}
    normalized = dict(card)
    card_id = (
        normalized.get('taskid')
        or normalized.get('cardId')
        or normalized.get('id')
        or normalized.get('card_id')
        or normalized.get('cardid')
    )
    if card_id not in (None, ''):
        normalized.setdefault('taskid', card_id)
        normalized.setdefault('cardId', card_id)
        normalized.setdefault('id', card_id)

    lane_id = normalized.get('laneid') or normalized.get('laneId') or normalized.get('lane_id')
    lane_name = (
        normalized.get('lanename') or normalized.get('laneName') or normalized.get('lane_name')
    )
    if lane_id not in (None, ''):
        normalized.setdefault('laneid', lane_id)
        normalized.setdefault('laneId', lane_id)
    if lane_name:
        normalized.setdefault('lanename', lane_name)
        normalized.setdefault('laneName', lane_name)

    column_id = (
        normalized.get('columnid') or normalized.get('columnId') or normalized.get('column_id')
    )
    column_name = (
        normalized.get('columnname') or normalized.get('columnName') or normalized.get('column_name')
    )
    if column_id not in (None, ''):
        normalized.setdefault('columnid', column_id)
        normalized.setdefault('columnId', column_id)
    if column_name:
        normalized.setdefault('columnname', column_name)
        normalized.setdefault('columnName', column_name)

    return normalized


def _fetch_kanban_cards_page(board_id, page, page_size):
    board_token = _normalize_board_token(board_id)
    params = urllib.parse.urlencode(
        {
            'page': page,
            'per_page': page_size,
            'archived': 'false',
        }
    )
    url = f"{KANBANIZE_BASE_URL}/api/v2/boards/{board_token}/cards?{params}"
    req = Request(url, headers={'apikey': KANBANIZE_API_KEY})
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return json.load(resp)
    except HTTPError as e:
        body = None
        try:
            body = e.read().decode(errors='replace')
        except Exception:
            body = None
        print(
            'Kanbanize API HTTPError:',
            f"status={getattr(e, 'code', '?')}",
            f"reason={getattr(e, 'reason', '?')}",
            f"url={getattr(e, 'url', '?')}",
        )
        if e.headers:
            filtered_headers = {
                k: v for k, v in e.headers.items() if k.lower() != 'content-security-policy'
            }
            if filtered_headers:
                formatted_headers = "\n".join(f"{k}: {v}" for k, v in filtered_headers.items())
                print('Headers:', formatted_headers)
        if body:
            print('Body:', body)
    except Exception as e:
        print('Kanbanize API error:', e)
    return None


def _fetch_all_kanban_cards(board_id=None):
    page = 1
    page_size = 200
    cards = []
    while True:
        payload = _fetch_kanban_cards_page(board_id, page, page_size)
        if payload is None:
            break

        data = payload.get('data') if isinstance(payload, dict) else payload
        pagination = payload.get('pagination') if isinstance(payload, dict) else None
        page_cards = None
        if isinstance(data, dict):
            page_cards = data.get('cards') or data.get('data')
            if pagination is None:
                pagination = data.get('pagination')
        elif isinstance(data, list):
            page_cards = data

        if not page_cards:
            break
        if isinstance(page_cards, dict):
            page_cards = list(page_cards.values())
        if isinstance(page_cards, list):
            cards.extend(page_cards)

        total_pages = None
        current_page = None
        if isinstance(pagination, dict):
            total_pages = (
                pagination.get('totalPages')
                or pagination.get('total_pages')
                or pagination.get('pages')
            )
            current_page = (
                pagination.get('page')
                or pagination.get('currentPage')
                or pagination.get('current_page')
            )
        if total_pages and current_page and int(current_page) >= int(total_pages):
            break
        if len(page_cards) < page_size and not total_pages:
            break
        page += 1
    return [card for card in cards if not _card_is_archived(card)]


def rebuild_kanban_cache(board_id=None):
    with KANBAN_CARDS_LOCK:
        cards = _fetch_all_kanban_cards(board_id=board_id)
        timestamp = local_now().isoformat()
        card_index = {}
        for card in cards:
            normalized = _normalize_kanban_card_payload(card)
            card_id = (
                normalized.get('taskid')
                or normalized.get('cardId')
                or normalized.get('id')
            )
            normalized_id = _normalize_card_id(card_id)
            if not normalized_id:
                continue
            entry = {
                'timestamp': timestamp,
                'card': normalized,
                'stored_title_date': None,
                'previous_title_date': None,
            }
            column = (
                normalized.get('columnname')
                or normalized.get('columnName')
                or normalized.get('column')
            )
            if isinstance(column, str):
                column = column.strip()
            elif column:
                column = str(column).strip()
            else:
                column = ''
            if column:
                entry['last_column'] = column
            card_index[normalized_id] = entry
        save_kanban_cards(list(card_index.values()))
        return card_index


@app.route('/')
def home():
    """Redirect to the combined view by default."""
    return redirect(url_for('complete'))


@app.route('/calendar')
def calendar_view():
    include_optional_phases = optional_phases_enabled()
    projects = get_projects()
    projects = _strip_optional_phases(projects, include_optional=include_optional_phases)
    projects = [p for p in projects if project_has_hours(p) and not p.get('kanban_archived')]
    if archive_ready_to_archive_projects_if_due(projects, today=local_today()):
        save_projects(projects)
    schedule, conflicts, _archived_entries, archived_project_map = build_schedule_with_archived(
        projects, include_optional_phases=include_optional_phases
    )
    annotate_schedule_frozen_background(schedule)
    today = local_today()
    worker_notes_raw = load_worker_notes()
    manual_entries = load_manual_bucket_entries()
    if not include_optional_phases:
        manual_entries = [
            entry for entry in manual_entries if phase_base(entry.get('phase')) not in OPTIONAL_PHASES
        ]
    unplanned_raw = []
    if UNPLANNED in schedule:
        for day, tasks in schedule.pop(UNPLANNED).items():
            for t in tasks:
                item = t.copy()
                item['day'] = day
                unplanned_raw.append(item)
    groups = {}
    for item in unplanned_raw:
        pid = item['pid']
        phase = item['phase']
        part = item.get('part')
        proj = groups.setdefault(
            pid,
            {
                'project': item['project'],
                'client': item['client'],
                'material_date': item.get('material_date'),
                'due_date': item.get('due_date'),
                'phases': {},
            },
        )
        phase_key = (phase, part) if part is not None else (phase, None)
        ph = proj['phases'].setdefault(
            phase_key,
            {
                'project': item['project'],
                'client': item['client'],
                'pid': pid,
                'phase': phase,
                'part': part,
                'color': item.get('color'),
                'due_date': item.get('due_date'),
                'start_date': item.get('start_date'),
                'day': item.get('day'),
                'hours': 0,
                'late': item.get('late', False),
                'due_status': item.get('due_status'),
                'blocked': item.get('blocked', False),
                'frozen': item.get('frozen', False),
                'frozen_background': item.get('frozen_background'),
                'auto': item.get('auto', False),
            },
        )
        ph['hours'] += item.get('hours', 0)
        if item.get('day') and (ph['day'] is None or item['day'] < ph['day']):
            ph['day'] = item['day']
        if item.get('start_date') and (
            ph['start_date'] is None or item['start_date'] < ph['start_date']
        ):
            ph['start_date'] = item['start_date']
        if item.get('due_date') and (
            ph['due_date'] is None or item['due_date'] < ph['due_date']
        ):
            ph['due_date'] = item['due_date']
        if item.get('late'):
            ph['late'] = True
        if item.get('blocked'):
            ph['blocked'] = True
        if item.get('frozen'):
            ph['frozen'] = True
            bg = item.get('frozen_background')
            if bg:
                ph['frozen_background'] = bg
        if item.get('auto'):
            ph['auto'] = True
    unplanned_list = []
    for pid, data in groups.items():
        unplanned_list.append(
            {
                'pid': pid,
                'project': data['project'],
                'client': data['client'],
                'material_date': data.get('material_date'),
                'due_date': data.get('due_date'),
                'tasks': list(data['phases'].values()),
            }
        )
    deactivated_workers = _inactive_worker_date_map()
    calendar_workers = planner_calendar_workers(today)
    schedule = _schedule_with_deactivated_rows(schedule, calendar_workers, deactivated_workers)
    for p in projects:
        if p.get('due_date'):
            try:
                p['met'] = date.fromisoformat(p['end_date']) <= date.fromisoformat(p['due_date'])
            except ValueError:
                p['met'] = False
        else:
            p['met'] = False
    notes = load_notes()
    extra = load_extra_conflicts()
    conflicts.extend(extra)
    dismissed = load_dismissed()
    conflicts = [
        c
        for c in conflicts
        if c['key'] not in dismissed and c.get('message') != 'No se cumple la fecha de entrega'
    ]

    text_filter = request.args.get('q', '').strip()
    project_filter = text_filter
    client_filter = ''
    filter_active = bool(text_filter)

    def matches_filters(name, client, phase_name=''):
        if not text_filter:
            return True
        terms = [token.strip().lower() for token in text_filter.split() if token.strip()]
        normalized_phase = phase_base(phase_name or '')
        phase_for_filter = '' if normalized_phase == 'lanzamiento' else (phase_name or '')
        haystack = " ".join([(name or ''), (client or ''), phase_for_filter]).lower()
        return all(term in haystack for term in terms)

    if filter_active:
        for worker, days_data in schedule.items():
            for day, tasks in days_data.items():
                for t in tasks:
                    t['filter_match'] = matches_filters(t['project'], t['client'], t.get('phase'))
        filtered_projects = [
            p for p in projects if matches_filters(p.get('name'), p.get('client'))
        ]
        for g in unplanned_list:
            match = matches_filters(g['project'], g['client'])
            g['filter_match'] = match
            for t in g['tasks']:
                t['filter_match'] = matches_filters(t['project'], t['client'], t.get('phase'))
    else:
        filtered_projects = projects

    # Ensure started phases appear before unstarted ones within each cell
    _sort_cell_tasks(schedule)

    points = split_markers(schedule)
    start = today - timedelta(days=30)
    end = today + timedelta(days=120)
    days, cols, week_spans = build_calendar(start, end)
    hours_map = load_daily_hours()
    worker_day_overrides = load_worker_day_hours()
    worker_limits_map = {
        worker: HOURS_LIMITS.get(worker, HOURS_PER_DAY)
        for worker in WORKERS
    }

    unplanned_list.sort(key=lambda g: g.get('material_date') or '9999-12-31')

    note_map = {}
    for n in notes:
        note_map.setdefault(n['date'], []).append(n['description'])
    worker_note_map = {}
    for w, info in worker_notes_raw.items():
        text = info.get('text', '')
        ts = info.get('edited')
        fmt = ''
        if ts:
            try:
                fmt = datetime.fromisoformat(ts).strftime('%H:%M %d/%m')
            except ValueError:
                fmt = ''
        height_val = None
        raw_height = info.get('height')
        if raw_height is not None:
            try:
                height_val = int(float(raw_height))
            except (TypeError, ValueError):
                height_val = None
        if isinstance(height_val, int) and height_val > 0:
            worker_note_map[w] = {'text': text, 'edited': fmt, 'height': height_val}
        else:
            worker_note_map[w] = {'text': text, 'edited': fmt}
    material_status_map, material_missing_map = compute_material_status_map(
        projects, include_missing_titles=True
    )
    for archived_pid in archived_project_map.keys():
        material_status_map[str(archived_pid)] = 'archived'
        material_missing_map.setdefault(str(archived_pid), [])

    for row in filtered_projects:
        pid = row.get('id')
        if pid is None:
            continue
        row['material_status'] = material_status_map.get(str(pid), 'complete')

    manual_index = {}
    manual_bucket_items = []
    if manual_entries:
        manual_index = {
            (entry['pid'], entry['phase'], entry.get('part')): idx
            for idx, entry in enumerate(manual_entries)
        }
        manual_bucket_items = [None] * len(manual_entries)
        for group in list(unplanned_list):
            remaining_tasks = []
            for task in group['tasks']:
                key = (str(group['pid']), task['phase'], task.get('part'))
                idx = manual_index.get(key)
                if idx is None:
                    remaining_tasks.append(task)
                    continue
                status = material_status_map.get(str(group['pid']), 'complete')
                due_text = task.get('due_date') or group.get('due_date')
                matches_filter = (
                    task.get('filter_match', True)
                    and group.get('filter_match', True)
                )
                manual_bucket_items[idx] = {
                    'pid': group['pid'],
                    'project': group['project'],
                    'client': group['client'],
                    'phase': task['phase'],
                    'part': task.get('part'),
                    'color': task.get('color'),
                    'due_date': due_text,
                    'start_date': task.get('start_date'),
                    'day': task.get('day'),
                    'hours': task.get('hours'),
                    'late': task.get('late', False),
                    'due_status': task.get('due_status'),
                    'phase_deadline_status': task.get('phase_deadline_status'),
                    'blocked': task.get('blocked', False),
                    'frozen': task.get('frozen', False),
                    'frozen_background': task.get('frozen_background'),
                    'auto': task.get('auto', False),
                    'material_status': status,
                    'material_label': material_status_label(status),
                    'material_css': f"material-status-{status}",
                    'filter_match': matches_filter,
                }
            group['tasks'] = remaining_tasks
            if not remaining_tasks:
                unplanned_list.remove(group)
        cleaned_entries = []
        cleaned_bucket = []
        for idx, item in enumerate(manual_bucket_items):
            if item:
                cleaned_bucket.append(item)
                entry = manual_entries[idx]
                new_entry = {'pid': entry['pid'], 'phase': entry['phase']}
                if entry.get('part') is not None:
                    new_entry['part'] = entry['part']
                cleaned_entries.append(new_entry)
        if cleaned_entries != manual_entries:
            save_manual_unplanned(cleaned_entries)
            manual_entries = cleaned_entries
            manual_bucket_items = cleaned_bucket
        else:
            manual_bucket_items = cleaned_bucket
    else:
        manual_bucket_items = []

    unplanned_groups = group_unplanned_by_status(unplanned_list, material_status_map)

    def _due_sort_value(entry):
        due_text = (entry.get('due_date') or '').strip()
        if due_text and due_text != '0':
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try:
                    return datetime.strptime(due_text, fmt).date()
                except ValueError:
                    continue
        return date.max

    unplanned_due = sorted(
        unplanned_list,
        key=lambda item: (
            _due_sort_value(item),
            (item.get('project') or '').lower(),
            (item.get('client') or '').lower(),
        ),
    )
    unplanned_phase_groups = group_unplanned_by_phase(unplanned_due)

    project_map = {}
    for p in projects:
        p.setdefault('kanban_attachments', [])
        p.setdefault('kanban_display_fields', {})
        pid = p.get('id')
        project_entry = {
            **p,
            'frozen_phases': sorted({t['phase'] for t in p.get('frozen_tasks', [])}),
            'phase_sequence': list((p.get('phases') or {}).keys()),
            'kanban_previous_phases': compute_previous_kanban_phases(p.get('kanban_column')),
        }
        if pid:
            pid_key = str(pid)
            project_entry['material_status'] = material_status_map.get(pid_key, 'complete')
            project_entry['material_missing_titles'] = material_missing_map.get(pid_key, [])
            project_map[pid_key] = project_entry
        project_map[p['id']] = project_entry
    for pid, info in archived_project_map.items():
        info.setdefault('kanban_display_fields', {})
        info.setdefault('kanban_attachments', [])
        info['material_status'] = material_status_map.get(str(pid), 'archived')
        info['material_missing_titles'] = material_missing_map.get(str(pid), [])
        project_map[pid] = info
        project_map[str(pid)] = info
    start_map = phase_start_map(projects)
    project_orders = build_project_order_popup_map(projects, today=today)
    complete_project_phases = [ph for ph in PHASE_ORDER if ph not in ['dibujo', 'pedidos']]
    if 'lanzamiento' not in complete_project_phases:
        complete_project_phases.insert(0, 'lanzamiento')

    return render_template(
        'index.html',
        schedule=schedule,
        cols=cols,
        week_spans=week_spans,
        conflicts=conflicts,
        workers=WORKERS,
        today=today,
        project_filter=project_filter,
        client_filter=client_filter,
        filter_active=filter_active,
        notes=note_map,
        project_data=project_map,
        start_map=start_map,
        phases=PHASE_ORDER,
        complete_project_phases=complete_project_phases,
        hours=hours_map,
        split_points=points,
        palette=COLORS,
        unplanned_groups=unplanned_groups,
        unplanned_due=unplanned_due,
        unplanned_phase_groups=unplanned_phase_groups,
        manual_bucket=manual_bucket_items,
        worker_notes=worker_note_map,
        material_status_labels=MATERIAL_STATUS_LABELS,
        worker_day_hours=worker_day_overrides,
        worker_limits=worker_limits_map,
        import_optional_phases=include_optional_phases,
        project_orders=project_orders,
        deactivated_workers=deactivated_workers,
    )


@app.route('/calendario-pedidos')
def calendar_pedidos():
    today = local_today()
    compras_raw, column_colors = load_compras_raw()
    projects = get_visible_projects()
    soldar_lookup = build_phase_finish_lookup(projects, SOLDAR_PHASE_PRIORITY)
    base_links = build_project_links(compras_raw)
    calendar_payload = compute_pedidos_entries(
        compras_raw,
        column_colors,
        today,
        soldar_finish_lookup=soldar_lookup,
    )
    pedidos_calendar = calendar_payload['pedidos']
    subcontr_calendar = calendar_payload['subcontrataciones']
    calendar_titles = calendar_payload['titles']
    calendar_refs = {
        'pedidos': {'ids': set(), 'titles': set()},
        'subcontrataciones': {'ids': set(), 'titles': set()},
    }

    def collect_calendar_refs(calendar_key, entries):
        refs = calendar_refs[calendar_key]
        for entry in entries or []:
            cid = entry.get('cid')
            if cid:
                refs['ids'].add(str(cid))
            title = entry.get('project') or entry.get('title')
            title_key = normalize_key(title)
            if title_key:
                refs['titles'].add(title_key)

    for day_entries in pedidos_calendar['scheduled'].values():
        collect_calendar_refs('pedidos', day_entries)
    collect_calendar_refs('pedidos', pedidos_calendar['unconfirmed'])
    for day_entries in subcontr_calendar['scheduled'].values():
        collect_calendar_refs('subcontrataciones', day_entries)
    collect_calendar_refs('subcontrataciones', subcontr_calendar['unconfirmed'])

    calendar_ids = set().union(*(refs['ids'] for refs in calendar_refs.values()))
    filtered_links = filter_project_links_by_titles(
        base_links, calendar_titles, calendar_ids, kanban_cards=compras_raw
    )
    enriched_links = attach_phase_starts(filtered_links, projects)
    annotate_order_details(enriched_links, today=today)
    links_table = [
        item
        for item in enriched_links
        if normalize_key(item.get('lane')) in COLUMN1_ALLOWED_LANE_KEYS
    ]

    for item in links_table:
        link_titles = list(item.get('links') or [])
        link_ids = list(item.get('link_ids') or [])
        details = list(item.get('link_details') or [])
        item_calendar_keys = set()
        for idx, title in enumerate(link_titles):
            detail = details[idx] if idx < len(details) and isinstance(details[idx], dict) else {}
            cid = ''
            if idx < len(link_ids):
                cid = str(link_ids[idx]).strip() if link_ids[idx] not in (None, '') else ''
            if not cid and detail.get('id') not in (None, ''):
                cid = str(detail.get('id')).strip()
            title_key = normalize_key(
                detail.get('title') or title or item.get('title') or item.get('project')
            )
            detail_calendar_keys = []
            for calendar_key, refs in calendar_refs.items():
                if (cid and cid in refs['ids']) or (title_key and title_key in refs['titles']):
                    detail_calendar_keys.append(calendar_key)
            if detail_calendar_keys:
                if idx >= len(details):
                    details.extend({} for _ in range(idx + 1 - len(details)))
                    detail = details[idx]
                details[idx]['calendar_keys'] = detail_calendar_keys
                item_calendar_keys.update(detail_calendar_keys)
        item['link_details'] = details
        item['calendar_keys'] = sorted(item_calendar_keys)

    info_names = sorted({
        name.strip()
        for name in (item.get('board') for item in links_table)
        if name and name.strip()
    })
    info_title = ' / '.join(info_names)

    weeks = build_calendar_weeks(pedidos_calendar['scheduled'], today)
    subcontr_weeks = build_calendar_weeks(subcontr_calendar['scheduled'], today)

    material_status_map, material_missing_map = compute_material_status_map(
        projects, include_missing_titles=True
    )

    project_map = {}
    for p in projects:
        p.setdefault('kanban_attachments', [])
        p.setdefault('kanban_display_fields', {})
        entry = {
            **p,
            'frozen_phases': sorted({t['phase'] for t in p.get('frozen_tasks', [])}),
            'phase_sequence': list((p.get('phases') or {}).keys()),
            'kanban_previous_phases': compute_previous_kanban_phases(p.get('kanban_column')),
        }
        pid = p.get('id')
        if pid:
            pid_key = str(pid)
            entry['material_status'] = material_status_map.get(pid_key, 'complete')
            entry['material_missing_titles'] = material_missing_map.get(pid_key, [])
            project_map[pid_key] = entry
        project_map[p['id']] = entry
    start_map = phase_start_map(projects)

    return render_template(
        'calendar_pedidos.html',
        weeks=weeks,
        today=today,
        unconfirmed=pedidos_calendar['unconfirmed'],
        subcontr_weeks=subcontr_weeks,
        subcontr_unconfirmed=subcontr_calendar['unconfirmed'],
        project_links=links_table,
        project_data=project_map,
        start_map=start_map,
        phases=PHASE_ORDER,
        project_info_title=info_title,
    )


@app.route('/cronologico')
def cronologico_view():
    projects = get_visible_projects()
    schedule_map, _ = schedule_projects(copy.deepcopy(projects))
    today = local_today()
    week_start = today - timedelta(days=today.weekday())
    week_days = [week_start + timedelta(days=i) for i in range(5)]

    interesting_phases = {'montar', 'soldar', 'pintar', 'mecanizar', 'tratamiento'}
    phase_display = {
        'montar': 'MONTAR',
        'soldar': 'SOLDAR',
        'pintar': 'PINTAR',
        'mecanizar': 'MECANIZAR',
        'tratamiento': 'TRATAMIENTO',
    }
    phase_entries = {}
    for worker, days in schedule_map.items():
        if worker == UNPLANNED:
            continue
        for day_str, tasks in days.items():
            try:
                day_obj = date.fromisoformat(day_str)
            except ValueError:
                continue
            for task in tasks:
                phase = task.get('phase')
                if phase not in interesting_phases:
                    continue
                key = (task.get('pid'), phase, task.get('part'), worker)
                entry = phase_entries.setdefault(
                    key,
                    {
                        'project': task.get('project'),
                        'client': task.get('client'),
                        'worker': worker,
                        'phase': phase,
                        'days': set(),
                    },
                )
                entry['days'].add(day_obj)

    start_templates = {
        'montar': '{worker} inicia la fase MONTAR del proyecto {project}',
        'soldar': '{worker} inicia la fase SOLDAR del proyecto {project}',
        'pintar': '{worker} inicia la fase PINTAR del proyecto {project}',
        'mecanizar': 'Llevar {project} a mecanizar.',
        'tratamiento': 'Llevar {project} a tratamiento.',
    }
    finish_templates = {
        'montar': '{worker} termina la fase MONTAR del proyecto {project}',
        'soldar': '{worker} termina la fase SOLDAR del proyecto {project}',
        'mecanizar': 'Recepcionar {project} del mecanizado.',
        'tratamiento': 'Recepcionar {project} del tratamiento.',
    }

    events_by_day = {d.isoformat(): [] for d in week_days}
    previews_by_day = {d.isoformat(): [] for d in week_days}
    preview_seen = {d.isoformat(): set() for d in week_days}
    project_lookup = {p.get('id'): p for p in projects}
    for worker, days in schedule_map.items():
        if worker == UNPLANNED:
            continue
        for day_key, tasks in days.items():
            if day_key not in previews_by_day:
                continue
            for task in tasks:
                pid = task.get('pid')
                if not pid:
                    continue
                project = project_lookup.get(pid)
                if not project:
                    continue
                image_path = project.get('image')
                if not image_path or pid in preview_seen[day_key]:
                    continue
                previews_by_day[day_key].append(
                    {
                        'project': project.get('name') or 'Sin nombre',
                        'client': project.get('client') or '',
                        'image': image_path,
                    }
                )
                preview_seen[day_key].add(pid)

    same_day_completions = {}
    for entry in phase_entries.values():
        days = sorted(entry['days'])
        if not days:
            continue
        start_day = days[0]
        end_day = days[-1]
        project_name = entry['project'] or 'Sin nombre'
        client_name = entry.get('client') or 'Sin cliente'
        worker_name = entry['worker'] or UNPLANNED
        if worker_name == UNPLANNED:
            continue
        phase = entry['phase']

        if start_day == end_day:
            key = (
                start_day.isoformat(),
                worker_name,
                project_name,
                client_name,
            )
            phases = same_day_completions.setdefault(key, [])
            phases.append(phase)
            continue

        start_template = start_templates.get(phase)
        day_key = start_day.isoformat()
        if start_template and day_key in events_by_day:
            events_by_day[day_key].append((0, start_template.format(worker=worker_name, project=project_name)))

        finish_template = finish_templates.get(phase)
        finish_key = end_day.isoformat()
        if finish_template and finish_key in events_by_day:
            events_by_day[finish_key].append((1, finish_template.format(worker=worker_name, project=project_name)))

    for key, phases in same_day_completions.items():
        day_key, worker_name, project_name, client_name = key
        if day_key not in events_by_day:
            continue
        unique_phases = []
        seen_phases = set()
        for phase in phases:
            if phase in seen_phases:
                continue
            seen_phases.add(phase)
            label = phase_display.get(phase, phase.upper())
            unique_phases.append(label)
        if not unique_phases:
            continue
        if len(unique_phases) == 1:
            message = (
                f"{worker_name} Inicia y termina la fase {unique_phases[0]} "
                f"de {project_name} - {client_name}"
            )
        else:
            if len(unique_phases) == 2:
                phases_text = f"{unique_phases[0]} y {unique_phases[1]}"
            else:
                phases_text = ", ".join(unique_phases[:-1]) + f" y {unique_phases[-1]}"
            message = (
                f"{worker_name} Inicia y termina las fases {phases_text} "
                f"de {project_name} - {client_name}"
            )
        events_by_day[day_key].append((0, message))

    for day_key, messages in events_by_day.items():
        messages.sort(key=lambda item: (item[0], item[1]))
        deduped = []
        seen = set()
        for _, message in messages:
            if message in seen:
                continue
            seen.add(message)
            deduped.append(message)
        events_by_day[day_key] = deduped

    for day_key, previews in previews_by_day.items():
        previews.sort(key=lambda item: item['project'])

    return render_template(
        'cronologico.html',
        week_days=week_days,
        events_by_day=events_by_day,
        previews_by_day=previews_by_day,
        today=today,
    )


@app.route('/project_links')
def project_links_api():
    today = local_today()
    compras_raw, column_colors = load_compras_raw()
    projects = get_visible_projects()
    soldar_lookup = build_phase_finish_lookup(projects, SOLDAR_PHASE_PRIORITY)
    base_links = build_project_links(compras_raw)
    calendar_payload = compute_pedidos_entries(
        compras_raw,
        column_colors,
        today,
        soldar_finish_lookup=soldar_lookup,
    )
    calendar_titles = calendar_payload['titles']
    pedidos_calendar = calendar_payload['pedidos']
    subcontr_calendar = calendar_payload['subcontrataciones']
    calendar_ids = set()
    for bucket in (
        pedidos_calendar['scheduled'].values(),
        subcontr_calendar['scheduled'].values(),
    ):
        for entries in bucket:
            for item in entries:
                cid = item.get('cid')
                if cid:
                    calendar_ids.add(str(cid))
    for entry in chain(
        pedidos_calendar['unconfirmed'], subcontr_calendar['unconfirmed']
    ):
        cid = entry.get('cid')
        if cid:
            calendar_ids.add(str(cid))
    filtered_links = filter_project_links_by_titles(
        base_links, calendar_titles, calendar_ids, kanban_cards=compras_raw
    )
    enriched_links = attach_phase_starts(filtered_links, projects)
    annotate_order_details(enriched_links, today=today)
    lane_filtered = [
        item
        for item in enriched_links
        if normalize_key(item.get('lane')) in COLUMN1_ALLOWED_LANE_KEYS
    ]
    return jsonify(lane_filtered)


@app.route('/orden-carpetas')
@app.route('/pndt-verificacion')
def orden_carpetas_view():
    projects = get_visible_projects()
    project_lookup = {}
    planned_starts = {}
    project_workers = {}

    for project in projects:
        pid = project.get('id')
        if not pid:
            continue
        pid_str = str(pid)
        project_lookup[pid_str] = project
        planned_starts[pid_str] = None
        project_workers[pid_str] = set()

    scheduled_projects = copy.deepcopy(projects)
    schedule_data, _ = schedule_projects(scheduled_projects)

    for worker, days in schedule_data.items():
        for tasks in days.values():
            for task in tasks:
                pid = task.get('pid')
                if not pid:
                    continue
                pid_str = str(pid)
                project_workers.setdefault(pid_str, set())
                if worker:
                    project_workers[pid_str].add(worker)
                task_worker = task.get('worker')
                if task_worker and task_worker != worker:
                    project_workers[pid_str].add(task_worker)
                if worker == UNPLANNED or task_worker == UNPLANNED:
                    continue
                start_day = _safe_iso_date(task.get('start_time'))
                if not start_day:
                    continue
                current = planned_starts.get(pid_str)
                if current is None or start_day < current:
                    planned_starts[pid_str] = start_day
                else:
                    planned_starts.setdefault(pid_str, start_day)
    all_unplanned_projects = {}
    for pid_str, workers in project_workers.items():
        cleaned = {w for w in workers if w}
        all_unplanned_projects[pid_str] = bool(cleaned) and all(
            w == UNPLANNED for w in cleaned
        )

    project_key_map = {}
    for project in projects:
        pid = project.get('id')
        if not pid:
            continue
        pid_str = str(pid)
        name = (project.get('name') or '').strip()
        if name:
            norm = normalize_key(name)
            if norm:
                project_key_map.setdefault(norm, pid_str)
            split_name, _ = split_project_and_client(name)
            if split_name:
                norm_split = normalize_key(split_name)
                if norm_split:
                    project_key_map.setdefault(norm_split, pid_str)
            code_match = PROJECT_TITLE_PATTERN.search(name)
            if code_match:
                code_key = normalize_key(code_match.group(0))
                if code_key:
                    project_key_map.setdefault(code_key, pid_str)
        custom = (project.get('custom_card_id') or '').strip()
        if custom:
            norm_custom = normalize_key(custom)
            if norm_custom:
                project_key_map.setdefault(norm_custom, pid_str)
            code_match = PROJECT_TITLE_PATTERN.search(custom)
            if code_match:
                code_key = normalize_key(code_match.group(0))
                if code_key:
                    project_key_map.setdefault(code_key, pid_str)

    compras_raw, _ = load_compras_raw()
    links_table = attach_phase_starts(build_project_links(compras_raw), projects)

    target_column_keys = {
        normalize_key('Pdte. Verificación'),
        normalize_key('Pendiente Verificación'),
        normalize_key('Pendiente de Verificación'),
    }
    ready_to_archive_key = normalize_key('Ready to Archive')

    def _project_is_ready_to_archive(pid_text='', title_text=''):
        pid_key = str(pid_text or '').strip()
        if pid_key:
            project = project_lookup.get(pid_key)
            if project and normalize_key(project.get('kanban_column')) == ready_to_archive_key:
                return True
        if title_text:
            mapped_pid = project_key_map.get(normalize_key(title_text))
            if mapped_pid:
                project = project_lookup.get(str(mapped_pid))
                if project and normalize_key(project.get('kanban_column')) == ready_to_archive_key:
                    return True
        return False

    pending_rows = []
    pending_seen = set()
    montaje_keys = {normalize_key('Listo para iniciar')}
    montaje_rows = {}

    for entry in links_table:
        pid_value = entry.get('pid')
        pid_str = str(pid_value) if pid_value not in (None, '') else ''
        if not pid_str:
            for field in ('project', 'title', 'display_title', 'custom_card_id'):
                candidate = entry.get(field)
                norm_candidate = normalize_key(candidate)
                if norm_candidate and norm_candidate in project_key_map:
                    pid_str = project_key_map[norm_candidate]
                    break
        def ensure_montaje_row():
            project = project_lookup.get(pid_str)
            project_title = ''
            project_description = ''
            if project:
                project_title = (project.get('name') or '').strip()
                project_description = (project.get('client') or '').strip()
            if not project_title:
                fallback = (
                    entry.get('project')
                    or entry.get('title')
                    or entry.get('display_title')
                    or ''
                )
                project_title = fallback.strip()
            if not project_description:
                project_description = (entry.get('client') or '').strip()
            if _project_is_ready_to_archive(pid_str, project_title):
                return
            key = pid_str or normalize_key(project_title)
            if not key or key in montaje_rows:
                return
            planned_date = None
            if pid_str and not all_unplanned_projects.get(pid_str, False):
                planned_date = planned_starts.get(pid_str)
            display_date = ''
            if isinstance(planned_date, date):
                display_date = planned_date.strftime('%d/%m/%Y')
            montaje_rows[key] = {
                'project_title': project_title,
                'project_description': project_description,
                'planned_date': display_date,
                'sort_key': planned_date if isinstance(planned_date, date) else None,
                'title_key': project_title.casefold() if project_title else '',
            }

        entry_column = normalize_key(entry.get('column'))
        if entry_column in montaje_keys:
            ensure_montaje_row()

        details = entry.get('link_details') or []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            column_name = detail.get('column')
            normalized_column = normalize_key(column_name)
            if normalized_column in target_column_keys:
                title = (detail.get('title') or '').strip()
                if not title:
                    fallback = (
                        entry.get('project')
                        or entry.get('title')
                        or entry.get('display_title')
                        or ''
                    )
                    title = fallback.strip()
                card_id = str(detail.get('id') or '').strip()
                dedupe_key = (card_id or normalize_key(title), pid_str)
                if dedupe_key in pending_seen:
                    continue
                pending_seen.add(dedupe_key)
                planned_date = None
                if pid_str and not all_unplanned_projects.get(pid_str, False):
                    planned_date = planned_starts.get(pid_str)
                display_date = ''
                if isinstance(planned_date, date):
                    display_date = planned_date.strftime('%d/%m/%Y')
                project = project_lookup.get(pid_str)
                project_title = ''
                project_description = ''
                if project:
                    project_title = (project.get('name') or '').strip()
                    project_description = (project.get('client') or '').strip()
                if not project_title:
                    fallback = (
                        entry.get('project')
                        or entry.get('title')
                        or entry.get('display_title')
                        or ''
                    )
                    project_title = fallback.strip()
                if not project_description:
                    project_description = (entry.get('client') or '').strip()
                pending_rows.append(
                    {
                        'title': title,
                        'project_title': project_title,
                        'project_description': project_description,
                        'planned_date': display_date,
                        'sort_key': planned_date if isinstance(planned_date, date) else None,
                        'title_key': title.casefold() if title else '',
                    }
                )
            if normalized_column in montaje_keys:
                ensure_montaje_row()

    pending_rows.sort(
        key=lambda item: (
            item['sort_key'] is None,
            item['sort_key'] or date.max,
            item['title_key'],
        )
    )
    for item in pending_rows:
        item.pop('sort_key', None)
        item.pop('title_key', None)

    montaje_list = list(montaje_rows.values())
    montaje_list.sort(
        key=lambda item: (
            item['sort_key'] is None,
            item['sort_key'] or date.max,
            item['title_key'],
        )
    )
    for item in montaje_list:
        item.pop('sort_key', None)
        item.pop('title_key', None)

    return render_template(
        'orden_carpetas.html',
        pending_rows=pending_rows,
        montaje_rows=montaje_list,
    )


def _normalize_order_code(value):
    if not value:
        return ''
    text = str(value).strip()
    match = re.search(r'OF\s*(\d+)', text, re.IGNORECASE)
    if match:
        return f"OF {match.group(1)}"
    return text


def _pick_deadline_start(project):
    candidates = [
        project.get('due_date'),
        project.get('client_date'),
        project.get('client_due_date'),
        project.get('customer_date'),
    ]
    for value in candidates:
        if not value:
            continue
        text = str(value).strip()
        if not text:
            continue
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            parsed = parse_input_date(text)
            if parsed:
                return parsed.isoformat()
    display_fields = project.get('kanban_display_fields') or {}
    for label in ('Fecha Cliente', 'Fecha cliente'):
        raw = display_fields.get(label)
        if not raw:
            continue
        parsed = parse_input_date(str(raw).strip())
        if parsed:
            return parsed.isoformat()
    return ''


@app.route('/gantt')
def gantt_view():
    projects = get_visible_projects()
    sched, _ = schedule_projects(copy.deepcopy(projects))
    by_pid = {}
    project_workers = {}
    for worker, days in sched.items():
        for day, tasks in days.items():
            for t in tasks:
                pid = t.get('pid')
                if not pid:
                    continue
                if worker:
                    project_workers.setdefault(pid, set()).add(worker)
                if worker == UNPLANNED:
                    continue
                by_pid.setdefault(pid, []).append(t)

    all_unplanned_projects = {
        pid: bool(workers) and all(w == UNPLANNED for w in workers)
        for pid, workers in project_workers.items()
    }

    project_map = {}
    for p in projects:
        p.setdefault('kanban_attachments', [])
        p.setdefault('kanban_display_fields', {})
        project_map[p['id']] = {
            **p,
            'frozen_phases': sorted({t['phase'] for t in p.get('frozen_tasks', [])}),
            'phase_sequence': list((p.get('phases') or {}).keys()),
            'all_phases_unplanned': all_unplanned_projects.get(p['id'], False),
        }

    start_map = phase_start_map(projects)

    gantt_projects = []
    for p in projects:
        pid = p['id']
        tasks = by_pid.get(pid, [])
        if not tasks:
            continue
        start = min(t['start_time'] for t in tasks)
        end = max(t['end_time'] for t in tasks)
        phase_map = {}
        for t in tasks:
            key = t['phase']
            start_time = t.get('start_time')
            end_time = t.get('end_time')
            entry = phase_map.get(key)
            if not entry:
                entry = {
                    'id': f"{pid}-{key}",
                    'name': key,
                    'start': start_time,
                    'end': end_time,
                    'color': t.get('color', p.get('color')),
                    'worker': t.get('worker'),
                    'segments': [],
                    '_segment_map': {},
                }
                phase_map[key] = entry
            else:
                if start_time and (not entry['start'] or start_time < entry['start']):
                    entry['start'] = start_time
                if end_time and (not entry['end'] or end_time > entry['end']):
                    entry['end'] = end_time
                if not entry.get('worker') and t.get('worker'):
                    entry['worker'] = t.get('worker')

            seg_key = t.get('part')
            if seg_key is None:
                seg_key = '__default__'
            segment_map = entry['_segment_map']
            segment = segment_map.get(seg_key)
            if not segment:
                segment = {
                    'start': start_time,
                    'end': end_time,
                    'worker': t.get('worker'),
                    'part': t.get('part'),
                }
                segment_map[seg_key] = segment
                entry['segments'].append(segment)
            else:
                if start_time and segment['start'] and start_time < segment['start']:
                    segment['start'] = start_time
                if end_time and segment['end'] and end_time > segment['end']:
                    segment['end'] = end_time
                if not segment.get('worker') and t.get('worker'):
                    segment['worker'] = t.get('worker')

        phases = []
        for entry in phase_map.values():
            segments = entry.get('segments') or []
            segments.sort(key=lambda seg: (seg.get('start') or ''))
            starts = [seg.get('start') for seg in segments if seg.get('start')]
            ends = [seg.get('end') for seg in segments if seg.get('end')]
            if starts:
                entry['start'] = min(starts)
            if ends:
                entry['end'] = max(ends)
            entry.pop('_segment_map', None)
            phases.append(entry)
        gantt_projects.append({
            'id': pid,
            'name': p['name'],
            'client': p.get('client', ''),
            'start': start,
            'end': end,
            'due_date': p.get('due_date'),
            'color': p.get('color'),
            'deadline_start': _pick_deadline_start(p),
            'phases': phases,
            'all_phases_unplanned': all_unplanned_projects.get(pid, False),
        })
    return render_template(
        'gantt.html',
        projects=json.dumps(gantt_projects),
        project_data=project_map,
        start_map=start_map,
        phases=PHASE_ORDER,
        gantt_mode='phases',
        phase_actions_enabled=True,
    )


def _safe_iso_date(value):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _extract_order_title_date(title, reference_day, fallback_year):
    if not title:
        return None
    match = re.search(r"\((\d{2})/(\d{2})\)", title)
    if not match:
        return None
    try:
        day = int(match.group(1))
        month = int(match.group(2))
    except ValueError:
        return None
    year = fallback_year
    if isinstance(reference_day, date):
        year = reference_day.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if isinstance(reference_day, date):
        delta_days = (candidate - reference_day).days
        if delta_days < -183:
            try:
                candidate = date(year + 1, month, day)
            except ValueError:
                pass
        elif delta_days > 183:
            try:
                candidate = date(year - 1, month, day)
            except ValueError:
                pass
    return candidate


def _parse_order_deadline(raw_value):
    if not raw_value:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    parsed = parse_input_date(text)
    if parsed:
        return parsed
    return parse_kanban_date(text)


def _resolve_unconfirmed_schedule_day(entry, planned_end, today):
    explicit = _parse_order_deadline(entry.get('kanban_date'))
    if explicit:
        return explicit
    if isinstance(planned_end, date):
        return _subtract_business_days(planned_end, 5)
    return today


def _subtract_business_days(day, count):
    if not isinstance(day, date) or count <= 0:
        return day
    current = day
    removed = 0
    while removed < count:
        current -= timedelta(days=1)
        if current.weekday() not in WEEKEND:
            removed += 1
    return current


def _add_business_days(day, count):
    if not isinstance(day, date) or count <= 0:
        return day
    current = day
    added = 0
    while added < count:
        current += timedelta(days=1)
        if current.weekday() not in WEEKEND:
            added += 1
    return current


def business_days_elapsed(start, end):
    """Return the number of business days between *start* and *end*.

    The result excludes weekends and does not count the starting day.
    A positive value means ``end`` is after ``start``; negative values
    indicate the opposite direction.
    """

    if not isinstance(start, date) or not isinstance(end, date):
        return None

    if start == end:
        return 0

    if end >= start:
        begin = start
        finish = end
        sign = 1
    else:
        begin = end
        finish = start
        sign = -1

    delta_days = (finish - begin).days
    full_weeks, remainder = divmod(delta_days, 7)
    business = full_weeks * 5
    for offset in range(1, remainder + 1):
        candidate = begin + timedelta(days=offset)
        if candidate.weekday() not in WEEKEND:
            business += 1

    return business * sign


def business_days_since(start, *, today=None):
    """Return business days elapsed from *start* to *today* (>= 0)."""

    if today is None:
        today = local_today()

    diff = business_days_elapsed(start, today)
    if diff is None:
        return None
    return diff if diff >= 0 else 0


def _should_highlight_order(order_day, planned_start, *, window=3, today=None):
    if not isinstance(order_day, date) or not isinstance(planned_start, date):
        return False

    highlight = False
    threshold = _subtract_business_days(planned_start, window)
    if threshold <= order_day <= planned_start:
        highlight = True

    if isinstance(today, date) and planned_start <= order_day <= today:
        highlight = True

    return highlight


@app.route('/gantt-pedidos')
def gantt_orders_view():
    today = local_today()
    compras_raw, column_colors = load_compras_raw()
    projects = get_visible_projects()
    soldar_lookup = build_phase_finish_lookup(projects, SOLDAR_PHASE_PRIORITY)
    calendar_payload = compute_pedidos_entries(
        compras_raw,
        column_colors,
        today,
        soldar_finish_lookup=soldar_lookup,
    )
    pedidos_calendar = calendar_payload['pedidos']
    pedidos = pedidos_calendar['scheduled']
    unconfirmed = pedidos_calendar['unconfirmed']
    base_links = build_project_links(compras_raw)

    order_to_code = {}
    for entry in base_links:
        code = _normalize_order_code(entry.get('custom_card_id'))
        if not code:
            code = _normalize_order_code(entry.get('display_title') or entry.get('title'))
        details = entry.get('link_details') or []
        for detail in details:
            if isinstance(detail, dict):
                cid = str(detail.get('id') or '').strip()
                if cid and code:
                    order_to_code.setdefault(cid, code)
        for cid in entry.get('link_ids') or []:
            cid = str(cid or '').strip()
            if cid and code:
                order_to_code.setdefault(cid, code)

    scheduled_projects = copy.deepcopy(projects)
    schedule_data, _ = schedule_projects(scheduled_projects)

    planned_windows = {}
    project_workers = {}

    for project in scheduled_projects:
        pid = project.get('id')
        if not pid:
            continue
        planned_windows.setdefault(pid, {'start': None, 'end': None})

    for worker, days in schedule_data.items():
        for tasks in days.values():
            for task in tasks:
                pid = task.get('pid')
                if not pid:
                    continue
                if worker:
                    project_workers.setdefault(pid, set()).add(worker)
                if worker == UNPLANNED or task.get('worker') == UNPLANNED:
                    continue
                start_day = _safe_iso_date(task.get('start_time'))
                end_day = _safe_iso_date(task.get('end_time'))
                window = planned_windows.setdefault(pid, {'start': None, 'end': None})
                if start_day and (window['start'] is None or start_day < window['start']):
                    window['start'] = start_day
                if end_day and (window['end'] is None or end_day > window['end']):
                    window['end'] = end_day

    for project in scheduled_projects:
        pid = project.get('id')
        if not pid:
            continue
        window = planned_windows.setdefault(pid, {'start': None, 'end': None})
        if window['start'] is None:
            start_day = _safe_iso_date(project.get('start_date'))
            if start_day:
                window['start'] = start_day
        if window['end'] is None:
            end_day = _safe_iso_date(project.get('end_date'))
            if end_day:
                window['end'] = end_day

    all_unplanned_projects = {
        pid: bool(workers) and all(w == UNPLANNED for w in workers)
        for pid, workers in project_workers.items()
    }

    project_map = {}
    code_to_project = {}
    for p in projects:
        p.setdefault('kanban_attachments', [])
        p.setdefault('kanban_display_fields', {})
        plan_window = planned_windows.get(p['id']) or {}
        planned_start_iso = plan_window.get('start').isoformat() if plan_window.get('start') else ''
        planned_end_iso = plan_window.get('end').isoformat() if plan_window.get('end') else ''
        project_map[p['id']] = {
            **p,
            'frozen_phases': sorted({t['phase'] for t in p.get('frozen_tasks', [])}),
            'phase_sequence': list((p.get('phases') or {}).keys()),
            'kanban_previous_phases': compute_previous_kanban_phases(p.get('kanban_column')),
            'planned_start': planned_start_iso,
            'planned_end': planned_end_iso,
            'all_phases_unplanned': all_unplanned_projects.get(p['id'], False),
        }
        code = _normalize_order_code(p.get('name'))
        if code:
            code_to_project[code] = p

    for entry in unconfirmed:
        cid = str(entry.get('cid') or '').strip()
        code = _normalize_order_code(entry.get('custom_card_id'))
        if not code and cid:
            code = order_to_code.get(cid, '')
        if not code:
            code = _normalize_order_code(entry.get('project'))
        planned_end = None
        if code:
            project = code_to_project.get(code)
            if project:
                window = planned_windows.get(project['id']) or {}
                planned_end = window.get('end')
        scheduled_day = _resolve_unconfirmed_schedule_day(entry, planned_end, today)
        pedidos.setdefault(scheduled_day, []).append(entry)

    orders_by_pid = {}
    pseudo_counter = 0

    for scheduled_day, entries in pedidos.items():
        if not isinstance(scheduled_day, date):
            continue
        day_iso = scheduled_day.isoformat()
        for entry in entries:
            cid = str(entry.get('cid') or '').strip()
            code = _normalize_order_code(entry.get('custom_card_id'))
            if not code and cid:
                code = order_to_code.get(cid, '')
            if not code:
                code = _normalize_order_code(entry.get('project'))

            project = code_to_project.get(code)
            entry_color = entry.get('color')
            if project:
                pid = project['id']
                name = project.get('name') or code or entry.get('project') or 'Pedido'
                client = project.get('client', '')
                color = project.get('color') or entry_color
                due = project.get('due_date')
                deadline_start = _pick_deadline_start(project)
            else:
                pseudo_counter += 1
                pid = f"pedido-{pseudo_counter}"
                name = code or entry.get('project') or f"Pedido {pseudo_counter}"
                client = entry.get('client') or ''
                color = entry_color or '#6c9ec1'
                due = ''
                deadline_start = ''
                if pid not in project_map:
                    project_map[pid] = {
                        'id': pid,
                        'name': name,
                        'client': client,
                        'kanban_display_fields': {},
                        'kanban_attachments': [],
                        'phase_sequence': [],
                        'kanban_previous_phases': [],
                        'frozen_phases': [],
                        'color': color,
                        'due_date': due,
                        'deadline_start': deadline_start,
                        'observations': '',
                        'planned_start': '',
                        'planned_end': '',
                    }

            plan_window = planned_windows.get(pid)
            planned_start_date = plan_window.get('start') if plan_window else None
            planned_end_date = plan_window.get('end') if plan_window else None
            planned_start_iso = planned_start_date.isoformat() if planned_start_date else ''
            planned_end_iso = planned_end_date.isoformat() if planned_end_date else ''

            proj_entry = orders_by_pid.setdefault(pid, {
                'id': pid,
                'name': name,
                'client': client,
                'start': planned_start_iso or day_iso,
                'end': planned_end_iso or day_iso,
                'due_date': due,
                'color': color or '#6c9ec1',
                'deadline_start': deadline_start,
                'planned_start': planned_start_iso,
                'planned_end': planned_end_iso,
                'order_dates': [],
                'phases': [],
                'all_phases_unplanned': all_unplanned_projects.get(pid, False),
            })

            proj_entry['all_phases_unplanned'] = all_unplanned_projects.get(pid, False)

            order_column = (entry.get('column') or '').strip()

            effective_day = scheduled_day
            title_date = _extract_order_title_date(entry.get('project'), scheduled_day, today.year)
            if title_date:
                effective_day = title_date
            order_column_key = normalize_key(order_column)
            if (
                planned_end_date
                and order_column_key in PEDIDOS_OFFSET_TO_PLAN_END_KEYS
            ):
                effective_day = _subtract_business_days(planned_end_date, 5)
            effective_iso = effective_day.isoformat()

            proj_entry['order_dates'].append(effective_iso)

            should_flag_order = False
            if planned_start_date and order_column not in {'Tratamiento', 'Tratamiento final'}:
                should_flag_order = _should_highlight_order(
                    effective_day,
                    planned_start_date,
                    today=today,
                )

            phase = {
                'id': f"{pid}-pedido-{cid or len(proj_entry['phases'])}",
                'name': entry.get('project') or 'Pedido',
                'start': effective_iso,
                'end': effective_iso,
                'color': entry.get('color') or color,
                'worker': order_column,
                'order_column': order_column,
                'order_lane': entry.get('lane') or '',
                'order_client': entry.get('client') or '',
                'order_code': code,
                'order_cid': cid,
                'order_prev_date': entry.get('prev_date') or '',
                'order_date': effective_iso,
                'order_kanban_date': entry.get('kanban_date') or '',
                'order_highlight': should_flag_order,
            }
            proj_entry['phases'].append(phase)

            if planned_start_iso:
                proj_entry['start'] = planned_start_iso
                proj_entry['planned_start'] = planned_start_iso
            if planned_end_iso:
                proj_entry['end'] = planned_end_iso
                proj_entry['planned_end'] = planned_end_iso

    final_projects = []
    for pid, proj_entry in orders_by_pid.items():
        order_dates = sorted(d for d in proj_entry.pop('order_dates', []) if d)
        plan_window = planned_windows.get(pid)
        planned_start_date = plan_window.get('start') if plan_window else None
        planned_end_date = plan_window.get('end') if plan_window else None
        if planned_start_date:
            planned_start_iso = planned_start_date.isoformat()
            proj_entry['planned_start'] = planned_start_iso
            proj_entry['start'] = planned_start_iso
        else:
            if order_dates:
                proj_entry['start'] = order_dates[0]
                proj_entry['planned_start'] = order_dates[0]
            else:
                proj_entry['planned_start'] = proj_entry.get('planned_start') or proj_entry.get('start', '')
                proj_entry['start'] = proj_entry['planned_start']
        if planned_end_date:
            planned_end_iso = planned_end_date.isoformat()
            proj_entry['planned_end'] = planned_end_iso
            proj_entry['end'] = planned_end_iso
        else:
            if order_dates:
                proj_entry['end'] = order_dates[-1]
                proj_entry['planned_end'] = order_dates[-1]
            else:
                proj_entry['planned_end'] = proj_entry.get('planned_end') or proj_entry.get('end', proj_entry['start'])
                proj_entry['end'] = proj_entry['planned_end']
        if pid in project_map:
            project_map[pid]['planned_start'] = proj_entry['planned_start']
            project_map[pid]['planned_end'] = proj_entry['planned_end']
            project_map[pid]['all_phases_unplanned'] = all_unplanned_projects.get(pid, False)
        proj_entry['phases'].sort(key=lambda ph: ph.get('start') or '')
        final_projects.append(proj_entry)

    gantt_projects = sorted(final_projects, key=lambda item: item.get('start') or item.get('planned_start') or '9999-12-31')

    return render_template(
        'gantt.html',
        projects=json.dumps(gantt_projects),
        project_data=project_map,
        start_map={},
        phases=PHASE_ORDER,
        gantt_mode='orders',
        phase_actions_enabled=False,
    )

@app.route('/projects')
def project_list():
    projects = get_visible_projects()
    # Compute deadlines to show whether each project meets its due date
    proj_copy = copy.deepcopy(projects)
    schedule_projects(proj_copy)
    end_dates = {p['id']: p['end_date'] for p in proj_copy}
    for p in projects:
        if p['id'] in end_dates:
            p['end_date'] = end_dates[p['id']]
            if p.get('due_date'):
                try:
                    p['met'] = date.fromisoformat(p['end_date']) <= date.fromisoformat(p['due_date'])
                except ValueError:
                    p['met'] = False
            else:
                p['met'] = False
        else:
            p['met'] = False
    project_filter = request.args.get('project', '').strip()
    client_filter = request.args.get('client', '').strip()
    q_filter = request.args.get('q', '').strip()
    if q_filter:
        project_filter = q_filter
        client_filter = ''
    sort_option = request.args.get('sort', 'created')

    orig_order = {p['id']: idx for idx, p in enumerate(projects)}

    if project_filter or client_filter:
        projects = [
            p for p in projects
            if (not project_filter or project_filter.lower() in p['name'].lower())
            and (not client_filter or client_filter.lower() in p['client'].lower())
        ]

    if sort_option == 'name':
        projects.sort(key=lambda p: p['name'].lower())
    else:
        projects.sort(key=lambda p: orig_order[p['id']], reverse=True)
    start_map = phase_start_map(projects)
    hours_map = load_daily_hours()
    material_status_map, _ = compute_material_status_map(projects)
    for proj in projects:
        pid = proj.get('id')
        if pid is None:
            continue
        proj['material_status'] = material_status_map.get(str(pid), 'complete')
    projects = expand_for_display(projects)
    for proj in projects:
        pid = proj.get('id')
        if pid is None:
            continue
        proj['material_status'] = material_status_map.get(str(pid), 'complete')
    return render_template(
        'projects.html',
        projects=projects,
        phases=PHASE_ORDER,
        all_workers=active_workers(),
        project_filter=project_filter,
        client_filter=client_filter,
        sort_option=sort_option,
        start_map=start_map,
        hours=hours_map,
        palette=COLORS,
        material_status_labels=MATERIAL_STATUS_LABELS,
    )



def _create_manual_project_from_request(data, file_storage=None, *, target_worker=None, target_date=None):
    image_path = None
    if file_storage and file_storage.filename:
        ext = os.path.splitext(secure_filename(file_storage.filename))[1]
        fname = f"{uuid.uuid4()}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, fname)
        file_storage.save(save_path)
        image_path = f"uploads/{fname}"

    projects = get_projects()
    color = COLORS[len(projects) % len(COLORS)]
    due = parse_input_date(data.get('due_date'))
    project = {
        'id': str(uuid.uuid4()),
        'name': data.get('name', ''),
        'client': data.get('client', ''),
        'start_date': local_today().isoformat(),
        'due_date': due.isoformat() if due else '',
        'material_confirmed_date': '',
        'color': color,
        'phases': {},
        'assigned': {},
        'image': image_path,
        'kanban_attachments': [],
        'planned': False,
        'source': 'manual',
    }

    for phase in PHASE_ORDER:
        if phase in NEW_PROJECT_EXCLUDED_PHASES:
            continue
        value_h = data.get(phase)
        value_d = data.get(f"{phase}_days")
        if phase == 'pedidos':
            if value_h:
                val = parse_input_date(value_h)
                if val:
                    project['phases'][phase] = val.isoformat()
                    project['assigned'][phase] = UNPLANNED
        else:
            hours = 0
            if value_h:
                try:
                    hours += int(value_h)
                except ValueError:
                    pass
            if value_d:
                try:
                    hours += int(value_d) * HOURS_PER_DAY
                except ValueError:
                    pass
            if hours:
                project['phases'][phase] = hours
                project['assigned'][phase] = UNPLANNED

    _ensure_default_initial_phases(project)

    planning_worker = str(target_worker or '').strip()
    planning_day = parse_input_date(target_date) if target_date else None
    if planning_worker and planning_worker != UNPLANNED and isinstance(planning_day, date):
        first_phase = None
        for phase in PHASE_ORDER:
            if phase in ('dibujo', 'pedidos'):
                continue
            if phase in project['phases']:
                first_phase = phase
                break
        if first_phase:
            project['assigned'][first_phase] = planning_worker
            project['segment_starts'] = {first_phase: [planning_day.isoformat()]}
            project['planned'] = True

    projects.append(project)
    save_projects(projects)
    return project


@app.route('/add', methods=['GET', 'POST'])
def add_project():
    if request.method == 'POST':
        clear_prefill_project()
        data = request.form
        file = request.files.get('image')
        project = _create_manual_project_from_request(data, file)
        return redirect(url_for('complete', highlight=project['id']))
    prefill = load_prefill_project()
    return render_template(
        'add_project.html',
        phases=PHASE_ORDER,
        today=local_today().isoformat(),
        prefill=prefill,
    )


@app.route('/add_inline', methods=['POST'])
def add_project_inline():
    clear_prefill_project()
    data = request.form
    file = request.files.get('image')
    name = str(data.get('name') or '').strip()
    client = str(data.get('client') or '').strip()
    due_date = str(data.get('due_date') or '').strip()
    if not name or not client or not due_date:
        return jsonify({'error': 'Faltan campos obligatorios'}), 400
    target_worker = data.get('target_worker')
    target_date = data.get('target_date')
    project = _create_manual_project_from_request(data, file, target_worker=target_worker, target_date=target_date)
    broadcast_event({'type': 'kanban_update'})
    return jsonify({'ok': True, 'pid': project['id']})



@app.route('/add_note', methods=['POST'])
def add_note():
    """Add a note with a unique id."""
    notes = load_notes()
    ndate = parse_input_date(request.form['date'])
    notes.append({
        'id': str(uuid.uuid4()),
        'description': request.form['description'],
        'date': ndate.isoformat() if ndate else '',
    })
    save_notes(notes)
    next_url = request.form.get('next') or url_for('complete')
    return redirect(next_url)


@app.route('/update_calendar_note', methods=['POST'])
def update_calendar_note():
    data = request.get_json() or {}
    day_raw = (data.get('date') or '').strip()
    text = data.get('text', '')
    if not day_raw:
        return jsonify({'error': 'Falta la fecha'}), 400
    try:
        day = date.fromisoformat(day_raw)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    day_iso = day.isoformat()
    clean_text = str(text or '').strip()
    notes = [n for n in load_notes() if (n.get('date') or '') != day_iso]
    if clean_text:
        notes.append({
            'id': str(uuid.uuid4()),
            'description': clean_text,
            'date': day_iso,
        })
    save_notes(notes)
    return jsonify({'ok': True})


@app.route('/notes')
def note_list():
    notes = load_notes()
    return render_template('notes.html', notes=notes)


@app.route('/delete_note/<nid>', methods=['POST'])
def delete_note(nid):
    notes = load_notes()
    notes = [n for n in notes if n.get('id') != nid]
    save_notes(notes)
    next_url = request.form.get('next') or url_for('note_list')
    return redirect(next_url)


@app.route('/observaciones')
def observation_list():
    projects = [p for p in get_visible_projects() if p.get('observations')]
    return render_template('observations.html', projects=projects)


@app.route('/estado-datos')
def planning_snapshot_view():
    snapshots = load_planning_snapshots()
    snapshot = snapshots[-1] if snapshots else create_planning_snapshot()
    return render_template('planning_snapshot.html', snapshot=snapshot)


@app.route('/estado-datos/snapshot', methods=['GET', 'POST'])
def planning_snapshot_api():
    if request.method == 'POST':
        snapshot = create_planning_snapshot()
    else:
        snapshots = load_planning_snapshots()
        snapshot = snapshots[-1] if snapshots else create_planning_snapshot()
    return jsonify(snapshot)


@app.route('/vacations', methods=['GET', 'POST'])
def vacation_list():
    vacations = load_vacations()
    error = None

    def includes_year(raw_value):
        raw_value = (raw_value or '').strip()
        if not raw_value:
            return False
        normalized = raw_value.replace('/', '-').replace('.', '-').replace(' ', '')
        parts = [p for p in normalized.split('-') if p]
        if len(parts) < 3:
            return False
        return any(len(part) == 4 for part in parts)

    if request.method == 'POST':
        start_raw = request.form['start']
        end_raw = request.form['end']
        selected_workers = request.form.getlist('workers')
        selected_workers = [w for w in selected_workers if w]

        if not (includes_year(start_raw) and includes_year(end_raw)):
            error = 'Las fechas deben incluir el año (dd/mm/aaaa).'
        elif not selected_workers:
            error = 'Selecciona al menos un trabajador.'
        else:
            start = parse_input_date(start_raw)
            end = parse_input_date(end_raw)
            if not start or not end:
                error = 'Introduce fechas de inicio y fin válidas.'
            elif end < start:
                error = 'La fecha de fin no puede ser anterior a la fecha de inicio.'
            else:
                for worker in selected_workers:
                    vacations.append({
                        'id': str(uuid.uuid4()),
                        'worker': worker,
                        'start': start.isoformat(),
                        'end': end.isoformat(),
                    })
                save_vacations(vacations)
                return redirect(url_for('vacation_list'))
    today = local_today()

    def parse_iso(value):
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def vacation_sort_key(vacation):
        start_date = parse_iso(vacation.get('start'))
        end_date = parse_iso(vacation.get('end'))
        reference = start_date or end_date or today
        effective_end = end_date or start_date or reference
        is_past = effective_end < today
        ref_ord = reference.toordinal()
        if not is_past:
            return (0, ref_ord)
        return (1, -ref_ord)

    ordered_vacations = sorted(vacations, key=vacation_sort_key)

    formatted_vacations = []
    for vacation in ordered_vacations:
        entry = dict(vacation)
        start_date = parse_iso(vacation.get('start'))
        end_date = parse_iso(vacation.get('end'))
        entry['start_display'] = start_date.strftime('%d/%m/%Y') if start_date else ''
        entry['end_display'] = end_date.strftime('%d/%m/%Y') if end_date else ''
        formatted_vacations.append(entry)

    return render_template(
        'vacations.html',
        vacations=formatted_vacations,
        workers=active_workers(),
        today=today.isoformat(),
        error=error,
    )


@app.route('/delete_vacation/<vid>', methods=['POST'])
def delete_vacation(vid):
    vacations = load_vacations()
    vacations = [v for v in vacations if v.get('id') != vid]
    save_vacations(vacations)
    return redirect(url_for('vacation_list'))


@app.route('/remove_vacation', methods=['POST'])
def remove_vacation():
    worker = request.form['worker']
    day = date.fromisoformat(request.form['date'])
    vacations = load_vacations()
    new_list = []
    for v in vacations:
        if v['worker'] != worker:
            new_list.append(v)
            continue
        start = date.fromisoformat(v['start'])
        end = date.fromisoformat(v['end'])
        if day < start or day > end:
            new_list.append(v)
            continue
        if start == end == day:
            continue
        if start == day:
            v['start'] = (day + timedelta(days=1)).isoformat()
            new_list.append(v)
        elif end == day:
            v['end'] = (day - timedelta(days=1)).isoformat()
            new_list.append(v)
        else:
            before = v.copy()
            after = v.copy()
            before['end'] = (day - timedelta(days=1)).isoformat()
            after['start'] = (day + timedelta(days=1)).isoformat()
            before['id'] = str(uuid.uuid4())
            after['id'] = str(uuid.uuid4())
            new_list.extend([before, after])
    save_vacations(new_list)
    return ('', 204)


@app.route('/assign_vacation_day', methods=['POST'])
def assign_vacation_day():
    data = request.get_json() or request.form
    worker = (data.get('worker') or '').strip()
    day_text = data.get('date')
    if not worker or worker not in WORKERS:
        return jsonify({'error': 'Trabajador no válido'}), 400
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        return jsonify({'error': 'Fecha no válida'}), 400
    vacations = load_vacations()
    already_marked = False
    for entry in vacations:
        if entry.get('worker') != worker:
            continue
        try:
            start = date.fromisoformat(entry.get('start'))
            end = date.fromisoformat(entry.get('end'))
        except (TypeError, ValueError):
            continue
        if start <= day <= end:
            already_marked = True
            break
    if not already_marked:
        vacations.append(
            {
                'id': str(uuid.uuid4()),
                'worker': worker,
                'start': day.isoformat(),
                'end': day.isoformat(),
            }
        )
        save_vacations(vacations)
    return ('', 204)


@app.route('/deactivate_worker_day', methods=['POST'])
def deactivate_worker_day():
    data = request.get_json(silent=True) or request.form
    worker = (data.get('worker') or '').strip()
    day_text = data.get('date')
    if not worker or worker not in WORKERS or worker == UNPLANNED:
        return jsonify({'error': 'Recurso no válido'}), 400
    try:
        effective_date = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        return jsonify({'error': 'Fecha no válida'}), 400
    _deactivate_worker_from_date(worker, effective_date)
    return ('', 204)


@app.route('/remove_deactivated_worker', methods=['POST'])
def remove_deactivated_worker():
    data = request.get_json(silent=True) or request.form
    worker = (data.get('worker') or '').strip()
    if not worker or worker == UNPLANNED:
        return jsonify({'error': 'Recurso no válido'}), 400
    inactive = load_inactive_workers()
    updated_inactive = [w for w in inactive if w != worker]
    if updated_inactive != inactive:
        save_inactive_workers(updated_inactive)
    inactive_dates = load_inactive_worker_dates()
    if worker in inactive_dates:
        inactive_dates.pop(worker, None)
        save_inactive_worker_dates(inactive_dates)
    return ('', 204)


@app.route('/update_worker_day_hours', methods=['POST'])
def update_worker_day_hours():
    data = request.get_json() or request.form
    worker = (data.get('worker') or '').strip()
    day_text = data.get('date')
    hours_value = data.get('hours')
    if not worker or worker not in WORKERS:
        return jsonify({'error': 'Trabajador no válido'}), 400
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        return jsonify({'error': 'Fecha no válida'}), 400
    try:
        hours = int(hours_value)
    except (TypeError, ValueError):
        return jsonify({'error': 'Horas no válidas'}), 400
    if hours < 1 or hours > 10:
        return jsonify({'error': 'Horas fuera de rango'}), 400
    overrides = load_worker_day_hours()
    worker_map = overrides.get(worker, {})
    worker_map[day.isoformat()] = hours
    overrides[worker] = worker_map
    save_worker_day_hours(overrides)
    return ('', 204)



def _clear_phase_segment_position(project, phase, part):
    """Remove manual start metadata for a phase or split part."""

    if part is None:
        for key in ('segment_starts', 'segment_start_hours', 'segment_workers'):
            mapping = project.get(key)
            if isinstance(mapping, dict):
                mapping.pop(phase, None)
                if not mapping:
                    project.pop(key, None)
        return

    try:
        idx = int(part)
    except (TypeError, ValueError):
        return
    for key in ('segment_starts', 'segment_start_hours'):
        mapping = project.get(key)
        values = mapping.get(phase) if isinstance(mapping, dict) else None
        if isinstance(values, list) and idx < len(values):
            values[idx] = None
            while values and values[-1] is None:
                values.pop()
            if values:
                mapping[phase] = values
            else:
                mapping.pop(phase, None)
            if not mapping:
                project.pop(key, None)


def _set_phase_worker_unplanned(project, phase, part=None):
    phases = project.get('phases') or {}
    assigned = project.setdefault('assigned', {})
    if part is None or not isinstance(phases.get(phase), list):
        assigned[phase] = UNPLANNED
        _clear_phase_segment_position(project, phase, None)
        return

    try:
        idx = int(part)
    except (TypeError, ValueError):
        assigned[phase] = UNPLANNED
        _clear_phase_segment_position(project, phase, None)
        return
    segment_workers = project.setdefault('segment_workers', {}).setdefault(phase, [])
    while len(segment_workers) <= idx:
        segment_workers.append(None)
    segment_workers[idx] = UNPLANNED
    _clear_phase_segment_position(project, phase, idx)


def _remove_worker_frozen_tasks(project, workers, *, today=None, phase=None, part=None):
    frozen_tasks = project.get('frozen_tasks')
    if not isinstance(frozen_tasks, list):
        return False
    worker_set = {w for w in workers if w}
    kept = []
    changed = False
    for task in frozen_tasks:
        if not isinstance(task, dict) or task.get('worker') not in worker_set:
            kept.append(task)
            continue
        if phase is not None and task.get('phase') != phase:
            kept.append(task)
            continue
        task_part = task.get('part')
        normalized_task_part = task_part if task_part not in ('', 'None') else None
        if part is not None and normalized_task_part != part:
            kept.append(task)
            continue
        if today is not None:
            try:
                task_day = date.fromisoformat(task.get('day'))
            except (TypeError, ValueError):
                task_day = None
            if task_day is not None and task_day < today:
                kept.append(task)
                continue
        changed = True
    if changed:
        if kept:
            project['frozen_tasks'] = kept
        else:
            project.pop('frozen_tasks', None)
    return changed


def _move_worker_future_phases_to_manual_bucket(projects, workers_to_hide, *, today=None):
    """Move phases planned from *today* onwards for hidden workers to ARR."""

    hidden_workers = {w for w in workers_to_hide if w and w != UNPLANNED}
    if not hidden_workers:
        return False
    if today is None:
        today = local_today()
    schedule_map = compute_schedule_map(projects)
    to_move = []
    seen = set()
    for pid, entries in schedule_map.items():
        for worker, day_text, phase, _hours, part in entries:
            if worker not in hidden_workers:
                continue
            try:
                planned_day = date.fromisoformat(day_text)
            except (TypeError, ValueError):
                continue
            if planned_day < today:
                continue
            key = (str(pid), phase, part if part not in ('', 'None') else None)
            if key in seen:
                continue
            seen.add(key)
            to_move.append(key)
    if not to_move:
        return False

    project_by_pid = {str(project.get('id')): project for project in projects or []}
    for pid, phase, part in to_move:
        project = project_by_pid.get(pid)
        if not project:
            continue
        _set_phase_worker_unplanned(project, phase, part)
        _remove_worker_frozen_tasks(
            project, hidden_workers, today=today, phase=phase, part=part
        )
        manual_bucket_add(pid, phase, part)
    return True


def _move_worker_all_phases_to_manual_bucket(projects, worker):
    """Move every phase assigned to a deleted worker to ARR before deletion."""

    if not worker or worker == UNPLANNED:
        return False
    changed = False
    entries = []
    for project in projects or []:
        pid = str(project.get('id') or '')
        if not pid:
            continue
        assigned = project.get('assigned') or {}
        phases = project.get('phases') or {}
        for phase, assigned_worker in list(assigned.items()):
            if assigned_worker == worker:
                value = phases.get(phase)
                if isinstance(value, list):
                    for idx in range(len(value)):
                        entries.append((project, pid, phase, idx))
                else:
                    entries.append((project, pid, phase, None))
        segment_workers = project.get('segment_workers') or {}
        for phase, workers in list(segment_workers.items()):
            if not isinstance(workers, list):
                continue
            for idx, assigned_worker in enumerate(workers):
                if assigned_worker == worker:
                    part = idx if isinstance(phases.get(phase), list) else None
                    entries.append((project, pid, phase, part))
    seen = set()
    for project, pid, phase, part in entries:
        key = (pid, phase, part)
        if key in seen:
            continue
        seen.add(key)
        _set_phase_worker_unplanned(project, phase, part)
        _remove_worker_frozen_tasks(project, {worker}, phase=phase, part=part)
        manual_bucket_add(pid, phase, part)
        changed = True
    for project in projects or []:
        if _remove_worker_frozen_tasks(project, {worker}):
            changed = True
    return changed

@app.route('/resources', methods=['GET', 'POST'])
def resources():
    workers = [w for w in WORKERS.keys() if w != UNPLANNED]
    current_order_snapshot = list(workers)
    inactive = set(load_inactive_workers())
    worker_overrides = load_worker_hours()
    if request.method == 'POST':
        if 'add_worker' in request.form:
            new_worker = request.form.get('new_worker', '').strip()
            if new_worker and new_worker not in WORKERS:
                _schedule_mod.add_worker(new_worker)
            return redirect(url_for('resources'))
        delete_worker_name = request.form.get('delete_worker', '').strip()
        if delete_worker_name:
            projects = get_projects()
            moved = _move_worker_all_phases_to_manual_bucket(projects, delete_worker_name)
            if moved:
                save_projects(projects)
            delete_worker(delete_worker_name)
            return redirect(url_for('resources'))
        rename_pairs = []
        seen_targets = set()
        for key in request.form.keys():
            if not key.startswith('worker_name__'):
                continue
            idx = key.split('__', 1)[-1]
            original = request.form.get(f'worker_original__{idx}', '').strip()
            new_value = request.form.get(key, '').strip()
            if not original or not new_value or original == new_value:
                continue
            if new_value in seen_targets:
                continue
            rename_pairs.append((original, new_value))
            seen_targets.add(new_value)
        rename_map = {}
        for original, new_value in rename_pairs:
            if rename_worker(original, new_value):
                rename_map[original] = new_value
        if rename_map:
            workers = [w for w in WORKERS.keys() if w != UNPLANNED]
            current_order_snapshot = list(workers)
            inactive = set(load_inactive_workers())
            worker_overrides = load_worker_hours()
        active = request.form.getlist('worker')
        if rename_map:
            active = [rename_map.get(w, w) for w in active]
        new_inactive = [w for w in workers if w not in active]
        default_effective_date = local_today()
        inactive_date_map = load_inactive_worker_dates()
        visibility_effective_dates = {}
        for key, value in request.form.items():
            if not key.startswith('visibility_effective__'):
                continue
            idx = key.split('__', 1)[-1]
            worker = request.form.get(f'worker_original__{idx}', '').strip()
            if rename_map and worker:
                worker = rename_map.get(worker, worker)
            if not worker:
                continue
            try:
                effective_date = date.fromisoformat((value or '').strip())
            except (TypeError, ValueError):
                effective_date = default_effective_date
            visibility_effective_dates[worker] = effective_date
        newly_hidden = set(new_inactive) - inactive
        if newly_hidden:
            projects = get_projects()
            moved = False
            for worker in sorted(newly_hidden):
                effective_date = visibility_effective_dates.get(worker, default_effective_date)
                inactive_date_map[worker] = effective_date.isoformat()
                if _move_worker_future_phases_to_manual_bucket(
                    projects, {worker}, today=effective_date
                ):
                    moved = True
            if moved:
                save_projects(projects)
        active_inactive = set(new_inactive)
        cleaned_inactive_dates = {
            worker: inactive_date_map[worker]
            for worker in active_inactive
            if worker in inactive_date_map
        }
        if newly_hidden or cleaned_inactive_dates != inactive_date_map:
            save_inactive_worker_dates(cleaned_inactive_dates)
        if set(new_inactive) != inactive:
            save_inactive_workers(new_inactive)
            inactive = set(new_inactive)
        order = request.form.getlist('order')
        if rename_map and order:
            order = [rename_map.get(w, w) for w in order]
        if order:
            cleaned_order = []
            seen = set()
            for name in order:
                if name in workers and name not in seen:
                    cleaned_order.append(name)
                    seen.add(name)
            for name in workers:
                if name not in seen:
                    cleaned_order.append(name)
            if cleaned_order != current_order_snapshot:
                set_worker_order(cleaned_order)
                workers = [w for w in WORKERS.keys() if w != UNPLANNED]
                current_order_snapshot = list(workers)
        hours_modified = False
        for key, changed in request.form.items():
            if not key.startswith('hours_changed__'):
                continue
            if changed != '1':
                continue
            idx = key.split('__', 1)[-1]
            worker = request.form.get(f'hours_worker__{idx}')
            if rename_map and worker:
                worker = rename_map.get(worker, worker)
            if not worker:
                continue
            value = request.form.get(f'hours__{idx}', '').strip()
            if value == 'inf' or not value:
                if worker in worker_overrides:
                    worker_overrides.pop(worker, None)
                    hours_modified = True
                continue
            try:
                hours_val = int(value)
            except ValueError:
                continue
            if 1 <= hours_val <= 12:
                if worker_overrides.get(worker) != hours_val:
                    worker_overrides[worker] = hours_val
                    hours_modified = True
        if hours_modified:
            save_worker_hours(worker_overrides)
        get_projects()
        return redirect(url_for('resources'))
    worker_limits = {}
    for w in workers:
        limit = HOURS_LIMITS.get(w)
        if isinstance(limit, (int, float)) and limit == float('inf'):
            worker_limits[w] = 'inf'
        elif isinstance(limit, (int, float)):
            worker_limits[w] = int(limit)
        else:
            worker_limits[w] = HOURS_PER_DAY
    hour_options = list(range(1, 13))
    return render_template(
        'resources.html',
        workers=workers,
        inactive=inactive,
        worker_limits=worker_limits,
        hour_options=hour_options,
        today=local_today().isoformat(),
    )




@app.route('/analisis')
def analisis_view():
    today = local_today()
    projects = load_projects() or []
    compras_raw, _colors = load_compras_raw()
    links_table = attach_phase_starts(build_project_links(compras_raw), projects)

    planned_schedule, _planned_conflicts = schedule_projects(copy.deepcopy(projects))
    planned_start_by_pid = {}
    planned_end_by_pid = {}
    for worker, days in (planned_schedule or {}).items():
        if worker == UNPLANNED:
            continue
        if not isinstance(days, dict):
            continue
        for day_text, tasks in days.items():
            try:
                day_obj = date.fromisoformat(day_text)
            except Exception:
                continue
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                pid = str(task.get('pid') or '').strip()
                if not pid:
                    continue
                phase_name = phase_base(task.get('phase'))
                if phase_name != 'vacaciones':
                    prev_end = planned_end_by_pid.get(pid)
                    if prev_end is None or day_obj > prev_end:
                        planned_end_by_pid[pid] = day_obj
                if phase_name in ('preparar material', 'vacaciones', 'lanzamiento'):
                    continue
                prev_start = planned_start_by_pid.get(pid)
                if prev_start is None or day_obj < prev_start:
                    planned_start_by_pid[pid] = day_obj

    def _fmt_day(value):
        return _format_analysis_date(value) if isinstance(value, date) else ''

    def _metric_payload(value, *, inverse=False, exclamation=False):
        if exclamation:
            return {
                'display': '!',
                'sort': value if isinstance(value, int) else -1,
                'class': 'analysis-warn-red' if isinstance(value, int) and value > 5 else 'analysis-warn-orange',
            }
        if value is None:
            return {'display': '', 'sort': -1, 'class': ''}
        css = ''
        if inverse:
            if value < 5:
                css = 'analysis-warn-red'
            elif value < 10:
                css = 'analysis-warn-orange'
        else:
            if value > 5:
                css = 'analysis-warn-red'
            elif value > 3:
                css = 'analysis-warn-orange'
        return {'display': value, 'sort': value, 'class': css}

    order_dates_by_pid = {}
    for entry in links_table:
        pid = str(entry.get('pid') or '').strip()
        if not pid:
            continue
        details = entry.get('link_details') or []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            order_day = _parse_analysis_date(detail.get('order_date') or detail.get('order_date_raw'))
            if not isinstance(order_day, date):
                continue
            order_dates_by_pid.setdefault(pid, []).append(order_day)

    rows = []
    for project in projects:
        pid = str(project.get('id') or '').strip()
        if not pid:
            continue
        kanban_card = _project_kanban_card(project)

        fecha_pedido_text = _get_custom_field_text(kanban_card, 'Fecha pedido')
        fecha_pedido = _parse_analysis_date(fecha_pedido_text)
        if not isinstance(fecha_pedido, date) or fecha_pedido.year != 2026:
            continue

        fecha_tope_text = _get_custom_field_text(kanban_card, 'Fecha tope', 'Fecha tope cliente', 'Fecha tope proyecto')
        fecha_cliente_text = _get_custom_field_text(kanban_card, 'Fecha cliente', 'Fecha Cliente')
        fecha_cliente = _parse_analysis_date(fecha_tope_text) or _parse_analysis_date(fecha_cliente_text) or _parse_analysis_date(project.get('due_date'))

        lanzamiento_text = _get_custom_field_text(kanban_card, 'LANZAMIENTO')
        material_text = _get_custom_field_text(kanban_card, 'MATERIAL')
        caldereria_text = _get_custom_field_text(kanban_card, 'CALDERERIA', 'CALDERERÍA')
        pintura_text = _get_custom_field_text(kanban_card, 'PINTURA', 'PINTADO')
        mecanizado_text = _get_custom_field_text(kanban_card, 'MECANIZADO')
        tratamiento_text = _get_custom_field_text(kanban_card, 'TRATAMIENTO')
        lanzamiento_date = _parse_analysis_date(lanzamiento_text)
        material_date = _parse_analysis_date(material_text)
        caldereria_date = _parse_analysis_date(caldereria_text)
        pintura_date = _parse_analysis_date(pintura_text)
        mecanizado_date = _parse_analysis_date(mecanizado_text)
        tratamiento_date = _parse_analysis_date(tratamiento_text)

        order_dates = sorted(order_dates_by_pid.get(pid, []))
        first_order = order_dates[0] if order_dates else None
        last_order = order_dates[-1] if order_dates else None
        planned_start = planned_start_by_pid.get(pid)
        planned_end = planned_end_by_pid.get(pid)

        dias_primer = business_days_elapsed(fecha_pedido, first_order) if first_order else business_days_elapsed(fecha_pedido, today)
        dias_ultimo = business_days_elapsed(fecha_pedido, last_order) if last_order else None
        dias_ultimo_a_cliente = business_days_elapsed(last_order, fecha_cliente) if last_order and fecha_cliente else None

        dias_caldereria_inicio = business_days_elapsed(caldereria_date, planned_start) if caldereria_date and planned_start else None

        if first_order:
            dias_lanzamiento_primer = business_days_elapsed(lanzamiento_date, first_order) if lanzamiento_date else None
            dias_material_primer = business_days_elapsed(material_date, first_order) if material_date else None
            lanzamiento_payload = _metric_payload(dias_lanzamiento_primer)
            material_payload = _metric_payload(dias_material_primer)
        else:
            material_gap_today = business_days_elapsed(material_date, today) if material_date else None
            if isinstance(material_gap_today, int) and material_gap_today > 3:
                lanzamiento_payload = _metric_payload(material_gap_today, exclamation=True)
                material_payload = _metric_payload(material_gap_today, exclamation=True)
            else:
                lanzamiento_payload = _metric_payload(None)
                material_payload = _metric_payload(None)

        if first_order and planned_start:
            dias_primer_inicio = business_days_elapsed(first_order, planned_start)
            primer_inicio_payload = _metric_payload(dias_primer_inicio, inverse=True)
        elif not first_order and planned_start:
            days_until_start = business_days_elapsed(today, planned_start)
            if isinstance(days_until_start, int) and days_until_start < 10:
                primer_inicio_payload = _metric_payload(days_until_start, inverse=True, exclamation=True)
            else:
                primer_inicio_payload = _metric_payload(None)
        else:
            primer_inicio_payload = _metric_payload(None)

        caldereria_inicio_payload = _metric_payload(dias_caldereria_inicio)
        if caldereria_inicio_payload['class'] == 'analysis-warn-red':
            # Para este indicador el usuario pidió naranja también por encima de 5.
            caldereria_inicio_payload['class'] = 'analysis-warn-orange'

        of_value, client_value = _extract_transport_of_and_client(project)
        rows.append({
            'of': of_value,
            'client': client_value,
            'fecha_pedido': _fmt_day(fecha_pedido),
            'fecha_cliente': _fmt_day(fecha_cliente),
            'fecha_primer_pedido': _fmt_day(first_order),
            'fecha_ultimo_pedido': _fmt_day(last_order),
            'lanzamiento': _fmt_day(lanzamiento_date) if lanzamiento_date else str(lanzamiento_text or '').strip(),
            'material': _fmt_day(material_date) if material_date else str(material_text or '').strip(),
            'caldereria': _fmt_day(caldereria_date) if caldereria_date else str(caldereria_text or '').strip(),
            'pintura': _fmt_day(pintura_date) if pintura_date else str(pintura_text or '').strip(),
            'mecanizado': _fmt_day(mecanizado_date) if mecanizado_date else str(mecanizado_text or '').strip(),
            'tratamiento': _fmt_day(tratamiento_date) if tratamiento_date else str(tratamiento_text or '').strip(),
            'fecha_inicio_planificado': _fmt_day(planned_start),
            'fecha_fin_planificado': _fmt_day(planned_end),
            'dias_caldereria_inicio_display': caldereria_inicio_payload['display'],
            'dias_caldereria_inicio_class': caldereria_inicio_payload['class'],
            'dias_caldereria_inicio_sort': caldereria_inicio_payload['sort'],
            'dias_lanzamiento_primer_display': lanzamiento_payload['display'],
            'dias_lanzamiento_primer_class': lanzamiento_payload['class'],
            'dias_lanzamiento_primer_sort': lanzamiento_payload['sort'],
            'dias_material_primer_display': material_payload['display'],
            'dias_material_primer_class': material_payload['class'],
            'dias_material_primer_sort': material_payload['sort'],
            'dias_primer_inicio_display': primer_inicio_payload['display'],
            'dias_primer_inicio_class': primer_inicio_payload['class'],
            'dias_primer_inicio_sort': primer_inicio_payload['sort'],
            'dias_primer_pedido': '' if dias_primer is None else dias_primer,
            'dias_ultimo_pedido': '' if dias_ultimo is None else dias_ultimo,
            'dias_ultimo_fecha_cliente': '' if dias_ultimo_a_cliente is None else dias_ultimo_a_cliente,
            '_sort_fecha_pedido': fecha_pedido,
        })

    rows.sort(key=lambda row: (row.get('_sort_fecha_pedido') or date.max, row['of'] or '9999', row['client'].lower()))
    for row in rows:
        row.pop('_sort_fecha_pedido', None)
    return render_template('analisis.html', rows=rows)


def _parse_webhook_timestamp(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 1e12:
                stamp = stamp / 1000.0
            return datetime.fromtimestamp(stamp).date()
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except Exception:
        pass
    try:
        stamp = float(text)
        if stamp > 1e12:
            stamp = stamp / 1000.0
        return datetime.fromtimestamp(stamp).date()
    except Exception:
        return None


def _archive_project_from_ready_to_archive(projects, project, *, lane='', timestamp=None):
    """Archive *project* into archived calendar storage and remove it from *projects*."""

    if not isinstance(project, dict):
        return False
    pid = project.get('id')
    if not pid:
        return False
    pid_str = str(pid)

    snapshot_projects = copy.deepcopy(projects)
    snapshot_schedule, _ = schedule_projects(snapshot_projects)
    annotate_schedule_frozen_background(snapshot_schedule)
    archived_tasks = []
    for worker, days in snapshot_schedule.items():
        if worker == UNPLANNED or not isinstance(days, dict):
            continue
        for day, tasks in days.items():
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if str(task.get('pid')) != pid_str:
                    continue
                task_copy = copy.deepcopy(task)
                task_copy['archived_shadow'] = True
                task_copy['frozen'] = True
                task_copy['color'] = '#d9d9d9'
                task_copy['frozen_background'] = 'rgba(128, 128, 128, 0.35)'
                task_copy['pid'] = pid_str
                archived_tasks.append({'worker': worker, 'day': day, 'task': task_copy})

    removed_project = remove_project_and_preserve_schedule(projects, pid)
    archived_source = removed_project or project
    archived_project = copy.deepcopy(archived_source) if isinstance(archived_source, dict) else {}
    if archived_project:
        archived_project['kanban_archived'] = True
        archived_project['kanban_column'] = (
            archived_project.get('kanban_column') or 'Ready to Archive'
        )
        archived_project['id'] = pid_str
        archived_project['archived_shadow'] = True

    project_name = archived_project.get('name') or str(project.get('name') or f'Proyecto {pid_str}')
    client_name = archived_project.get('client') or str(project.get('client') or '')
    store_archived_calendar_entry(
        {
            'pid': pid_str,
            'kanban_id': str(project.get('kanban_id') or ''),
            'name': project_name,
            'client': client_name,
            'lane': lane,
            'timestamp': timestamp,
            'project': archived_project,
            'tasks': archived_tasks,
        }
    )

    extras = load_extra_conflicts()
    conflict_id = str(uuid.uuid4())
    extras.insert(
        0,
        {
            'id': conflict_id,
            'project': project_name,
            'client': client_name,
            'message': 'Se ha archivado.',
            'key': f"kanban-archived-{conflict_id}",
            'source': 'kanbanize',
        },
    )
    save_extra_conflicts(extras)
    return True


def archive_ready_to_archive_projects_if_due(projects, *, today=None):
    """Archive projects that have spent 30+ days in Ready to Archive."""

    if today is None:
        today = local_today()
    changed = False
    while True:
        candidate = None
        for project in projects:
            column_key = normalize_key(project.get('kanban_column'))
            if column_key != READY_TO_ARCHIVE_COLUMN:
                continue
            since_text = str(project.get('ready_to_archive_since') or '').strip()
            if not since_text:
                continue
            since_day = _parse_analysis_date(since_text)
            if not isinstance(since_day, date):
                continue
            if today < since_day + timedelta(days=30):
                continue
            candidate = project
            break
        if not candidate:
            break
        if _archive_project_from_ready_to_archive(projects, candidate):
            changed = True
        else:
            break
    return changed


def _column_to_weekly_phase(column):
    key = normalize_key(column)
    if key in {
        normalize_key('Administración'),
        normalize_key('Oficina Técnica'),
        normalize_key('Pdte. lanzamiento'),
    }:
        return 'Lanzamiento'
    if key in {
        normalize_key('Pendiente Por Recepcionar'),
        normalize_key('Lanzado. Pdte. Req. Técn.'),
    }:
        return 'Material'
    if key in {
        normalize_key('Preparación de material'),
        normalize_key('Listo para iniciar'),
        normalize_key('Montaje'),
        normalize_key('Verificación montaje'),
        normalize_key('Soldadura'),
        normalize_key('Montaje 2º fase'),
        normalize_key('Soldadura final'),
        normalize_key('Verificación final'),
        normalize_key('Enderezado'),
        normalize_key('Pintura'),
        normalize_key('Montaje final'),
    }:
        return 'Caldereria'
    if key in {normalize_key('Mec. Interno'), normalize_key('Mec. Externo')}:
        return 'Mecanizado'
    if key in {normalize_key('Tratamiento')}:
        return 'Tratamiento'
    return ''


WEEKLY_PHASE_ORDER = ['Lanzamiento', 'Material', 'Caldereria', 'Mecanizado', 'Tratamiento']
WEEKLY_PHASE_INDEX = {name: idx for idx, name in enumerate(WEEKLY_PHASE_ORDER)}
WEEKLY_TASK_RETENTION_HOURS = 20


def _coerce_weekly_task(task):
    if isinstance(task, str):
        text = task.strip()
        return {'text': text, 'created_at': datetime.now().isoformat()} if text else None
    if not isinstance(task, dict):
        return None
    text = str(task.get('text') or '').strip()
    if not text:
        return None
    created_at_raw = str(task.get('created_at') or '').strip()
    created_at = created_at_raw or datetime.now().isoformat()
    return {'text': text, 'created_at': created_at}


def _parse_weekly_task_stamp(value):
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = f"{text[:-1]}+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except Exception:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    return stamp


def _normalize_weekly_tasks(entry):
    today_tasks = []
    previous_tasks = []
    changed = False

    for raw_task in (entry.get('today_tasks') or []):
        normalized = _coerce_weekly_task(raw_task)
        if normalized:
            today_tasks.append(normalized)
        else:
            changed = True
    for raw_task in (entry.get('previous_tasks') or []):
        normalized = _coerce_weekly_task(raw_task)
        if normalized:
            previous_tasks.append(normalized)
        else:
            changed = True

    legacy_action = str(entry.get('action') or '')
    if not today_tasks and not previous_tasks and legacy_action.strip():
        for line in legacy_action.splitlines():
            text = line.strip()
            if text:
                today_tasks.append({'text': text, 'created_at': datetime.now().isoformat()})
        changed = True

    now_local = local_now()
    cutoff = now_local - timedelta(hours=WEEKLY_TASK_RETENTION_HOURS)
    kept_today = []
    for task in today_tasks:
        created = _parse_weekly_task_stamp(task.get('created_at'))
        if created and created <= cutoff:
            previous_tasks.append(task)
            changed = True
        else:
            kept_today.append(task)
    today_tasks = kept_today

    if entry.get('today_tasks') != today_tasks or entry.get('previous_tasks') != previous_tasks:
        changed = True
    return today_tasks, previous_tasks, changed


def _build_weekly_tracking_rows(projects, today):
    compras_raw, _colors = load_compras_raw()
    links_table = attach_phase_starts(build_project_links(compras_raw), projects)

    first_order_by_pid = {}
    last_order_by_pid = {}
    for entry in links_table:
        pid = str(entry.get('pid') or '').strip()
        if not pid:
            continue
        details = entry.get('link_details') or []
        dates = []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            order_day = _parse_analysis_date(detail.get('order_date') or detail.get('order_date_raw'))
            if isinstance(order_day, date):
                dates.append(order_day)
        if dates:
            first_order_by_pid[pid] = min(dates)
            last_order_by_pid[pid] = max(dates)

    schedule, _ = schedule_projects(copy.deepcopy(projects))
    caldereria_end_by_pid = {}
    planned_end_by_pid = {}
    caldereria_phases = {'montar', 'montar 2º', 'soldar', 'soldar 2º'}
    for worker, days in (schedule or {}).items():
        if worker == UNPLANNED or not isinstance(days, dict):
            continue
        for day_text, tasks in days.items():
            try:
                day_obj = date.fromisoformat(day_text)
            except Exception:
                continue
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                phase_name = str(task.get('phase') or '').strip().lower()
                if phase_name not in caldereria_phases:
                    continue
                pid = str(task.get('pid') or '').strip()
                if not pid:
                    continue
                prev_end = planned_end_by_pid.get(pid)
                if prev_end is None or day_obj > prev_end:
                    planned_end_by_pid[pid] = day_obj
                prev = caldereria_end_by_pid.get(pid)
                if prev is None or day_obj > prev:
                    caldereria_end_by_pid[pid] = day_obj
    phase_starts = phase_start_map(projects)

    cards_by_id = {}
    for entry in load_kanban_cards():
        if not isinstance(entry, dict):
            continue
        card = entry.get('card') or {}
        if not isinstance(card, dict):
            continue
        cid = card.get('taskid') or card.get('cardId') or card.get('id')
        if not cid:
            continue
        cards_by_id.setdefault(str(cid), []).append(entry)

    persisted = load_weekly_tracking_data()
    updated_state = dict(persisted)
    rows = []
    weekly_lane_keys = {
        normalize_key('Acero al Carbono'),
        normalize_key('Inoxidable - Aluminio'),
    }

    for project in projects:
        pid = str(project.get('id') or '').strip()
        if not pid:
            continue
        if project.get('kanban_archived'):
            continue
        if str(project.get('source') or '').strip().lower() != 'api' and not project.get('kanban_id'):
            continue

        column = str(project.get('kanban_column') or '').strip()
        phase = _column_to_weekly_phase(column)
        if not phase:
            continue

        card = _project_kanban_card(project)
        project_lane = (
            str(project.get('kanban_lane') or '').strip()
            or str((card or {}).get('lanename') or (card or {}).get('laneName') or (card or {}).get('lane') or '').strip()
        )
        if normalize_key(project_lane) not in weekly_lane_keys:
            continue

        display_fields = project.get('kanban_display_fields') or {}
        if not isinstance(display_fields, dict):
            display_fields = {}

        lanzamiento_date = _parse_analysis_date(display_fields.get('LANZAMIENTO') or _get_custom_field_text(card, 'LANZAMIENTO'))
        material_date = _parse_analysis_date(display_fields.get('MATERIAL') or _get_custom_field_text(card, 'MATERIAL'))
        mecanizado_date = _parse_analysis_date(display_fields.get('MECANIZADO') or _get_custom_field_text(card, 'MECANIZADO'))
        tratamiento_date = _parse_analysis_date(display_fields.get('TRATAMIENTO') or _get_custom_field_text(card, 'TRATAMIENTO'))
        caldereria_date = _parse_analysis_date(display_fields.get('CALDERERIA') or _get_custom_field_text(card, 'CALDERERIA', 'CALDERERÍA'))
        fecha_pedido = _parse_analysis_date(
            display_fields.get('Fecha pedido')
            or display_fields.get('Fecha Pedido')
            or _get_custom_field_text(card, 'Fecha pedido', 'Fecha Pedido')
        )
        fecha_cliente = _parse_analysis_date(
            display_fields.get('Fecha cliente')
            or display_fields.get('Fecha Cliente')
            or _get_custom_field_text(card, 'Fecha cliente', 'Fecha Cliente')
        )
        launch_margin_days = business_days_elapsed(fecha_pedido, fecha_cliente)
        launch_low_margin = isinstance(launch_margin_days, int) and launch_margin_days < 25

        target_dates = []
        for label in ('TRATAMIENTO', 'PINTURA', 'MECANIZADO'):
            parsed = _parse_analysis_date(display_fields.get(label) or _get_custom_field_text(card, label))
            if isinstance(parsed, date):
                target_dates.append(parsed)
        earliest_target = min(target_dates) if target_dates else None

        first_order = first_order_by_pid.get(pid)
        last_order = last_order_by_pid.get(pid)

        cid = str(project.get('kanban_id') or '').strip()
        phase_entry_day = None
        if cid and cid in cards_by_id:
            history = cards_by_id[cid]
            valid_columns = {
                'Tratamiento': {normalize_key('Tratamiento')},
                'Mecanizado': {normalize_key('Mec. Interno'), normalize_key('Mec. Externo')},
            }
            wanted_columns = valid_columns.get(phase, set())
            if wanted_columns:
                for entry in history:
                    card_snapshot = entry.get('card') or {}
                    snap_col = card_snapshot.get('columnname') or card_snapshot.get('columnName') or card_snapshot.get('column') or entry.get('last_column')
                    if normalize_key(snap_col) not in wanted_columns:
                        continue
                    stamp_day = _parse_webhook_timestamp(entry.get('timestamp'))
                    if isinstance(stamp_day, date):
                        if phase_entry_day is None or stamp_day < phase_entry_day:
                            phase_entry_day = stamp_day

        candidate_origin = ''
        launch_from_order_elapsed = False
        tratamiento_elapsed = None
        mecanizado_elapsed = None

        if phase == 'Lanzamiento':
            if isinstance(lanzamiento_date, date):
                if isinstance(first_order, date) and first_order > lanzamiento_date:
                    candidate_origin = 'Lanzamiento'
                elif isinstance(last_order, date) and last_order > lanzamiento_date:
                    candidate_origin = 'Lanzamiento'
                elif today >= lanzamiento_date:
                    cutoff = _add_business_days(lanzamiento_date, 2)
                    if not isinstance(first_order, date) or first_order > cutoff:
                        candidate_origin = 'Lanzamiento'
            else:
                pedido_elapsed = business_days_elapsed(fecha_pedido, today)
                if isinstance(pedido_elapsed, int) and pedido_elapsed >= 10:
                    candidate_origin = 'Lanzamiento'
                    launch_from_order_elapsed = True

        elif phase == 'Material':
            if isinstance(caldereria_date, date) and today > caldereria_date:
                candidate_origin = 'Material'

        elif phase == 'Caldereria':
            planned_caldereria_end = caldereria_end_by_pid.get(pid)
            if isinstance(planned_caldereria_end, date) and isinstance(earliest_target, date) and planned_caldereria_end > earliest_target:
                candidate_origin = 'Caldereria'

        elif phase == 'Tratamiento':
            if isinstance(phase_entry_day, date):
                elapsed = business_days_elapsed(phase_entry_day, today)
                if isinstance(elapsed, int):
                    tratamiento_elapsed = elapsed
                if isinstance(elapsed, int) and elapsed > 5:
                    candidate_origin = 'Tratamiento'

        elif phase == 'Mecanizado':
            if isinstance(phase_entry_day, date):
                elapsed = business_days_elapsed(phase_entry_day, today)
                if isinstance(elapsed, int):
                    mecanizado_elapsed = elapsed
                if isinstance(elapsed, int) and elapsed > 10:
                    candidate_origin = 'Mecanizado'

        saved = persisted.get(pid) or {}
        saved_origin = str(saved.get('origin') or '').strip()
        saved_phase = str(saved.get('phase') or '').strip()

        origin = candidate_origin
        origin_phase = phase if origin else ''
        inherited_previous_origin = False

        current_idx = WEEKLY_PHASE_INDEX.get(phase)
        saved_idx = WEEKLY_PHASE_INDEX.get(saved_phase)
        if (
            not origin
            and saved_origin
            and saved_phase
            and current_idx is not None
            and saved_idx is not None
            and saved_idx < current_idx
        ):
            origin = saved_origin
            origin_phase = saved_phase
            inherited_previous_origin = True

        today_tasks = []
        for task in (saved.get('today_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                today_tasks.append(normalized)
        previous_tasks = []
        for task in (saved.get('previous_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                previous_tasks.append(normalized)
        action_lines = [task.get('text') for task in previous_tasks + today_tasks if str(task.get('text') or '').strip()]
        action_value = '\n'.join(action_lines)

        updated_state[pid] = {
            'origin': origin,
            'phase': origin_phase,
            'action': action_value,
            'today_tasks': today_tasks,
            'previous_tasks': previous_tasks,
        }

        name = str(project.get('name') or '').strip()
        client = str(project.get('client') or '').strip()
        of_value, analysis_client = _extract_transport_of_and_client(project)
        project_display = name
        if client and client not in name:
            project_display = f"{name} - {client}" if name else client

        phase_date_lookup = {
            'Lanzamiento': lanzamiento_date,
            'Material': material_date,
            'Mecanizado': mecanizado_date,
            'Tratamiento': tratamiento_date,
        }
        current_phase_date = phase_date_lookup.get(phase)
        current_phase_date_text = _format_analysis_date(current_phase_date)
        phase_display = phase

        phase_text = (
            f"Fase actual: {phase_display}, porque el proyecto está en la columna '{column or 'sin columna'}' de Kanbanize."
        )

        if origin:
            status_text = f"La fase actual está en rojo porque se detecta atraso con origen en {origin}."
        else:
            status_text = "La fase actual está en verde porque no se detecta atraso con las reglas actuales."

        if inherited_previous_origin and origin:
            origin_text = (
                f"Origen del atraso: {origin}, se mantiene porque una fase anterior ya cumplió criterio de atraso "
                f"y en la fase actual ({phase}) todavía no hay un nuevo origen."
            )
        elif origin == 'Lanzamiento':
            if launch_from_order_elapsed:
                origin_text = "Origen del atraso: Lanzamiento, porque no hay fecha de lanzamiento y ya han pasado al menos 10 días laborables desde la fecha de pedido."
            else:
                origin_text = "Origen del atraso: Lanzamiento, porque el primer pedido llegó tarde respecto al hito de lanzamiento."
        elif origin == 'Material':
            origin_text = "Origen del atraso: Material, porque ya se superó la fecha objetivo de calderería."
        elif origin == 'Caldereria':
            origin_text = "Origen del atraso: Calderería, porque la planificación termina después de la fecha objetivo más cercana."
        elif origin == 'Tratamiento':
            origin_text = "Origen del atraso: Tratamiento, porque la fase lleva más de 5 días laborables desde su entrada."
        elif origin == 'Mecanizado':
            origin_text = "Origen del atraso: Mecanizado, porque la fase lleva más de 10 días laborables desde su entrada."
        else:
            origin_text = "Origen del atraso: no aplica en este momento."

        next_phase_text = ''
        if not origin and isinstance(current_phase_date, date):
            remaining_days = business_days_elapsed(today, current_phase_date)
            if isinstance(remaining_days, int):
                current_idx = WEEKLY_PHASE_INDEX.get(phase)
                following = WEEKLY_PHASE_ORDER[current_idx + 1] if isinstance(current_idx, int) and (current_idx + 1) < len(WEEKLY_PHASE_ORDER) else ''
                if following:
                    next_phase_text = f"Faltan {max(remaining_days, 0)} días para {following}."

        date_details = [
            {'label': 'Fecha pedido', 'value': _format_analysis_date(fecha_pedido)},
            {'label': 'Fecha cliente', 'value': _format_analysis_date(fecha_cliente)},
            {'label': 'Fecha primer pedido', 'value': _format_analysis_date(first_order)},
            {'label': 'Fecha último pedido', 'value': _format_analysis_date(last_order)},
            {'label': 'LANZAMIENTO', 'value': _format_analysis_date(lanzamiento_date)},
            {'label': 'MATERIAL', 'value': _format_analysis_date(material_date)},
            {'label': 'CALDERERIA', 'value': _format_analysis_date(caldereria_date)},
            {'label': 'PINTURA', 'value': _format_analysis_date(_parse_analysis_date(display_fields.get('PINTURA') or _get_custom_field_text(card, 'PINTURA', 'PINTADO')))},
            {'label': 'MECANIZADO', 'value': _format_analysis_date(mecanizado_date)},
            {'label': 'TRATAMIENTO', 'value': _format_analysis_date(tratamiento_date)},
            {'label': 'Fecha inicio planificado', 'value': _format_analysis_date(_parse_analysis_date((phase_starts.get(pid) or {}).get('montar')))},
            {'label': 'Fecha fin planificado', 'value': _format_analysis_date(planned_end_by_pid.get(pid))},
        ]

        rows.append(
            {
                'pid': pid,
                'image': project.get('image') or '',
                'project_display': project_display,
                'analysis_of': of_value,
                'analysis_client': analysis_client,
                'phase': phase,
                'phase_display': phase_display,
                'origin': origin,
                'action': action_value,
                'today_tasks': today_tasks,
                'previous_tasks': previous_tasks,
                'first_order': first_order,
                'caldereria_date': caldereria_date,
                'tratamiento_elapsed_days': tratamiento_elapsed,
                'mecanizado_elapsed_days': mecanizado_elapsed,
                'launch_low_margin': launch_low_margin,
                'launch_from_order_elapsed': launch_from_order_elapsed,
                'is_delayed': bool(origin),
                'explanation_phase': phase_text,
                'explanation_status': status_text,
                'explanation_origin': origin_text,
                'explanation_next': next_phase_text,
                'date_details': date_details,
            }
        )

    # Remove state for projects no longer shown.
    visible_pids = {row['pid'] for row in rows}
    for pid in list(updated_state.keys()):
        if pid not in visible_pids:
            updated_state.pop(pid, None)

    phase_counts = Counter(row['phase'] for row in rows)
    for row in rows:
        origin = row.get('origin')
        motive = ''
        if origin == 'Lanzamiento':
            launch_motives = []
            if row.get('launch_from_order_elapsed'):
                launch_motives.append('2 semanas o más desde el pedido.')
            if phase_counts.get(row['phase'], 0) >= 4:
                launch_motives.append('Sobrecarga')
            if row.get('launch_low_margin'):
                launch_motives.append('Poco margen')
            motive = ' / '.join(launch_motives)
        elif origin == 'Material':
            first_order = row.get('first_order')
            caldereria_date = row.get('caldereria_date')
            gap = business_days_elapsed(first_order, caldereria_date)
            if isinstance(gap, int):
                motive = 'Proveedor' if gap >= 12 else 'Poco margen'
        elif origin == 'Caldereria':
            motive = 'Alargándose'
        elif origin == 'Tratamiento':
            days = row.get('tratamiento_elapsed_days')
            motive = f'Tratamiento ({days} días)' if isinstance(days, int) else 'Tratamiento'
        elif origin == 'Mecanizado':
            days = row.get('mecanizado_elapsed_days')
            motive = f'Mecanizado ({days} días)' if isinstance(days, int) else 'Mecanizado'
        row['motive'] = motive

        if motive:
            motive_text = f"Motivo: {motive}."
        else:
            motive_text = "Motivo: sin causa adicional definida para este caso."
        row['explanation'] = (
            f"{row.get('explanation_phase', '')} {row.get('explanation_status', '')} "
            f"{row.get('explanation_origin', '')} {motive_text} {row.get('explanation_next', '')}"
        ).strip()

    rows.sort(key=lambda item: ((item.get('project_display') or '').lower(), item.get('pid') or ''))

    if updated_state != persisted:
        save_weekly_tracking_data(updated_state)

    return rows


@app.route('/seguimiento-semanal')
def weekly_tracking_view():
    projects = get_visible_projects()
    rows = _build_weekly_tracking_rows(projects, local_today())
    previous_meeting_date = (local_today() - timedelta(days=7)).strftime('%d/%m/%Y')
    return render_template('seguimiento_semanal.html', rows=rows, previous_meeting_date=previous_meeting_date)


@app.route('/seguimiento-semanal/action/<pid>', methods=['POST'])
def update_weekly_tracking_action(pid):
    payload = request.get_json(silent=True) or {}
    pid_key = str(pid or '').strip()
    if not pid_key:
        return jsonify({'error': 'PID inválido'}), 400

    data = load_weekly_tracking_data()
    entry = data.setdefault(pid_key, {'origin': '', 'phase': '', 'action': '', 'today_tasks': [], 'previous_tasks': []})
    if isinstance(payload.get('today_tasks'), list) or isinstance(payload.get('previous_tasks'), list):
        today_tasks = []
        previous_tasks = []
        for task in (payload.get('today_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                today_tasks.append(normalized)
        for task in (payload.get('previous_tasks') or []):
            normalized = _coerce_weekly_task(task)
            if normalized:
                previous_tasks.append(normalized)
        entry['today_tasks'] = today_tasks
        entry['previous_tasks'] = previous_tasks
        entry['action'] = '\n'.join(
            [task.get('text') for task in previous_tasks + today_tasks if str(task.get('text') or '').strip()]
        )
    else:
        action_text = str(payload.get('action') or '')
        entry['action'] = action_text
        fallback_tasks = []
        for line in action_text.splitlines():
            text = line.strip()
            if text:
                fallback_tasks.append({'text': text, 'created_at': datetime.now().isoformat()})
        entry['today_tasks'] = fallback_tasks
        entry['previous_tasks'] = []
    save_weekly_tracking_data(data)
    return jsonify({'status': 'ok'}), 200


@app.get('/seguimiento-semanal/acta')
def weekly_tracking_minutes_pdf():
    rows = _build_weekly_tracking_rows(get_visible_projects(), local_today())
    rows = [r for r in rows if (r.get('today_tasks') or r.get('previous_tasks') or str(r.get('action') or '').strip())]
    if not rows:
        return Response('No hay acciones para generar el acta.', status=400, mimetype='text/plain')
    previous_meeting_date = (local_today() - timedelta(days=7)).strftime('%d/%m/%Y')
    print_css_href = url_for('static', filename='print.css')
    html = render_template_string(
        """
        <html lang="es">
        <head>
          <meta charset="utf-8">
          <title>Acta de seguimiento semanal</title>
          <link rel="stylesheet" href="{{ print_css_href }}">
          <style>
            body{font-family:Arial,Helvetica,sans-serif;font-size:12px;}
            .row{page-break-inside:avoid;border:1px solid #ccc;margin:0 0 12px 0;padding:8px;}
            .title{font-size:16px;font-weight:700;margin:0 0 8px 0;}
            .img{max-width:360px;max-height:220px;display:block;margin:6px 0;}
            .row-grid{display:flex;gap:14px;align-items:flex-start;}
            .left-col{flex:1 1 auto;min-width:0;}
            .right-col{flex:0 0 250px;border-left:1px solid #ddd;padding-left:10px;}
            .dates-title{font-weight:700;margin-bottom:6px;}
            .date-line{font-size:11px;line-height:1.35;margin:0 0 4px 0;}
            .tasks-wrap{margin-top:12px;}
            .tasks-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
            .tasks-col{border:1px solid #ddd;border-radius:6px;padding:8px;background:#fff;}
            .tasks-title{font-weight:700;margin:0 0 6px 0;font-size:12px;}
            .task-item{display:flex;align-items:flex-start;gap:6px;font-size:13px;line-height:1.35;margin:0 0 4px 0;}
            .task-check{font-size:14px;line-height:1;display:inline-block;margin-top:1px;}
            .task-text{display:inline-block;word-break:break-word;}
            .task-empty{font-size:12px;color:#777;margin:0;}
          </style>
        </head>
        <body>
          <h1>Acta de seguimiento semanal</h1>
          {% for row in rows %}
          <section class="row">
            <div class="title">{{ row.project_display }}</div>
            <div class="row-grid">
              <div class="left-col">
                {% if row.image_url %}<img class="img" src="{{ row.image_url }}" alt="{{ row.project_display }}">{% endif %}
                <div><strong>Fase:</strong> {{ row.phase }}</div>
                <div><strong>Origen:</strong> {{ row.origin }}</div>
                <div><strong>Motivo:</strong> {{ row.motive }}</div>
                <div class="tasks-wrap">
                  <div class="tasks-grid">
                    <section class="tasks-col">
                      <h3 class="tasks-title">Tareas última reunión ({{ previous_meeting_date }})</h3>
                      {% if row.previous_tasks %}
                        {% for task in row.previous_tasks %}
                        <div class="task-item"><span class="task-check">☐</span><span class="task-text">{{ task.text }}</span></div>
                        {% endfor %}
                      {% else %}
                        <p class="task-empty">Sin tareas.</p>
                      {% endif %}
                    </section>
                    <section class="tasks-col">
                      <h3 class="tasks-title">Tareas hoy</h3>
                      {% if row.today_tasks %}
                        {% for task in row.today_tasks %}
                        <div class="task-item"><span class="task-check">☐</span><span class="task-text">{{ task.text }}</span></div>
                        {% endfor %}
                      {% else %}
                        <p class="task-empty">Sin tareas.</p>
                      {% endif %}
                    </section>
                  </div>
                </div>
              </div>
              <div class="right-col">
                <div class="dates-title">Fechas</div>
                {% for item in row.date_details or [] %}
                <div class="date-line"><strong>{{ item.label }}:</strong> {{ item.value or '-' }}</div>
                {% endfor %}
              </div>
            </div>
          </section>
          {% endfor %}
        </body>
        </html>
        """,
        rows=[
            {
                **row,
                'image_url': (
                    urllib.parse.urljoin(request.url_root, url_for('static', filename=row.get('image')))
                    if row.get('image') else ''
                ),
            }
            for row in rows
        ],
        previous_meeting_date=previous_meeting_date,
        print_css_href=print_css_href,
    )
    if pdfkit is None:
        return Response('No se pudo generar el PDF del acta porque pdfkit no está instalado.', status=500, mimetype='text/plain')

    try:
        pdf = pdfkit.from_string(
            html,
            False,
            configuration=config,
            options={'enable-local-file-access': '', 'quiet': '', 'print-media-type': ''},
            css=[os.path.join(app.root_path, 'static', 'style.css')],
        )
    except Exception:
        app.logger.exception('Error generando acta de seguimiento')
        return Response('No se pudo generar el PDF del acta.', status=500, mimetype='text/plain')
    response = Response(pdf, mimetype='application/pdf')
    response.headers['Content-Disposition'] = f'attachment; filename="acta_seguimiento_{datetime.now().strftime("%Y-%m-%d_%H-%M")}.pdf"'
    return response




def _build_project_calendar_rows(projects):
    compras_raw, _colors = load_compras_raw()
    links_table = attach_phase_starts(build_project_links(compras_raw), projects)

    orders_by_pid = {}
    for entry in links_table:
        pid = str(entry.get('pid') or '').strip()
        if not pid:
            continue
        details = entry.get('link_details') or []
        for detail in details:
            if not isinstance(detail, dict):
                continue
            order_day = _parse_analysis_date(detail.get('order_date') or detail.get('order_date_raw'))
            if isinstance(order_day, date):
                orders_by_pid.setdefault(pid, []).append(order_day)

    phase_starts = phase_start_map(projects)
    rows = []
    for project in projects:
        if project.get('kanban_archived'):
            continue
        if str(project.get('source') or '').strip().lower() != 'api' and not project.get('kanban_id'):
            continue
        pid = str(project.get('id') or '').strip()
        if not pid:
            continue

        card = _project_kanban_card(project)
        display_fields = project.get('kanban_display_fields') or {}
        if not isinstance(display_fields, dict):
            display_fields = {}

        def _pick_date(*labels):
            for label in labels:
                parsed = _parse_analysis_date(display_fields.get(label) or _get_custom_field_text(card, label))
                if isinstance(parsed, date):
                    return parsed
            return None

        ordered = sorted(orders_by_pid.get(pid, []))
        planned_start = _parse_analysis_date((phase_starts.get(pid) or {}).get('montar'))

        schedule_map, _ = schedule_projects(copy.deepcopy([project]))
        planned_end = None
        for worker, days in (schedule_map or {}).items():
            if worker == UNPLANNED or not isinstance(days, dict):
                continue
            for day_text, tasks in days.items():
                if not isinstance(tasks, list):
                    continue
                if not any(str(task.get('pid') or '') == pid for task in tasks if isinstance(task, dict)):
                    continue
                try:
                    day_obj = date.fromisoformat(day_text)
                except Exception:
                    continue
                if planned_end is None or day_obj > planned_end:
                    planned_end = day_obj

        date_points = [
            {'label': 'Fecha pedido', 'date': _pick_date('Fecha pedido', 'Fecha Pedido')},
            {'label': 'Fecha cliente', 'date': _pick_date('Fecha cliente', 'Fecha Cliente')},
            {'label': 'Fecha de Inicio planificado', 'date': planned_start},
            {'label': 'Fecha de fin planificado', 'date': planned_end},
            {'label': 'Fecha de LANZAMIENTO', 'date': _pick_date('LANZAMIENTO')},
            {'label': 'Fecha de MATERIAL', 'date': _pick_date('MATERIAL')},
            {'label': 'Fecha de CALDERERIA', 'date': _pick_date('CALDERERIA', 'CALDERERÍA')},
            {'label': 'Fecha de PINTURA', 'date': _pick_date('PINTURA', 'PINTADO')},
            {'label': 'Fecha de MECANIZADO', 'date': _pick_date('MECANIZADO')},
            {'label': 'Fecha de TRATAMIENTO', 'date': _pick_date('TRATAMIENTO')},
            {'label': 'Fecha del primer pedido del proyecto', 'date': ordered[0] if ordered else None},
            {'label': 'Fecha del último pedido del proyecto', 'date': ordered[-1] if ordered else None},
        ]

        fecha_cliente_date = _pick_date('Fecha cliente', 'Fecha Cliente')
        fecha_lanzamiento_date = _pick_date('LANZAMIENTO')

        rows.append({
            'pid': pid,
            'title': str(project.get('name') or ''),
            'client': str(project.get('client') or ''),
            'is_delayed': isinstance(fecha_cliente_date, date) and fecha_cliente_date < local_today(),
            'has_lanzamiento': isinstance(fecha_lanzamiento_date, date),
            'dates': [
                {'label': item['label'], 'date': item['date'].isoformat()}
                for item in date_points
                if isinstance(item.get('date'), date)
            ],
        })

    rows.sort(key=lambda item: ((item.get('title') or '').lower(), (item.get('client') or '').lower(), item.get('pid') or ''))
    return rows


@app.route('/calendario-proyectos')
def project_calendar_view():
    projects = get_visible_projects()
    rows = _build_project_calendar_rows(projects)
    today = local_today()
    month_start = date(today.year, today.month, 1)
    month_meta = []
    for offset in range(-3, 7):
        year = month_start.year + (month_start.month - 1 + offset) // 12
        month = (month_start.month - 1 + offset) % 12 + 1
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        first_weekday = start.weekday()
        day_cursor = start - timedelta(days=first_weekday)
        weeks = []
        while True:
            week = []
            for _ in range(7):
                in_month = start <= day_cursor <= end
                week.append({
                    'date': day_cursor.isoformat() if in_month else '',
                    'day': day_cursor.day if in_month else '',
                })
                day_cursor += timedelta(days=1)
            weeks.append(week)
            if day_cursor > end and day_cursor.weekday() == 0:
                break

        month_meta.append({
            'label': start.strftime('%B %Y').capitalize(),
            'start': start.isoformat(),
            'end': end.isoformat(),
            'weeks': weeks,
        })

    range_start = date.fromisoformat(month_meta[0]['start']) if month_meta else month_start
    range_end = date.fromisoformat(month_meta[-1]['end']) if month_meta else month_start
    return render_template(
        'calendario_proyectos.html',
        projects=rows,
        month_meta=month_meta,
        range_start=range_start.isoformat(),
        range_end=range_end.isoformat(),
        today_iso=today.isoformat(),
    )

@app.route('/transportes')
def transportes_view():
    projects = load_projects() or []
    options = load_transport_options()
    rows = []
    for project in projects:
        if not project.get('transport_visible'):
            continue
        if project.get('transport_delivered'):
            continue
        pid = project.get('id')
        if not pid:
            continue
        of_digits, client_name = _extract_transport_of_and_client(project)
        image_url = ''
        image_path = str(project.get('image') or '').strip()
        if image_path:
            image_url = url_for('static', filename=image_path)
        else:
            for att in project.get('kanban_attachments') or []:
                if not isinstance(att, dict):
                    continue
                url = str(att.get('url') or '').strip()
                name = str(att.get('name') or '').strip().lower()
                if not url:
                    continue
                if re.search(r'\.(png|jpe?g|gif|webp|bmp|svg)(?:$|\?)', name) or re.search(r'\.(png|jpe?g|gif|webp|bmp|svg)(?:$|\?)', url.lower()):
                    image_url = url
                    break

        rows.append({
            'pid': str(pid),
            'of': of_digits,
            'client': client_name,
            'image_url': image_url,
            'bulto': str(project.get('transport_bulto') or ''),
            'lugar': str(project.get('transport_lugar') or ''),
            'embalado': bool(project.get('transport_embalado')),
            'transportista': str(project.get('transport_transportista') or ''),
            'project_name': project.get('name') or '',
        })

    rows.sort(key=lambda item: ((item.get('of') or '9999'), (item.get('client') or '').lower(), (item.get('project_name') or '').lower()))
    for idx, row in enumerate(rows, start=1):
        row['number'] = idx

    return render_template('transportes.html', rows=rows, location_options=options['locations'], carrier_options=options['carriers'])


@app.route('/toggle_transport_visibility', methods=['POST'])
def toggle_transport_visibility():
    data = request.get_json(silent=True) or {}
    pid = str(data.get('pid') or '').strip()
    visible = bool(data.get('visible'))
    if not pid:
        return jsonify({'error': 'Falta proyecto'}), 400

    projects = load_projects() or []
    changed = False
    for project in projects:
        if str(project.get('id')) != pid:
            continue
        project['transport_visible'] = visible
        if visible:
            project['transport_delivered'] = False
        changed = True
        break

    if not changed:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    save_projects(projects)
    broadcast_event({'type': 'kanban_update'})
    return jsonify({'ok': True})


@app.route('/update_transport_row', methods=['POST'])
def update_transport_row():
    data = request.get_json(silent=True) or {}
    pid = str(data.get('pid') or '').strip()
    field = str(data.get('field') or '').strip()
    value = data.get('value')
    if not pid or field not in {'bulto', 'lugar', 'transportista', 'embalado'}:
        return jsonify({'error': 'Datos inválidos'}), 400

    projects = load_projects() or []
    target = None
    for project in projects:
        if str(project.get('id')) == pid:
            target = project
            break
    if target is None:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    if field == 'bulto':
        target['transport_bulto'] = str(value or '').strip()
    elif field == 'lugar':
        target['transport_lugar'] = str(value or '').strip()
        if target['transport_lugar']:
            options = load_transport_options()
            merged = options['locations'] + [target['transport_lugar']]
            options['locations'] = _dedupe_text_options(merged)
            save_transport_options(options)
    elif field == 'transportista':
        target['transport_transportista'] = str(value or '').strip()
        if target['transport_transportista']:
            options = load_transport_options()
            merged = options['carriers'] + [target['transport_transportista']]
            options['carriers'] = _dedupe_text_options(merged)
            save_transport_options(options)
    elif field == 'embalado':
        target['transport_embalado'] = bool(value)

    save_projects(projects)
    broadcast_event({'type': 'kanban_update'})
    return jsonify({'ok': True})


@app.route('/mark_transport_delivered', methods=['POST'])
def mark_transport_delivered():
    data = request.get_json(silent=True) or {}
    pid = str(data.get('pid') or '').strip()
    if not pid:
        return jsonify({'error': 'Falta proyecto'}), 400

    projects = load_projects() or []
    changed = False
    for project in projects:
        if str(project.get('id')) != pid:
            continue
        project['transport_delivered'] = True
        project['transport_visible'] = False
        changed = True
        break

    if not changed:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    save_projects(projects)
    broadcast_event({'type': 'kanban_update'})
    return jsonify({'ok': True})


@app.route('/taller')
def taller_view():
    include_optional_phases = optional_phases_enabled()
    projects = get_visible_projects(include_optional_phases=include_optional_phases)
    schedule, _conflicts, _archived_entries, archived_project_map = build_schedule_with_archived(
        projects, include_optional_phases=include_optional_phases
    )
    annotate_schedule_frozen_background(schedule)
    today = local_today()
    workers = active_workers(today)
    schedule = {w: d for w, d in schedule.items() if w in workers}
    _sort_cell_tasks(schedule)
    days = _build_taller_days(today, 3)
    columns = []
    for worker in workers:
        worker_days = schedule.get(worker, {})
        worker_tasks = []
        seen = set()
        for day in days:
            tasks = worker_days.get(day.isoformat(), [])
            for task in tasks:
                if task.get('phase') == 'vacaciones':
                    continue
                key = (
                    str(task.get('pid')),
                    task.get('phase'),
                    task.get('part'),
                )
                if key in seen:
                    continue
                seen.add(key)
                entry = task.copy()
                entry['day'] = day
                worker_tasks.append(entry)
        worker_tasks.sort(key=lambda item: (item.get('day') or date.max, item.get('start', 0)))
        columns.append({'worker': worker, 'tasks': worker_tasks[:3]})
    project_map = {}
    for project in projects:
        project.setdefault('kanban_attachments', [])
        pid = project.get('id')
        if pid is None:
            continue
        project_map[pid] = project
        project_map[str(pid)] = project
    for pid, info in archived_project_map.items():
        info.setdefault('kanban_attachments', [])
        project_map[pid] = info
        project_map[str(pid)] = info
    if len(columns) <= 1:
        columns_first = columns
        columns_second = []
    else:
        split_index = len(columns) // 2
        columns_first = columns[:split_index]
        columns_second = columns[split_index:]
    return render_template(
        'taller.html',
        columns_first=columns_first,
        columns_second=columns_second,
        project_data=project_map,
    )


@app.route('/complete')
def complete():
    include_optional_phases = optional_phases_enabled()
    projects = get_visible_projects(include_optional_phases=include_optional_phases)
    schedule, conflicts, _archived_entries, archived_project_map = build_schedule_with_archived(
        projects, include_optional_phases=include_optional_phases
    )
    annotate_schedule_frozen_background(schedule)
    today = local_today()
    worker_notes_raw = load_worker_notes()
    manual_entries = load_manual_bucket_entries()
    if not include_optional_phases:
        manual_entries = [
            entry for entry in manual_entries if phase_base(entry.get('phase')) not in OPTIONAL_PHASES
        ]
    deactivated_workers = _inactive_worker_date_map()
    calendar_workers = planner_calendar_workers(today)
    unplanned_raw = []
    if UNPLANNED in schedule:
        for day, tasks in schedule.pop(UNPLANNED).items():
            for t in tasks:
                item = t.copy()
                item['day'] = day
                unplanned_raw.append(item)
    groups = {}
    for item in unplanned_raw:
        pid = item['pid']
        phase = item['phase']
        part = item.get('part')
        proj = groups.setdefault(
            pid,
            {
                'project': item['project'],
                'client': item['client'],
                'material_date': item.get('material_date'),
                'due_date': item.get('due_date'),
                'phases': {},
            },
        )
        phase_key = (phase, part) if part is not None else (phase, None)
        ph = proj['phases'].setdefault(
            phase_key,
            {
                'project': item['project'],
                'client': item['client'],
                'pid': pid,
                'phase': phase,
                'part': part,
                'color': item.get('color'),
                'due_date': item.get('due_date'),
                'start_date': item.get('start_date'),
                'day': item.get('day'),
                'hours': 0,
                'late': item.get('late', False),
                'due_status': item.get('due_status'),
                'blocked': item.get('blocked', False),
                'frozen': item.get('frozen', False),
                'frozen_background': item.get('frozen_background'),
                'auto': item.get('auto', False),
            },
        )
        ph['hours'] += item.get('hours', 0)
        if item.get('day') and (ph['day'] is None or item['day'] < ph['day']):
            ph['day'] = item['day']
        if item.get('start_date') and (
            ph['start_date'] is None or item['start_date'] < ph['start_date']
        ):
            ph['start_date'] = item['start_date']
        if item.get('due_date') and (
            ph['due_date'] is None or item['due_date'] < ph['due_date']
        ):
            ph['due_date'] = item['due_date']
        if item.get('late'):
            ph['late'] = True
        if item.get('blocked'):
            ph['blocked'] = True
        if item.get('frozen'):
            ph['frozen'] = True
            bg = item.get('frozen_background')
            if bg:
                ph['frozen_background'] = bg
        if item.get('auto'):
            ph['auto'] = True
    unplanned_list = []
    for pid, data in groups.items():
        unplanned_list.append(
            {
                'pid': pid,
                'project': data['project'],
                'client': data['client'],
                'material_date': data.get('material_date'),
                'due_date': data.get('due_date'),
                'tasks': list(data['phases'].values()),
            }
        )
    schedule = _schedule_with_deactivated_rows(schedule, calendar_workers, deactivated_workers)
    for p in projects:
        if p.get('due_date'):
            try:
                p['met'] = date.fromisoformat(p['end_date']) <= date.fromisoformat(p['due_date'])
            except ValueError:
                p['met'] = False
        else:
            p['met'] = False
    notes = load_notes()
    extra = load_extra_conflicts()
    conflicts.extend(extra)
    dismissed = load_dismissed()
    conflicts = [
        c
        for c in conflicts
        if c['key'] not in dismissed and c.get('message') != 'No se cumple la fecha de entrega'
    ]

    sort_option = request.args.get('sort', 'created')
    orig_order = {p['id']: idx for idx, p in enumerate(projects)}

    project_filter = request.args.get('project', '').strip()
    client_filter = request.args.get('client', '').strip()
    q_filter = request.args.get('q', '').strip()
    if q_filter:
        project_filter = q_filter
        client_filter = ''
    project_id_filter = request.args.get('project_id', '').strip()
    project_name_from_id = ''
    if project_id_filter:
        for p in projects:
            pid = p.get('id')
            if pid is not None and str(pid) == project_id_filter:
                project_name_from_id = (p.get('name') or '').strip()
                break
    text_filter = request.args.get('q', '').strip()
    if not text_filter:
        text_filter = " ".join(
            chunk for chunk in [project_filter, client_filter] if chunk
        ).strip()
    if project_name_from_id and not text_filter:
        text_filter = project_name_from_id
    project_filter = text_filter
    client_filter = ''
    filter_active = bool(text_filter or project_id_filter)

    def matches_filters(name, client, pid=None, phase_name=''):
        if project_id_filter:
            pid_text = '' if pid is None else str(pid)
            if pid_text != project_id_filter:
                return False
        if not text_filter:
            return True
        terms = [token.strip().lower() for token in text_filter.split() if token.strip()]
        normalized_phase = phase_base(phase_name or '')
        phase_for_filter = '' if normalized_phase == 'lanzamiento' else (phase_name or '')
        haystack = " ".join([(name or ''), (client or ''), phase_for_filter]).lower()
        return all(term in haystack for term in terms)

    if filter_active:
        for worker, days_data in schedule.items():
            for day, tasks in days_data.items():
                for t in tasks:
                    t['filter_match'] = matches_filters(t['project'], t['client'], t.get('pid'), t.get('phase'))
        filtered_projects = [
            p
            for p in projects
            if matches_filters(p['name'], p['client'], p.get('id'))
        ]
        for g in unplanned_list:
            match = matches_filters(g['project'], g['client'], g.get('pid'))
            g['filter_match'] = match
            for t in g['tasks']:
                t['filter_match'] = matches_filters(t['project'], t['client'], t.get('pid'), t.get('phase'))
    else:
        filtered_projects = projects

    # Order phases within each day: started ones first
    _sort_cell_tasks(schedule)

    if sort_option == 'name':
        filtered_projects.sort(key=lambda p: p['name'].lower())
    else:
        filtered_projects.sort(key=lambda p: orig_order[p['id']], reverse=True)

    filtered_projects = expand_for_display(filtered_projects)

    unplanned_list.sort(key=lambda g: g.get('material_date') or '9999-12-31')

    points = split_markers(schedule)

    today = local_today()
    pdf_range = request.args.get('pdf_range')
    if request.args.get('pdf') and pdf_range == 'prev3':
        start = today - timedelta(days=7)
        end = today + timedelta(days=90)
    else:
        start = today - timedelta(days=30)
        end = today + timedelta(days=120)
    days, cols, week_spans = build_calendar(start, end)
    hours_map = load_daily_hours()
    worker_day_overrides = load_worker_day_hours()
    worker_limits_map = {
        worker: HOURS_LIMITS.get(worker, HOURS_PER_DAY)
        for worker in WORKERS
    }
    note_map = {}
    for n in notes:
        note_map.setdefault(n['date'], []).append(n['description'])
    worker_note_map = {}
    for w, info in worker_notes_raw.items():
        text = info.get('text', '')
        ts = info.get('edited')
        fmt = ''
        if ts:
            try:
                fmt = datetime.fromisoformat(ts).strftime('%H:%M %d/%m')
            except ValueError:
                fmt = ''
        height_val = None
        raw_height = info.get('height')
        if raw_height is not None:
            try:
                height_val = int(float(raw_height))
            except (TypeError, ValueError):
                height_val = None
        if isinstance(height_val, int) and height_val > 0:
            worker_note_map[w] = {'text': text, 'edited': fmt, 'height': height_val}
        else:
            worker_note_map[w] = {'text': text, 'edited': fmt}
    material_status_map, material_missing_map = compute_material_status_map(
        projects, include_missing_titles=True
    )
    for archived_pid in archived_project_map.keys():
        material_status_map[str(archived_pid)] = 'archived'
        material_missing_map.setdefault(str(archived_pid), [])

    manual_index = {}
    manual_bucket_items = []
    if manual_entries:
        manual_index = {
            (entry['pid'], entry['phase'], entry.get('part')): idx
            for idx, entry in enumerate(manual_entries)
        }
        manual_bucket_items = [None] * len(manual_entries)
        for group in list(unplanned_list):
            remaining_tasks = []
            for task in group['tasks']:
                key = (str(group['pid']), task['phase'], task.get('part'))
                idx = manual_index.get(key)
                if idx is None:
                    remaining_tasks.append(task)
                    continue
                status = material_status_map.get(str(group['pid']), 'complete')
                due_text = task.get('due_date') or group.get('due_date')
                matches_filter = (
                    task.get('filter_match', True)
                    and group.get('filter_match', True)
                )
                manual_bucket_items[idx] = {
                    'pid': group['pid'],
                    'project': group['project'],
                    'client': group['client'],
                    'phase': task['phase'],
                    'part': task.get('part'),
                    'color': task.get('color'),
                    'due_date': due_text,
                    'start_date': task.get('start_date'),
                    'day': task.get('day'),
                    'hours': task.get('hours'),
                    'late': task.get('late', False),
                    'due_status': task.get('due_status'),
                    'phase_deadline_status': task.get('phase_deadline_status'),
                    'blocked': task.get('blocked', False),
                    'frozen': task.get('frozen', False),
                    'frozen_background': task.get('frozen_background'),
                    'auto': task.get('auto', False),
                    'material_status': status,
                    'material_label': material_status_label(status),
                    'material_css': f"material-status-{status}",
                    'filter_match': matches_filter,
                }
            group['tasks'] = remaining_tasks
            if not remaining_tasks:
                unplanned_list.remove(group)
        cleaned_entries = []
        cleaned_bucket = []
        for idx, item in enumerate(manual_bucket_items):
            if item:
                cleaned_bucket.append(item)
                entry = manual_entries[idx]
                new_entry = {'pid': entry['pid'], 'phase': entry['phase']}
                if entry.get('part') is not None:
                    new_entry['part'] = entry['part']
                cleaned_entries.append(new_entry)
        if cleaned_entries != manual_entries:
            save_manual_unplanned(cleaned_entries)
            manual_entries = cleaned_entries
            manual_bucket_items = cleaned_bucket
        else:
            manual_bucket_items = cleaned_bucket
    else:
        manual_bucket_items = []

    unplanned_groups = group_unplanned_by_status(unplanned_list, material_status_map)

    def _due_sort_value(entry):
        due_text = (entry.get('due_date') or '').strip()
        if due_text and due_text != '0':
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try:
                    return datetime.strptime(due_text, fmt).date()
                except ValueError:
                    continue
        return date.max

    unplanned_due = sorted(
        unplanned_list,
        key=lambda item: (
            _due_sort_value(item),
            (item.get('project') or '').lower(),
            (item.get('client') or '').lower(),
        ),
    )
    unplanned_phase_groups = group_unplanned_by_phase(unplanned_due)

    project_map = {}
    for p in projects:
        p.setdefault('kanban_attachments', [])
        p.setdefault('kanban_display_fields', {})
        entry = {
            **p,
            'frozen_phases': sorted({t['phase'] for t in p.get('frozen_tasks', [])}),
            'phase_sequence': list((p.get('phases') or {}).keys()),
            'kanban_previous_phases': compute_previous_kanban_phases(p.get('kanban_column')),
        }
        pid = p.get('id')
        if pid:
            pid_key = str(pid)
            entry['material_status'] = material_status_map.get(pid_key, 'complete')
            entry['material_missing_titles'] = material_missing_map.get(pid_key, [])
            project_map[pid_key] = entry
        project_map[p['id']] = entry
    for pid, info in archived_project_map.items():
        info.setdefault('kanban_display_fields', {})
        info.setdefault('kanban_attachments', [])
        info['material_status'] = material_status_map.get(str(pid), 'archived')
        info['material_missing_titles'] = material_missing_map.get(str(pid), [])
        project_map[pid] = info
        project_map[str(pid)] = info
    start_map = phase_start_map(projects)
    project_orders = build_project_order_popup_map(projects, today=today)
    orders_summary = build_complete_orders_summary(today)
    for row in filtered_projects:
        pid = row.get('id')
        if pid is None:
            continue
        row['material_status'] = material_status_map.get(str(pid), 'complete')
    if project_id_filter and not project_filter:
        info = project_map.get(project_id_filter)
        if info:
            project_filter = (info.get('name') or '').strip()

    complete_project_phases = [ph for ph in PHASE_ORDER if ph not in ['dibujo', 'pedidos']]
    if 'lanzamiento' not in complete_project_phases:
        complete_project_phases.insert(0, 'lanzamiento')
    complete_project_phase_columns = build_complete_project_phase_columns(filtered_projects)

    return render_template(
        'complete.html',
        schedule=schedule,
        cols=cols,
        week_spans=week_spans,
        conflicts=conflicts,
        workers=WORKERS,
        project_filter=project_filter,
        client_filter=client_filter,
        project_id_filter=project_id_filter,
        filter_active=filter_active,
        projects=filtered_projects,
        sort_option=sort_option,
        today=today,
        phases=PHASE_ORDER,
        complete_project_phases=complete_project_phases,
        complete_project_phase_columns=complete_project_phase_columns,
        all_workers=active_workers(today),
        notes=note_map,
        project_data=project_map,
        start_map=start_map,
        hours=hours_map,
        split_points=points,
        palette=COLORS,
        unplanned_groups=unplanned_groups,
        unplanned_due=unplanned_due,
        unplanned_phase_groups=unplanned_phase_groups,
        manual_bucket=manual_bucket_items,
        worker_notes=worker_note_map,
        material_status_labels=MATERIAL_STATUS_LABELS,
        worker_day_hours=worker_day_overrides,
        worker_limits=worker_limits_map,
        import_optional_phases=include_optional_phases,
        orders_summary=orders_summary,
        project_orders=project_orders,
        deactivated_workers=deactivated_workers,
    )


@app.route('/resumen')
def resumen():
    return complete()


@app.route('/update_worker_note', methods=['POST'])
def update_worker_note():
    data = request.get_json() or {}
    worker = data.get('worker')
    text = data.get('text', '')
    raw_height = data.get('height')
    if not worker:
        return jsonify({'error': 'Falta recurso'}), 400
    height_val = None
    if isinstance(raw_height, (int, float)):
        height_val = int(raw_height)
    else:
        try:
            if raw_height is not None:
                height_val = int(float(raw_height))
        except (TypeError, ValueError):
            height_val = None
    if isinstance(height_val, int):
        if height_val < 0:
            height_val = None
        elif height_val > 3000:
            height_val = 3000
    notes = load_worker_notes()
    previous = notes.get(worker) or {}
    payload = {
        'text': text,
        'edited': local_now().isoformat(timespec='minutes'),
    }
    if isinstance(height_val, int) and height_val > 0:
        payload['height'] = height_val
    elif previous.get('height') is not None:
        try:
            prev_height = int(float(previous['height']))
        except (TypeError, ValueError):
            prev_height = None
        if isinstance(prev_height, int) and prev_height > 0:
            payload['height'] = prev_height
    notes[worker] = payload
    save_worker_notes(notes)
    dt = datetime.fromisoformat(payload['edited'])
    response = {'edited': dt.strftime('%H:%M %d/%m')}
    if 'height' in payload:
        response['height'] = int(payload['height'])
    return jsonify(response)


@app.route('/update_pedido_date', methods=['POST'])
def update_pedido_date():
    data = request.get_json() or {}
    cid = data.get('cid')
    date_str = data.get('date')
    if not cid:
        return '', 400
    if date_str:
        try:
            d = date.fromisoformat(date_str)
        except Exception:
            return '', 400
        stored = d.isoformat()
    else:
        stored = None
    cards = load_kanban_cards()
    cid = str(cid)
    updated = False
    for entry in cards:
        card = entry.get('card') or {}
        existing = card.get('taskid') or card.get('cardId') or card.get('id')
        if str(existing) == cid:
            if stored is not None:
                entry['stored_title_date'] = stored
                entry.pop('previous_title_date', None)
            else:
                prev = entry.get('stored_title_date')
                if not prev:
                    title = (card.get('title') or '').strip()
                    m = re.search(r"\((\d{2})/(\d{2})\)", title)
                    if m:
                        prev = f"{int(m.group(1)):02d}/{int(m.group(2)):02d}"
                    else:
                        d_dead = parse_kanban_date(card.get('deadline'))
                        if d_dead:
                            prev = f"{d_dead.day:02d}/{d_dead.month:02d}"
                entry['previous_title_date'] = prev
                entry['stored_title_date'] = None
                card['deadline'] = None
            updated = True
            break
    if updated:
        save_kanban_cards(cards)
        broadcast_event({'type': 'kanban_update'})
        return jsonify({'stored_date': stored})
    return '', 404


@app.route('/update_worker/<pid>/<phase>', methods=['POST'])
def update_worker(pid, phase):
    projects = get_projects()
    for p in projects:
        if p['id'] == pid:
            worker = request.form['worker']
            if worker in set(load_inactive_workers()):
                worker = UNPLANNED
            p.setdefault('assigned', {})[phase] = worker
            break
    save_projects(projects)
    next_url = request.form.get('next') or request.args.get('next') or url_for('project_list')
    return redirect(next_url)


@app.route('/update_phase_start', methods=['POST'])
def update_phase_start():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    date_str = data.get('date')
    next_url = data.get('next') or request.args.get('next') or url_for('project_list')
    if not pid or not phase or not date_str:
        return jsonify({'error': 'Datos incompletos'}), 400
    new_date = parse_input_date(date_str)
    if not new_date:
        return jsonify({'error': 'Fecha inválida'}), 400
    if new_date.weekday() in WEEKEND:
        return jsonify({'error': 'No se puede iniciar en fin de semana'}), 400
    if new_date < MIN_DATE or new_date > MAX_DATE:
        return jsonify({'error': 'Fecha fuera de rango'}), 400
    projects = get_projects()
    mapping = compute_schedule_map(projects)
    tasks = [t for t in mapping.get(pid, []) if t[2] == phase]
    if not tasks:
        return jsonify({'error': 'Fase no encontrada'}), 404
    proj = next((p for p in projects if p['id'] == pid), None)
    base = date.fromisoformat(proj['start_date'])
    offset = (date.fromisoformat(tasks[0][1]) - base).days
    proj['start_date'] = (new_date - timedelta(days=offset)).isoformat()
    if proj.get('segment_starts'):
        proj['segment_starts'].pop(phase, None)
    temp = copy.deepcopy(projects)
    new_map = compute_schedule_map(temp)
    new_tasks = [t for t in new_map.get(pid, []) if t[2] == phase]
    if not new_tasks or date.fromisoformat(new_tasks[0][1]) != new_date:
        return jsonify({'error': 'No se puede asignar esa fecha'}), 400
    save_projects(temp)
    if request.is_json:
        return jsonify({'date': new_tasks[0][1], 'pid': pid, 'phase': phase})
    return redirect(next_url)


@app.route('/update_due_date', methods=['POST'])
def update_due_date():
    """Modify a project's deadline."""
    data = request.get_json() or request.form
    pid = data.get('pid')
    date_str = data.get('due_date')
    next_url = data.get('next') or request.args.get('next') or url_for('project_list')
    if not pid or not date_str:
        return jsonify({'error': 'Datos incompletos'}), 400
    new_date = parse_input_date(date_str)
    if not new_date:
        return jsonify({'error': 'Fecha inválida'}), 400
    if new_date < MIN_DATE or new_date > MAX_DATE:
        return jsonify({'error': 'Fecha fuera de rango'}), 400
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    proj['due_date'] = new_date.isoformat()
    proj['due_confirmed'] = True
    proj['due_warning'] = True
    save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(next_url)


@app.route('/clear_deadline', methods=['POST'])
def clear_deadline():
    data = request.get_json() or {}
    pid = data.get('pid')
    if not pid:
        return '', 400
    projects = load_projects()
    for p in projects:
        if p['id'] == pid:
            p['due_warning'] = False
            save_projects(projects)
            break
    return '', 204


@app.route('/update_start_date', methods=['POST'])
def update_start_date():
    """Modify a project's start date."""
    data = request.get_json() or request.form
    pid = data.get('pid')
    date_str = data.get('start_date') or data.get('date')
    next_url = data.get('next') or request.args.get('next') or url_for('project_list')
    if not pid or not date_str:
        return jsonify({'error': 'Datos incompletos'}), 400
    new_date = parse_input_date(date_str)
    if not new_date:
        return jsonify({'error': 'Fecha inválida'}), 400
    if new_date < MIN_DATE or new_date > MAX_DATE:
        return jsonify({'error': 'Fecha fuera de rango'}), 400
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    proj['start_date'] = new_date.isoformat()
    save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(next_url)


@app.route('/update_phase_hours', methods=['POST'])
def update_phase_hours():
    """Modify hours for a specific phase."""
    data = request.get_json() or request.form
    pid_value = data.get('pid')
    phase = data.get('phase')
    hours_val = data.get('hours')
    part_val = data.get('part')
    next_url = data.get('next') or request.args.get('next') or url_for('project_list')
    if not pid_value or not phase or hours_val is None:
        return jsonify({'error': 'Datos incompletos'}), 400
    pid = str(pid_value)
    try:
        hours = int(hours_val)
    except Exception:
        return jsonify({'error': 'Horas inválidas'}), 400
    part_index = None
    if part_val not in (None, '', 'null'):
        try:
            part_index = int(part_val)
        except Exception:
            return jsonify({'error': 'Parte inválida'}), 400
        if part_index < 0:
            return jsonify({'error': 'Parte inválida'}), 400
    projects = get_projects()
    proj = next((p for p in projects if str(p.get('id')) == pid), None)
    archived_entries = None
    archived_entry = None
    is_archived = False
    active_project_id = proj.get('id') if proj else None
    archived_update_hours = None
    archived_update_part = None
    if not proj:
        archived_entries = load_archived_calendar_entries()
        for entry in archived_entries:
            entry_pid = str((entry or {}).get('pid') or '')
            if entry_pid != pid:
                continue
            info = entry.get('project')
            if not isinstance(info, dict):
                continue
            proj = info
            archived_entry = entry
            is_archived = True
            active_project_id = proj.get('id') or pid
            break
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    if phase == 'montar':
        _set_phase_source(proj, 'montar', 'planner')
    proj.setdefault('phases', {})
    proj.setdefault('auto_hours', {})
    if hours <= 0:
        removed = _remove_phase_references(proj, phase)
        if removed and is_archived and archived_entry:
            phase_lower = phase.lower()
            tasks_list = archived_entry.get('tasks')
            if isinstance(tasks_list, list):
                archived_entry['tasks'] = [
                    item
                    for item in tasks_list
                    if not (
                        isinstance(item, dict)
                        and isinstance(item.get('task'), dict)
                        and str(item['task'].get('pid') or '').strip() == pid
                        and str(item['task'].get('phase') or '').strip().lower() == phase_lower
                    )
                ]
        if not proj.get('phases'):
            if is_archived and archived_entries is not None:
                archived_entries = [e for e in archived_entries if e is not archived_entry]
                save_archived_calendar_entries(archived_entries)
                if request.is_json:
                    return '', 204
                return redirect(next_url)
            remove_project_and_preserve_schedule(
                projects, active_project_id if active_project_id is not None else pid_value
            )
            if request.is_json:
                return '', 204
            return redirect(next_url)
        if removed:
            proj['frozen_tasks'] = [
                t for t in proj.get('frozen_tasks', []) if t['phase'] != phase
            ]
    else:
        prev_val = proj['phases'].get(phase)
        was_list = isinstance(prev_val, list)
        prev_total = _phase_total_hours(prev_val)
        assigned = proj.setdefault('assigned', {})
        if part_index is not None:
            if not isinstance(prev_val, list):
                return jsonify({'error': 'La fase no está dividida'}), 400
            if part_index >= len(prev_val):
                return jsonify({'error': 'Parte inválida'}), 400

            def _coerce_part(value):
                if isinstance(value, int):
                    return value
                if isinstance(value, float):
                    if not float(value).is_integer():
                        raise ValueError
                    return int(value)
                text = '' if value is None else str(value).strip()
                if not text:
                    raise ValueError
                try:
                    return int(text)
                except Exception:
                    num = float(text)
                    if not float(num).is_integer():
                        raise ValueError
                    return int(num)

            try:
                current_parts = [_coerce_part(item) for item in prev_val]
            except Exception:
                return jsonify({'error': 'Horas inválidas registradas en la fase'}), 400

            diff = hours - prev_total
            previous_part_value = current_parts[part_index]
            new_value = previous_part_value + diff
            if new_value <= 0:
                return jsonify({'error': 'La parte resultante debe ser mayor que cero'}), 400

            current_parts[part_index] = new_value
            proj['phases'][phase] = current_parts
            if assigned.get(phase) in (None, ''):
                assigned[phase] = UNPLANNED
            seg_map = proj.get('segment_starts', {})
            if seg_map and phase in seg_map:
                seg_list = seg_map[phase]
                if seg_list is not None and len(seg_list) < len(current_parts):
                    seg_list.extend([None] * (len(current_parts) - len(seg_list)))
            hour_map = proj.get('segment_start_hours', {})
            if hour_map and phase in hour_map:
                hour_list = hour_map[phase]
                if hour_list is not None and len(hour_list) < len(current_parts):
                    hour_list.extend([0] * (len(current_parts) - len(hour_list)))
            worker_map = proj.get('segment_workers', {})
            if worker_map and phase in worker_map:
                worker_list = worker_map[phase]
                if worker_list is not None and len(worker_list) < len(current_parts):
                    worker_list.extend([assigned.get(phase, UNPLANNED)] * (len(current_parts) - len(worker_list)))
            archived_update_hours = new_value
            archived_update_part = part_index
        else:
            proj['phases'][phase] = hours
            if prev_total <= 0:
                assigned[phase] = UNPLANNED
            else:
                assigned.setdefault(phase, UNPLANNED)
            if was_list:
                if proj.get('segment_starts'):
                    proj['segment_starts'].pop(phase, None)
                    if not proj['segment_starts']:
                        proj.pop('segment_starts')
                if proj.get('segment_start_hours'):
                    proj['segment_start_hours'].pop(phase, None)
                    if not proj['segment_start_hours']:
                        proj.pop('segment_start_hours')
                if proj.get('segment_workers'):
                    proj['segment_workers'].pop(phase, None)
                    if not proj['segment_workers']:
                        proj.pop('segment_workers')
            archived_update_hours = hours
            archived_update_part = None
        if phase == AUTO_RECEIVING_PHASE:
            proj['auto_hours'].pop(AUTO_RECEIVING_PHASE, None)
        else:
            if _cleanup_auto_receiving_placeholder(proj):
                proj['frozen_tasks'] = [
                    t for t in proj.get('frozen_tasks', []) if t['phase'] != AUTO_RECEIVING_PHASE
                ]
    proj['frozen_tasks'] = [t for t in proj.get('frozen_tasks', []) if t['phase'] != phase]
    rescheduled = False
    if is_archived and archived_entries is not None:
        if archived_entry and archived_update_hours is not None:
            rescheduled = _reschedule_archived_phase_hours(
                archived_entries,
                pid,
                phase,
                part_index=archived_update_part,
            )
            if not rescheduled:
                _rebalance_archived_phase_hours(
                    archived_entry,
                    pid,
                    phase,
                    archived_update_hours,
                    part_index=archived_update_part,
                )
        save_archived_calendar_entries(archived_entries)
    else:
        schedule_projects(projects)
        save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(next_url)

@app.route('/update_project_row', methods=['POST'])
def update_project_row():
    """Apply multiple field changes for a project in one request."""
    data = request.get_json() or {}
    pid = data.get('pid')
    if not pid:
        return jsonify({'error': 'Datos incompletos'}), 400
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    modified = set()

    if 'start_date' in data:
        sd = parse_input_date(data['start_date'])
        if sd:
            proj['start_date'] = sd.isoformat()
    if 'due_date' in data:
        dd = parse_input_date(data['due_date'])
        proj['due_date'] = dd.isoformat() if dd else ''
    if 'client' in data:
        proj['client'] = data['client']
    if 'material_confirmed_date' in data:
        md = parse_input_date(data['material_confirmed_date'])
        proj['material_confirmed_date'] = md.isoformat() if md else ''
    if 'color' in data:
        proj['color'] = data['color']

    proj.setdefault('auto_hours', {})
    for ph, val in (data.get('phases') or {}).items():
        try:
            hours = int(val)
        except Exception:
            continue
        if hours <= 0:
            if _remove_phase_references(proj, ph):
                modified.add(ph)
            if ph == 'montar':
                _set_phase_source(proj, 'montar', 'planner')
            continue
        proj.setdefault('phases', {})
        prev = proj['phases'].get(ph)
        prev_total = (
            sum(map(int, prev)) if isinstance(prev, list)
            else int(prev) if prev is not None else None
        )
        was_list = isinstance(prev, list)
        proj['phases'][ph] = hours
        if ph == 'montar':
            _set_phase_source(proj, 'montar', 'planner')
        assigned = proj.setdefault('assigned', {})
        if prev_total in (None, 0):
            assigned[ph] = UNPLANNED
        else:
            assigned.setdefault(ph, UNPLANNED)
        if was_list:
            if proj.get('segment_starts'):
                proj['segment_starts'].pop(ph, None)
                if not proj['segment_starts']:
                    proj.pop('segment_starts')
            if proj.get('segment_workers'):
                proj['segment_workers'].pop(ph, None)
                if not proj['segment_workers']:
                    proj.pop('segment_workers')
        if ph == AUTO_RECEIVING_PHASE:
            proj['auto_hours'].pop(AUTO_RECEIVING_PHASE, None)
        else:
            if _cleanup_auto_receiving_placeholder(proj):
                modified.add(AUTO_RECEIVING_PHASE)
        modified.add(ph)

    for item in data.get('phase_parts') or []:
        if not isinstance(item, dict):
            continue
        ph = item.get('phase')
        try:
            part_index = int(item.get('part'))
            hours = int(item.get('hours'))
        except Exception:
            continue
        if not ph or part_index < 0 or hours <= 0:
            continue
        prev = proj.get('phases', {}).get(ph)
        if not isinstance(prev, list) or part_index >= len(prev):
            continue
        current_parts = []
        valid_parts = True
        for value in prev:
            try:
                current_parts.append(int(value))
            except Exception:
                valid_parts = False
                break
        if not valid_parts:
            continue
        current_parts[part_index] = hours
        proj.setdefault('phases', {})[ph] = current_parts
        proj.setdefault('assigned', {}).setdefault(ph, UNPLANNED)
        if ph == 'montar':
            _set_phase_source(proj, 'montar', 'planner')
        modified.add(ph)

    if data.get('phase_starts'):
        seg = proj.setdefault('segment_starts', {})
        for ph, d in data['phase_starts'].items():
            val = parse_input_date(d)
            if val:
                seg[ph] = [val.isoformat()]
                modified.add(ph)

    if data.get('workers'):
        ass = proj.setdefault('assigned', {})
        inactive = set(load_inactive_workers())
        for ph, w in data['workers'].items():
            ass[ph] = w if w not in inactive else UNPLANNED
            modified.add(ph)

    if not proj.get('phases'):
        remove_project_and_preserve_schedule(projects, pid)
        return jsonify({'status': 'ok'})

    if modified:
        proj['frozen_tasks'] = [t for t in proj.get('frozen_tasks', []) if t['phase'] not in modified]

    schedule_projects(projects)
    save_projects(projects)
    return jsonify({'status': 'ok'})


@app.route('/update_observations/<pid>', methods=['POST'])
def update_observations(pid):
    data = request.get_json() or {}
    projects = get_projects()
    for p in projects:
        if p['id'] == pid:
            p['observations'] = data.get('observations', '')
            save_projects(projects)
            return jsonify({'status': 'ok'})
    return jsonify({'error': 'Proyecto no encontrado'}), 404


@app.route('/update_image/<pid>', methods=['POST'])
def update_image(pid):
    """Attach or replace a project's image file."""
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    file = request.files.get('image')
    next_url = request.form.get('next') or request.args.get('next') or url_for('project_list')
    if file and file.filename:
        ext = os.path.splitext(secure_filename(file.filename))[1]
        fname = f"{uuid.uuid4()}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, fname)
        file.save(save_path)
        previous_image = proj.get('image')
        proj['image'] = f"uploads/{fname}"
        if previous_image and previous_image != proj['image']:
            _remove_upload_file(previous_image)
        prune_orphan_uploads(projects)
        save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(next_url)


@app.route('/toggle_optional_phases', methods=['POST'])
def toggle_optional_phases():
    data = request.get_json(silent=True) or {}
    raw_enabled = data.get('enabled')
    if isinstance(raw_enabled, str):
        enabled = raw_enabled.strip().lower() not in ('', '0', 'false', 'no', 'off')
    else:
        enabled = bool(raw_enabled)
    settings = load_planner_settings()
    settings['import_optional_phases'] = enabled
    save_planner_settings(settings)
    return jsonify({'import_optional_phases': enabled})


@app.route('/update_hours', methods=['POST'])
def update_hours():
    """Set working hours for a specific day (1-9)."""
    data = request.get_json() or request.form
    day = data.get('date')
    val = data.get('hours')
    try:
        hours = int(val)
    except Exception:
        return jsonify({'error': 'Horas invalidas'}), 400
    if not day or hours < 1 or hours > 9:
        return jsonify({'error': 'Datos invalidos'}), 400
    hours_map = load_daily_hours()
    default_hours = _default_day_hours(day)
    if hours == default_hours:
        hours_map.pop(day, None)
    else:
        hours_map[day] = hours
    save_daily_hours(hours_map)
    if request.is_json:
        return '', 204
    return redirect(request.referrer or url_for('calendar_view'))

@app.route('/delete_phase', methods=['POST'])
def delete_phase():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    if not pid or not phase:
        return '', 400
    projects = get_projects()
    for p in projects:
        if p['id'] == pid:
            if phase in (p.get('phases') or {}):
                _remove_phase_references(p, phase)
                if not p.get('phases'):
                    remove_project_and_preserve_schedule(projects, pid)
                else:
                    save_projects(projects)
            break
    return '', 204


@app.route('/remove_unplanned_verificar_phases', methods=['POST'])
def remove_unplanned_verificar_phases():
    projects = get_projects()
    if not projects:
        return jsonify({'removed_projects': 0, 'removed_phases': 0}), 200

    schedule_map, _ = schedule_projects(copy.deepcopy(projects))
    unplanned_verify_pids = set()
    for _day, tasks in (schedule_map.get(UNPLANNED) or {}).items():
        for task in tasks or []:
            if phase_base(task.get('phase')) != 'verificar':
                continue
            pid = str(task.get('pid') or '').strip()
            if pid:
                unplanned_verify_pids.add(pid)

    removed_projects = 0
    removed_phases = 0
    changed = False
    for project in projects:
        pid = str(project.get('id') or '').strip()
        if not pid or pid not in unplanned_verify_pids:
            continue
        column_key = normalize_key(project.get('kanban_column'))
        if column_key not in VERIFY_PHASE_REMOVAL_COLUMN_KEYS:
            continue
        phase_names = [
            name for name in (project.get('phases') or {}).keys()
            if phase_base(name) == 'verificar'
        ]
        if not phase_names:
            continue
        removed_here = 0
        for phase_name in phase_names:
            if _remove_phase_references(project, phase_name):
                removed_here += 1
        if removed_here:
            removed_projects += 1
            removed_phases += removed_here
            changed = True

    if changed:
        save_projects(projects)
    return jsonify({'removed_projects': removed_projects, 'removed_phases': removed_phases}), 200


@app.route('/split_phase', methods=['POST'])
def split_phase_route():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    date_str = data.get('date')
    parts_raw = data.get('parts')
    part_values = []
    if isinstance(parts_raw, list):
        part_values = parts_raw
    elif isinstance(parts_raw, str):
        try:
            parsed_parts = json.loads(parts_raw)
            if isinstance(parsed_parts, list):
                part_values = parsed_parts
        except Exception:
            part_values = []

    if not part_values:
        part1_str = data.get('part1')
        part2_str = data.get('part2')
        if part1_str is None or part2_str is None:
            return '', 400
        part_values = [part1_str, part2_str]

    if not pid or not phase or not date_str:
        return '', 400
    try:
        date.fromisoformat(date_str)
    except Exception:
        return '', 400
    parsed_parts = []
    try:
        for item in part_values:
            parsed = int(item)
            parsed_parts.append(parsed)
    except Exception:
        return '', 400

    if len(parsed_parts) < 2:
        return jsonify({'error': 'Debes crear al menos dos partes'}), 400
    if any(val <= 0 for val in parsed_parts):
        return jsonify({'error': 'Las partes deben ser mayores que cero'}), 400

    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj or phase not in proj.get('phases', {}):
        return '', 400

    val = proj['phases'][phase]
    total = _phase_total_hours(val)
    if total <= 0:
        return jsonify({'error': 'La fase no tiene horas válidas'}), 400
    if sum(parsed_parts) != total:
        return jsonify({'error': 'Las horas no coinciden con el total'}), 400

    proj['phases'][phase] = parsed_parts

    seg_map = proj.setdefault('segment_starts', {})
    prev_starts = list(seg_map.get(phase) or [])
    if len(prev_starts) >= len(parsed_parts):
        seg_map[phase] = prev_starts[: len(parsed_parts)]
    else:
        seg_map[phase] = prev_starts + [None] * (len(parsed_parts) - len(prev_starts))

    hour_map = proj.setdefault('segment_start_hours', {})
    prev_hours = list(hour_map.get(phase) or [])
    if len(prev_hours) >= len(parsed_parts):
        hour_map[phase] = prev_hours[: len(parsed_parts)]
    else:
        hour_map[phase] = prev_hours + [None] * (len(parsed_parts) - len(prev_hours))

    worker = proj.get('assigned', {}).get(phase)
    worker_map = proj.setdefault('segment_workers', {})
    prev_workers = list(worker_map.get(phase) or [])
    if not prev_workers:
        prev_workers = [worker] * len(parsed_parts)
    else:
        if len(prev_workers) >= len(parsed_parts):
            prev_workers = prev_workers[: len(parsed_parts)]
        else:
            prev_workers.extend([worker] * (len(parsed_parts) - len(prev_workers)))
    worker_map[phase] = prev_workers

    save_projects(projects)
    return '', 204


@app.route('/unsplit_phase', methods=['POST'])
def unsplit_phase():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    if not pid or not phase:
        return '', 400
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj or phase not in proj.get('phases', {}):
        return '', 400
    val = proj['phases'][phase]
    if not isinstance(val, list) or len(val) <= 1:
        return '', 400
    total = sum(int(v) for v in val)
    mapping = compute_schedule_map(projects)
    part_hours = {}
    part_workers = {}
    for worker, day, ph, hrs, prt in mapping.get(pid, []):
        if ph == phase and prt is not None and prt < len(val):
            part_hours[prt] = part_hours.get(prt, 0) + hrs
            part_workers.setdefault(prt, worker)
    if part_hours:
        largest = max(part_hours.items(), key=lambda x: x[1])[0]
        worker = part_workers.get(largest)
        if worker:
            proj.setdefault('assigned', {})[phase] = worker
    proj['phases'][phase] = total
    seg_map = proj.get('segment_starts')
    hour_map = proj.get('segment_start_hours')
    starts = (seg_map or {}).get(phase) or []
    hours_list = (hour_map or {}).get(phase) or []
    chosen_idx = None
    for idx, start in enumerate(starts):
        if start:
            chosen_idx = idx
            break
    if chosen_idx is None and starts:
        first = starts[0]
        if first:
            chosen_idx = 0
    chosen_start = None
    chosen_hour = None
    if chosen_idx is not None:
        chosen_start = starts[chosen_idx]
        if chosen_start:
            if hours_list and chosen_idx < len(hours_list):
                chosen_hour = hours_list[chosen_idx]
    if seg_map is not None:
        if chosen_start:
            seg_map[phase] = [chosen_start]
        else:
            seg_map.pop(phase, None)
        if not seg_map:
            proj.pop('segment_starts', None)
    if hour_map is not None:
        if chosen_start:
            hour_value = chosen_hour if chosen_hour is not None else 0
            hour_map[phase] = [hour_value]
        else:
            hour_map.pop(phase, None)
        if not hour_map:
            proj.pop('segment_start_hours', None)
    seg_workers = proj.get('segment_workers')
    if seg_workers and phase in seg_workers:
        seg_workers.pop(phase, None)
        if not seg_workers:
            proj.pop('segment_workers')
    save_projects(projects)
    return '', 204


@app.route('/add_phase_instance', methods=['POST'])
def add_phase_instance():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    hours_raw = data.get('hours')
    if not pid or not phase:
        return jsonify({'error': 'Faltan datos'}), 400
    phase = phase_base(str(phase).strip())
    if phase not in PHASE_ORDER:
        return jsonify({'error': 'Fase no válida'}), 400
    try:
        hours = int(hours_raw)
    except Exception:
        return jsonify({'error': 'Horas inválidas'}), 400
    if hours <= 0:
        return jsonify({'error': 'Las horas deben ser mayores que cero'}), 400
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    phases = proj.setdefault('phases', {})
    base = phase_base(phase)
    existing_keys = [ph for ph in phases if phase_base(ph) == base]

    def _phase_index(ph):
        if '#' in ph:
            try:
                return int(ph.split('#', 1)[1])
            except Exception:
                return None
        return 1

    next_idx = 1
    for key in existing_keys:
        idx = _phase_index(key)
        if idx is None:
            continue
        if idx >= next_idx:
            next_idx = idx + 1
    new_key = base if next_idx == 1 else f"{base}#{next_idx}"
    while new_key in phases:
        next_idx += 1
        new_key = f"{base}#{next_idx}"
    phases[new_key] = hours
    assigned = proj.setdefault('assigned', {})
    assigned[new_key] = UNPLANNED
    seq = proj.setdefault('phase_sequence', [])
    if new_key not in seq:
        seq.append(new_key)
    manual_bucket_add(pid, new_key, None)
    save_projects(projects)
    return jsonify({'phase': new_key}), 201


@app.route('/move', methods=['POST'])
def move_phase():
    data = request.get_json() or request.form
    pid = data.get('pid')
    phase = data.get('phase')
    date_str = data.get('date')
    worker = data.get('worker')
    part = data.get('part')
    if part in (None, '', 'None'):
        part = None
    else:
        try:
            part = int(part)
        except Exception:
            part = None
    manual_flag = str(data.get('manual_bucket')).lower() == 'true'
    manual_position = data.get('manual_position')
    # 🔧 Respetamos el modo que viene en la petición
    mode = data.get('mode', 'split')
    push_pid = data.get('push_pid')
    push_phase = data.get('push_phase')
    push_part = data.get('push_part')
    push_from = None
    if push_pid and push_phase:
        push_from = (push_pid, push_phase, push_part if push_part not in (None, '', 'None') else None)
    unblock = str(data.get('unblock')).lower() == 'true'
    skip_block = str(data.get('skip_block')).lower() == 'true'
    ack_warning = str(data.get('ack_warning')).lower() == 'true'
    start = data.get('start')
    if start in (None, '', 'None'):
        start_hour = None
    else:
        try:
            start_hour = float(start)
        except Exception:
            start_hour = None
    if not pid or not phase or not date_str:
        return '', 400
    try:
        day = date.fromisoformat(date_str)
    except Exception:
        return '', 400
    if worker != UNPLANNED and _is_worker_deactivated_on_day(worker, day):
        return jsonify({
            'error': (
                'No es posible planificar una fase porque ese día ese recurso está desactivado.'
            )
        }), 400

    projects = get_projects()
    original_projects = copy.deepcopy(projects)
    before_mapping = compute_schedule_map(original_projects)
    pid_candidates = []
    for candidate in (pid, str(pid)):
        if candidate not in pid_candidates:
            pid_candidates.append(candidate)
    try:
        pid_int = int(pid)
    except Exception:
        pid_int = None
    else:
        for candidate in (pid_int, str(pid_int)):
            if candidate not in pid_candidates:
                pid_candidates.append(candidate)

    def _find_phase_entry(mapping_data):
        for key in pid_candidates:
            entries = mapping_data.get(key)
            if not entries:
                continue
            for w, d, ph, _, prt in entries:
                if ph == phase and (part is None or prt == part):
                    return d, w
        return None, None

    before_day, before_worker = _find_phase_entry(before_mapping)
    tracker_events = []
    new_day, warn, info = move_phase_date(
        projects,
        pid,
        phase,
        day,
        worker,
        part,
        mode=mode,   # 👉 aquí respetamos el valor recibido
        push_from=push_from,
        unblock=unblock,
        skip_block=skip_block,
        start_hour=start_hour,
        track=tracker_events,
        initial_mapping=before_mapping,
    )
    if new_day is None:
        if isinstance(warn, dict):
            return jsonify({'blocked': warn}), 409
        return jsonify({'error': warn or 'No se pudo mover'}), 400

    # Revert move if the target day is already full and the phase was
    # scheduled elsewhere. This prevents the phase from jumping to the next
    # available day when the chosen cell has no remaining hours.
    actual_day = (info or {}).get('actual_day') if isinstance(info, dict) else None
    actual_worker = (info or {}).get('actual_worker') if isinstance(info, dict) else None
    if not actual_day or not actual_worker:
        # Fallback for legacy callers that may not populate location metadata.
        mapping = compute_schedule_map(projects)
        actual_day, actual_worker = _find_phase_entry(mapping)
    target_worker = worker or actual_worker or before_worker
    worker_limit = HOURS_LIMITS.get(target_worker, HOURS_PER_DAY)
    unlimited_worker = isinstance(worker_limit, (int, float)) and math.isinf(float(worker_limit))
    office_worker = normalize_key(target_worker) in {
        normalize_key('Oficina técnica'),
        normalize_key('Oficina técnica.'),
        normalize_key('Oficina Tecnica'),
        normalize_key('Oficina Tecnica.'),
    }

    if actual_day != date_str and not (unlimited_worker or office_worker):
        projects[:] = original_projects
        save_projects(projects)
        return jsonify({'error': 'Jornada ocupada'}), 409

    if actual_worker == UNPLANNED and manual_flag:
        manual_bucket_add(pid, phase, part, manual_position)
    else:
        manual_bucket_remove(pid, phase, part)

    # Build tracker entry with detailed reasoning
    proj = next((p for p in projects if p['id'] == pid), {})
    reason = build_move_reason(projects, pid, phase, part, mode, info)
    affected_entries = []
    for ev in tracker_events:
        p2 = next((p for p in projects if p['id'] == ev['pid']), None)
        if p2:
            affected_entries.append({
                'project': p2.get('name', ''),
                'client': p2.get('client', ''),
                'phase': ev['phase'],
            })
    movement_timestamp = local_now().isoformat()
    logs = load_tracker()
    logs.append({
        'timestamp': movement_timestamp,
        'project': proj.get('name', ''),
        'client': proj.get('client', ''),
        'phase': phase,
        'reason': reason,
        'affected': affected_entries,
    })
    save_tracker(logs)

    previous_worker = (before_worker or '').strip() or UNPLANNED
    current_worker = (actual_worker or '').strip() or UNPLANNED
    if before_day != actual_day or previous_worker != current_worker:
        history = load_phase_history()
        key = phase_history_key(pid, phase, part)
        entry = {
            'timestamp': movement_timestamp,
            'from_day': before_day,
            'to_day': actual_day,
            'from_worker': previous_worker,
            'to_worker': current_worker,
        }
        history.setdefault(key, []).append(entry)
        save_phase_history(history)

    blockers = material_blockers_for_project(projects, pid, new_day)

    resp = {
        'date': new_day,
        'pid': pid,
        'phase': phase,
        'part': part,
        'material_blockers': blockers,
    }
    if warn and not ack_warning:
        resp['warning'] = warn
    return jsonify(resp)


@app.route('/phase_history')
def phase_history():
    pid = request.args.get('pid')
    phase = request.args.get('phase')
    part = request.args.get('part')
    if not pid or not phase:
        return jsonify({'history': []})
    if part in (None, '', 'None'):
        part_value = None
    else:
        part_value = part
    history = load_phase_history()
    key = phase_history_key(pid, phase, part_value)
    entries = history.get(key, []) if isinstance(history, dict) else []
    cleaned = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        ts = item.get('timestamp')
        if not isinstance(ts, str):
            continue
        cleaned.append({
            'timestamp': ts,
            'from_day': item.get('from_day'),
            'to_day': item.get('to_day'),
            'from_worker': item.get('from_worker'),
            'to_worker': item.get('to_worker'),
        })
    cleaned.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return jsonify({'history': cleaned})


@app.route('/manual_bucket/reorder', methods=['POST'])
def manual_bucket_reorder_route():
    data = request.get_json(silent=True) or {}
    order = data.get('order')
    if not isinstance(order, list):
        return jsonify({'error': 'Datos inválidos'}), 400
    if not manual_bucket_reorder(order):
        return jsonify({'error': 'Datos inválidos'}), 400
    return '', 204


def remove_project_and_preserve_schedule(projects, pid):
    """Remove a project and keep other projects' schedules intact.

    Returns the removed project dictionary when found so callers can reuse
    metadata (e.g. project name or client) after the removal took place.
    """
    mapping = compute_schedule_map(projects)
    removed = None
    for p in projects:
        if p['id'] == pid:
            removed = p
            break
    if not removed:
        return None
    projects.remove(removed)
    _remove_upload_file(removed.get('image'))
    # Drop any persisted conflicts tied to the removed project so stale
    # warnings do not linger in the interface.
    extras = load_extra_conflicts()
    new_extras = [
        c for c in extras if c.get('pid') != pid and c.get('project') != removed.get('name')
    ]
    if len(new_extras) != len(extras):
        save_extra_conflicts(new_extras)

    dismissed = load_dismissed()
    prefix = f"{removed.get('name')}|"
    kanban_key = f"kanban-{pid}"
    new_dismissed = [
        k for k in dismissed if not (k.startswith(prefix) or k == kanban_key)
    ]
    if len(new_dismissed) != len(dismissed):
        save_dismissed(new_dismissed)
    for proj in projects:
        tasks = mapping.get(proj['id'])
        if not tasks:
            continue
        starts = {}
        for worker, day, phase, hours, part in tasks:
            key = (phase, part)
            if key not in starts or day < starts[key][1]:
                starts[key] = (worker, day)
        seg_starts = proj.setdefault('segment_starts', {})
        assigned = proj.setdefault('assigned', {})
        seg_workers = proj.setdefault('segment_workers', {})
        for (phase, part), (worker, day) in starts.items():
            if part is None:
                seg_starts.setdefault(phase, [None])[0] = day
                assigned[phase] = worker
            else:
                lst = seg_starts.setdefault(phase, [])
                while len(lst) <= part:
                    lst.append(None)
                lst[part] = day
                wl = seg_workers.setdefault(phase, [])
                while len(wl) <= part:
                    wl.append(None)
                wl[part] = worker
    save_projects(projects)
    prune_orphan_uploads(projects)
    return removed

@app.route('/delete_project/<pid>', methods=['POST'])
def delete_project(pid):
    projects = get_projects()
    remove_project_and_preserve_schedule(projects, pid)
    next_url = request.args.get('next') or url_for('project_list')
    return redirect(next_url)


@app.route('/delete_conflict/<path:key>', methods=['POST'])
def delete_conflict(key):
    dismissed = load_dismissed()
    if key not in dismissed:
        dismissed.append(key)
        save_dismissed(dismissed)
    extras = load_extra_conflicts()
    new_extras = [e for e in extras if e['key'] != key]
    if len(new_extras) != len(extras):
        save_extra_conflicts(new_extras)
    # Return to the page that issued the request so the user stays on the
    # same tab (e.g. "Completo") instead of always jumping back to the
    # calendar view.
    return redirect(request.referrer or url_for('complete'))


@app.route('/clear_conflicts', methods=['POST'])
def clear_conflicts():
    """Mark all current conflicts as dismissed and clear extras."""
    projects = get_projects()
    _, conflicts = schedule_projects(projects)
    conflicts = [c for c in conflicts if c.get('message') != 'No se cumple la fecha de entrega']
    extras = load_extra_conflicts()
    keys = [c['key'] for c in conflicts] + [e['key'] for e in extras]
    dismissed = load_dismissed()
    for k in keys:
        if k not in dismissed:
            dismissed.append(k)
    save_dismissed(dismissed)
    save_extra_conflicts([])
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/show_conflicts', methods=['POST'])
def show_conflicts():
    """Restore all dismissed conflicts so they appear again."""
    save_dismissed([])
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/toggle_freeze/<pid>/<phase>', methods=['POST'])
def toggle_freeze(pid, phase):
    def _task_positions(projs):
        sched, _ = schedule_projects(copy.deepcopy(projs))
        mapping = {}
        for w, days in sched.items():
            for d, tasks in days.items():
                for t in tasks:
                    key = (t['pid'], t['phase'], t.get('part'))
                    start = t.get('start', 0)
                    cur = mapping.get(key)
                    if not cur or d < cur['day'] or (d == cur['day'] and start < cur['start']):
                        mapping[key] = {'day': d, 'start': start}
        return mapping

    projects = get_projects()
    before = _task_positions(projects)
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    frozen = proj.get('frozen_tasks', [])
    was_frozen = any(t['phase'] == phase for t in frozen)
    if was_frozen:
        proj['frozen_tasks'] = [t for t in frozen if t['phase'] != phase]
    else:
        # Recompute the schedule on a copy so freezing a phase does not
        # persistently modify the existing planning
        schedule, _ = schedule_projects(copy.deepcopy(projects))
        for w, days in schedule.items():
            for day, tasks in days.items():
                for t in tasks:
                    if t['pid'] == pid and t['phase'] == phase:
                        item = t.copy()
                        item['worker'] = w
                        item['day'] = day
                        item['frozen'] = True
                        frozen.append(item)
        proj['frozen_tasks'] = frozen

    after = _task_positions(projects)

    if was_frozen:
        for (p_id, ph, part), info in before.items():
            if p_id == pid and ph == phase:
                continue
            new = after.get((p_id, ph, part))
            if not new or new['day'] != info['day'] or new['start'] != info['start']:
                target = next((p for p in projects if p['id'] == p_id), None)
                if not target:
                    continue
                segs = target.setdefault('segment_starts', {}).setdefault(ph, [])
                hours = target.setdefault('segment_start_hours', {}).setdefault(ph, [])
                idx = part if part is not None else 0
                while len(segs) <= idx:
                    segs.append(None)
                while len(hours) <= idx:
                    hours.append(None)
                segs[idx] = info['day']
                hours[idx] = info['start']

    save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/remove_archived_phase', methods=['POST'])
def remove_archived_phase():
    data = request.get_json(silent=True) or {}
    pid = str(data.get('pid') or '').strip()
    phase = str(data.get('phase') or '').strip()
    if not pid or not phase:
        return jsonify({'error': 'Datos incompletos'}), 400

    target_phase = phase.lower()
    entries = load_archived_calendar_entries()
    if not entries:
        return jsonify({'error': 'Fase no encontrada'}), 404

    updated_entries = []
    removed_count = 0

    def _phase_matches(value):
        text = str(value or '').strip()
        return text.lower() == target_phase if text else False

    for entry in entries:
        if not isinstance(entry, dict):
            updated_entries.append(entry)
            continue

        entry_pid = str(entry.get('pid') or '').strip()
        if entry_pid != pid:
            updated_entries.append(entry)
            continue

        tasks = entry.get('tasks')
        if not isinstance(tasks, list):
            # Nothing usable, keep entry as-is
            updated_entries.append(entry)
            continue

        kept_tasks = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            payload = item.get('task')
            if not isinstance(payload, dict):
                continue
            payload_phase = payload.get('phase')
            if _phase_matches(payload_phase):
                removed_count += 1
                continue
            kept_tasks.append(item)

        if kept_tasks:
            entry['tasks'] = kept_tasks
            project_info = entry.get('project')
            if isinstance(project_info, dict):
                phases = project_info.get('phases')
                if isinstance(phases, dict):
                    for key in list(phases.keys()):
                        if _phase_matches(key):
                            phases.pop(key, None)
                assigned = project_info.get('assigned')
                if isinstance(assigned, dict):
                    for key in list(assigned.keys()):
                        if _phase_matches(key):
                            assigned.pop(key, None)
                auto_hours = project_info.get('auto_hours')
                if isinstance(auto_hours, dict):
                    for key in list(auto_hours.keys()):
                        if _phase_matches(key):
                            auto_hours.pop(key, None)
                frozen_tasks = project_info.get('frozen_tasks')
                if isinstance(frozen_tasks, list):
                    project_info['frozen_tasks'] = [
                        t
                        for t in frozen_tasks
                        if not _phase_matches((t or {}).get('phase'))
                    ]
                sequence = project_info.get('phase_sequence')
                if isinstance(sequence, list):
                    project_info['phase_sequence'] = [
                        item
                        for item in sequence
                        if not _phase_matches(item)
                    ]
            updated_entries.append(entry)
        else:
            # No tasks remain for this archived project; drop the entry entirely
            if removed_count == 0:
                # We attempted to remove but found no matching tasks
                updated_entries.append(entry)

    if removed_count == 0:
        return jsonify({'error': 'Fase no encontrada'}), 404

    save_archived_calendar_entries(updated_entries)
    return jsonify({'removed': removed_count})


@app.route('/toggle_block/<pid>', methods=['POST'])
def toggle_block(pid):
    """Toggle blocked state on a project."""
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    proj['blocked'] = not proj.get('blocked', False)
    save_projects(projects)
    if request.is_json:
        return '', 204
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/toggle_urgent/<pid>', methods=['POST'])
def toggle_urgent(pid):
    """Toggle urgent highlight state on a project."""
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404
    proj['urgent'] = not proj.get('urgent', False)
    save_projects(projects)
    if request.is_json:
        return jsonify({'urgent': proj['urgent']}), 200
    return '', 204


@app.route('/toggle_launched/<pid>', methods=['POST'])
def toggle_launched(pid):
    """Toggle launched state for a specific project phase/part."""
    projects = get_projects()
    proj = next((p for p in projects if p['id'] == pid), None)
    if not proj:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    data = request.get_json(silent=True) or {}
    phase = (data.get('phase') or '').strip()
    if not phase:
        return jsonify({'error': 'Fase no indicada'}), 400

    part_raw = data.get('part')
    if part_raw in (None, '', 'None'):
        part = None
    else:
        try:
            part = int(part_raw)
        except (TypeError, ValueError):
            return jsonify({'error': 'Parte inválida'}), 400

    launched = proj.setdefault('launched_phases', [])
    if not isinstance(launched, list):
        launched = []

    idx = None
    for i, entry in enumerate(launched):
        if not isinstance(entry, dict):
            continue
        entry_phase = (entry.get('phase') or '').strip()
        entry_part = entry.get('part')
        if entry_part in ('', 'None'):
            entry_part = None
        if entry_phase == phase and entry_part == part:
            idx = i
            break

    if idx is None:
        item = {'phase': phase}
        if part is not None:
            item['part'] = part
        launched.append(item)
        launched_state = True
    else:
        launched.pop(idx)
        launched_state = False

    if launched:
        proj['launched_phases'] = launched
    else:
        proj.pop('launched_phases', None)

    save_projects(projects)
    return jsonify({'launched': launched_state}), 200


@app.route('/admin/rebuild-kanban-cache', methods=['POST'])
def rebuild_kanban_cache_route():
    payload = request.get_json(silent=True) or {}
    board_id = (
        payload.get('board_id')
        or payload.get('boardId')
        or payload.get('board')
        or request.form.get('board_id')
        or request.form.get('boardId')
    )
    card_index = rebuild_kanban_cache(board_id=board_id)
    return jsonify({'status': 'ok', 'cards': len(card_index)}), 200


@app.route('/kanbanize-webhook', methods=['POST'])
def kanbanize_webhook():
    """Convert incoming Kanbanize card data into a new project."""

    raw_body = request.get_data()

    data = None

    # 1. Intentar JSON directo
    if request.is_json:
        try:
            data = request.get_json()
        except Exception as e:
            print("Error leyendo JSON directo:", e)

    # 2. Intentar si vino como form-data o querystring
    if not data:
        payload = (
            request.form.get('kanbanize_payload')
            or request.form.get('payload')
            or request.form.get('data')
            or request.args.get('kanbanize_payload')
            or request.args.get('payload')
            or request.args.get('data')
        )
        if payload:
            data = _decode_json(payload)

    # 3. Último recurso: decodificar el body crudo
    if not data and raw_body:
        data = _decode_json(raw_body)

    if not isinstance(data, dict):
        return jsonify({'error': 'Error al procesar datos'}), 400

    # Si es un payload anidado dentro de "kanbanize_payload"
    if "kanbanize_payload" in data and isinstance(data["kanbanize_payload"], str):
        inner = _decode_json(data["kanbanize_payload"])
        if isinstance(inner, dict):
            data = inner

    print("Payload recibido:", data)

    card = data.get("card", {})
    if not isinstance(card, dict):
        card = {}

    payload_card = dict(card)

    payload_timestamp = data.get("timestamp")

    def pick(d, *keys):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        low = {k.lower(): v for k, v in d.items()}
        for k in keys:
            if k.lower() in low and low[k.lower()] is not None:
                return low[k.lower()]
        return None

    def norm(s):
        return re.sub(r'\s+', ' ', s or '').strip().lower()

    board_id = pick(card, 'boardid', 'boardId', 'board_id', 'board') or pick(
        data, 'boardid', 'boardId', 'board_id', 'board'
    )
    board_token = _normalize_board_token(board_id)

    cid = pick(card, 'taskid', 'cardId', 'id')
    normalized_cid = _normalize_card_id(cid)
    prev_card = last_kanban_card(cid)
    prev_card_tags = _extract_card_tags(prev_card)
    prev_lane = pick(prev_card, 'lanename', 'laneName', 'lane')
    prev_column = pick(prev_card, 'columnname', 'columnName', 'column')

    column_supplied = any(k in payload_card for k in ('columnname', 'columnName', 'column'))
    lane_supplied = any(k in payload_card for k in ('lanename', 'laneName', 'lane'))
    deadline_supplied = 'deadline' in payload_card
    attachments_supplied = 'Attachments' in payload_card
    tags_supplied = any(
        k in payload_card for k in ('tags', 'tagList', 'tagListData', 'tagNames', 'tag_names')
    )
    custom_fields_supplied = any(
        k in payload_card for k in ('customFields', 'customfields', 'custom_fields')
    )

    recent_fetch_entry = None
    if normalized_cid:
        recent_fetch_entry = (
            _KANBAN_CARD_FETCH_CACHE.get((board_token, normalized_cid, True))
            or _KANBAN_CARD_FETCH_CACHE.get((board_token, normalized_cid, False))
        )
    now_monotonic = time.monotonic()
    recently_fetched = False
    if recent_fetch_entry:
        fetch_timestamp, _ = recent_fetch_entry
        if now_monotonic - fetch_timestamp < KANBAN_CARD_FETCH_COOLDOWN_SECONDS:
            recently_fetched = True

    lane = pick(card, 'lanename', 'laneName', 'lane')
    column = pick(card, 'columnname', 'columnName', 'column')

    prev_lane_norm = norm(prev_lane)
    prev_column_norm = norm(prev_column)
    lane_norm = norm(lane)
    column_norm = norm(column)

    lane_changed = bool((lane_supplied or lane_norm) and lane_norm != prev_lane_norm)
    column_changed = bool((column_supplied or column_norm) and column_norm != prev_column_norm)

    fetched = None
    card_refreshed = False
    if normalized_cid:
        if not prev_card:
            fetched = _fetch_kanban_card(
                normalized_cid, with_links=True, force=True, board_id=board_id
            )
        elif lane_changed or column_changed:
            if not recently_fetched:
                fetched = _fetch_kanban_card(
                    normalized_cid, with_links=True, force=True, board_id=board_id
                )
        elif not card and not recently_fetched:
            fetched = _fetch_kanban_card(
                normalized_cid, with_links=True, board_id=board_id
            )

    if isinstance(fetched, dict):
        card_refreshed = True
        base_card = fetched
    elif prev_card:
        base_card = copy.deepcopy(prev_card)
    else:
        base_card = {}

    if isinstance(card, dict) and base_card is not card:
        base_card.update(card)

    card = base_card

    if normalized_cid and isinstance(card, dict):
        cache_snapshot = copy.deepcopy(card)
        cache_timestamp = time.monotonic()
        _KANBAN_CARD_FETCH_CACHE[(board_token, normalized_cid, False)] = (
            cache_timestamp,
            cache_snapshot,
        )
        if 'links' in cache_snapshot:
            _KANBAN_CARD_FETCH_CACHE[(board_token, normalized_cid, True)] = (
                cache_timestamp,
                cache_snapshot,
            )

    lane = pick(card, 'lanename', 'laneName', 'lane')
    column = pick(card, 'columnname', 'columnName', 'column')

    if not column_supplied and not card_refreshed and prev_column:
        column = prev_column
    if not lane_supplied and not card_refreshed and prev_lane:
        lane = prev_lane

    lane_norm = norm(lane)
    column_norm = norm(column)
    lane_changed = bool((lane_supplied or card_refreshed) and lane_norm != prev_lane_norm)
    column_changed = bool((column_supplied or card_refreshed) and column_norm != prev_column_norm)

    if isinstance(column, str):
        clean_column = column.strip()
    elif column:
        clean_column = str(column).strip()
    else:
        clean_column = ''

    if tags_supplied or card_refreshed:
        card_tags = _extract_card_tags(card)
    else:
        card_tags = prev_card_tags

    print("Evento Kanbanize → lane:", lane, "column:", column, "cid:", cid)

    # Guardar tarjetas del lane Seguimiento compras
    if lane_norm == "seguimiento compras":
        with KANBAN_CARDS_LOCK:
            cards = load_kanban_cards()
            cid_str = str(cid)
            prev = None
            new_cards = []
            for c in cards:
                existing_id = str(
                    c.get('card', {}).get('taskid')
                    or c.get('card', {}).get('cardId')
                    or c.get('card', {}).get('id')
                )
                if existing_id == cid_str:
                    prev = c
                else:
                    new_cards.append(c)
            prev_date = prev.get('stored_title_date') if prev else None
            prev_last_column = prev.get('last_column') if prev else ''
            title = card.get('title') or ''
            m = re.search(r"\((\d{2})/(\d{2})\)", title)
            if m:
                stored_date = f"{m.group(1)}/{m.group(2)}"
            else:
                stored_date = prev_date
            last_column = clean_column or prev_last_column
            if not last_column and prev:
                prev_card_data = prev.get('card') or {}
                prev_column_value = pick(prev_card_data, 'columnname', 'columnName', 'column')
                if isinstance(prev_column_value, str):
                    last_column = prev_column_value.strip()
                elif prev_column_value:
                    last_column = str(prev_column_value).strip()
            if last_column:
                existing_column = (
                    card.get('columnname')
                    or card.get('columnName')
                    or card.get('column')
                )
                if not (isinstance(existing_column, str) and existing_column.strip()):
                    # Preserve the column in the cached card so subsequent loads
                    # keep rendering the pedido even when Kanbanize events omit
                    # the column fields.
                    card = dict(card)
                    card['columnname'] = last_column
                    card['columnName'] = last_column
                    card['column'] = last_column
            entry = {'timestamp': payload_timestamp, 'card': card, 'stored_title_date': stored_date}
            if last_column:
                entry['last_column'] = last_column
            new_cards.append(entry)
            save_kanban_cards(new_cards)
        broadcast_event({"type": "kanban_update"})
        return jsonify({"mensaje": "Tarjeta procesada"}), 200

    # Lanes válidos para proyectos
    allowed_lanes_n = {norm(x) for x in ARCHIVE_LANES}
    if lane_norm not in allowed_lanes_n:
        return jsonify({"mensaje": f"Lane ignorada ({lane})"}), 200

    name_candidates = [
        pick(card, 'customCardId', 'effectiveCardId'),
        pick(card, 'title'),
    ]
    name_candidates = [n for n in name_candidates if n]
    if column_norm != 'ready to archive':
        remove_archived_calendar_entry(
            kanban_id=str(cid) if cid else None,
            names=name_candidates,
        )

    if column_norm == 'ready to archive':
        projects = load_projects()
        pid = None
        matched_project = None
        for p in projects:
            if p.get('kanban_id') == cid or (name_candidates and p.get('name') in name_candidates):
                if cid and not p.get('kanban_id'):
                    p['kanban_id'] = cid
                pid = p['id']
                matched_project = p
                break
        if pid:
            pid_str = str(pid)
            existing_since = _parse_analysis_date(matched_project.get('ready_to_archive_since'))
            archive_since = existing_since or _parse_webhook_timestamp(payload_timestamp) or local_today()
            matched_project['ready_to_archive_since'] = archive_since.isoformat()
            matched_project['kanban_archived'] = False
            matched_project['kanban_column'] = clean_column or 'Ready to Archive'
            save_projects(projects)
            broadcast_event({"type": "kanban_update"})
            return jsonify({"mensaje": "Proyecto marcado para archivar en 1 mes"}), 200
        else:
            print("Aviso: no se encontró proyecto para cid/nombre:", cid, name_candidates)
            return jsonify({"mensaje": "Evento recibido, proyecto no encontrado"}), 200


    print("Tarjeta recibida:")
    print(card)

    def _custom_dict(raw):
        if isinstance(raw, list):
            return {f.get('name'): f.get('value') for f in raw if isinstance(f, dict)}
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    prev_raw_custom = prev_card.get('customFields') or prev_card.get('customfields') or {}
    prev_custom = _custom_dict(prev_raw_custom)
    prev_custom.setdefault('CALDERERIA', prev_custom.get('CALDERERÍA'))

    custom = dict(prev_custom)
    custom_changes = set()
    if custom_fields_supplied:
        raw_custom = (
            card.get('customFields')
            or card.get('customfields')
            or {
                field.get('name'): field.get('value')
                for field in card.get('custom_fields', [])
                if isinstance(field, dict)
            }
        )
        incoming_custom = _custom_dict(raw_custom)
        incoming_custom.setdefault('CALDERERIA', incoming_custom.get('CALDERERÍA'))
        for key, value in incoming_custom.items():
            if custom.get(key) != value:
                custom_changes.add(key)
            custom[key] = value

    popup_raw = {field: custom.get(field) for field in KANBAN_POPUP_FIELDS}
    card['customFields'] = custom

    prev_popup_raw = {field: prev_custom.get(field) for field in KANBAN_POPUP_FIELDS}

    prev_deadline_str = prev_card.get('deadline')
    prev_fecha_cli_str = (
        prev_custom.get('Fecha Cliente')
        or prev_custom.get('Fecha cliente')
        or prev_custom.get('Fecha pedido')
    )
    if prev_deadline_str:
        prev_due_date_obj = parse_kanban_date(prev_deadline_str)
        prev_due_confirmed_flag = True
    else:
        prev_due_date_obj = parse_kanban_date(prev_fecha_cli_str)
        prev_due_confirmed_flag = False
    prev_mat_str = prev_custom.get('Fecha material confirmado')
    prev_material_date_obj = parse_kanban_date(prev_mat_str)

    deadline_str = card.get('deadline') if deadline_supplied else prev_deadline_str
    fecha_cli_str = (
        custom.get('Fecha Cliente')
        or custom.get('Fecha cliente')
        or custom.get('Fecha pedido')
    )
    if deadline_supplied:
        if deadline_str:
            due_date_obj = parse_kanban_date(deadline_str)
            due_confirmed_flag = True
        else:
            due_date_obj = None
            due_confirmed_flag = True
    else:
        if deadline_str:
            due_date_obj = parse_kanban_date(deadline_str)
            due_confirmed_flag = True
        else:
            due_date_obj = parse_kanban_date(fecha_cli_str)
            due_confirmed_flag = False

    mat_str = custom.get('Fecha material confirmado')
    material_date_obj = parse_kanban_date(mat_str)

    def obtener_duracion(campo):
        valor = custom.get(campo)
        if valor in [None, ""]:
            return 0
        if isinstance(valor, str):
            match = re.search(r"\d+", valor)
            return int(match.group()) if match else 0
        try:
            return int(valor)
        except Exception:
            return 0

    def obtener_duracion_prev(campo):
        valor = prev_custom.get(campo)
        if valor in [None, ""]:
            return 0
        if isinstance(valor, str):
            match = re.search(r"\d+", valor)
            return int(match.group()) if match else 0
        try:
            return int(valor)
        except Exception:
            return 0

    prep_raw = obtener_duracion('Horas Preparación')
    mont_raw = obtener_duracion('Horas Montaje')
    sold2_raw = obtener_duracion('Horas Soldadura 2º') or obtener_duracion('Horas Soldadura 2°')
    sold_raw = obtener_duracion('Horas Soldadura')
    pint_raw = obtener_duracion('Horas Acabado')
    mont2_raw = obtener_duracion('Horas Montaje 2º') or obtener_duracion('Horas Montaje 2°')

    prev_prep_raw = obtener_duracion_prev('Horas Preparación')
    prev_mont_raw = obtener_duracion_prev('Horas Montaje')
    prev_sold2_raw = obtener_duracion_prev('Horas Soldadura 2º') or obtener_duracion_prev('Horas Soldadura 2°')
    prev_sold_raw = obtener_duracion_prev('Horas Soldadura')
    prev_pint_raw = obtener_duracion_prev('Horas Acabado')
    prev_mont2_raw = obtener_duracion_prev('Horas Montaje 2º') or obtener_duracion_prev('Horas Montaje 2°')

    def flag_val(campo):
        v = custom.get(campo)
        if isinstance(v, str):
            return v.strip().lower() not in ("", "0", "false", "no")
        return bool(v)

    def flag_val_prev(campo):
        v = prev_custom.get(campo)
        if isinstance(v, str):
            return v.strip().lower() not in ("", "0", "false", "no")
        return bool(v)

    mecan_flag = flag_val('MECANIZADO')
    trat_flag = flag_val('TRATAMIENTO')
    prev_mecan_flag = flag_val_prev('MECANIZADO')
    prev_trat_flag = flag_val_prev('TRATAMIENTO')

    # Working copies that may be adjusted for automatic phases
    prep_hours, mont_hours = prep_raw, mont_raw
    sold2_hours, sold_hours = sold2_raw, sold_raw
    pint_hours, mont2_hours = pint_raw, mont2_raw

    def _clean_display_value(value):
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, bool):
            return 'Sí' if value else None
        if isinstance(value, (int, float)):
            return str(value) if value != 0 else None
        text = str(value).strip()
        return text or None

    display_fields = {}
    fecha_cliente_display = _clean_display_value(
        popup_raw.get('Fecha cliente') or popup_raw.get('Fecha Cliente')
    )
    if fecha_cliente_display:
        display_fields['Fecha cliente'] = fecha_cliente_display
    for field in ('LANZAMIENTO', 'MATERIAL', 'CALDERERIA', 'PINTADO'):
        cleaned = _clean_display_value(popup_raw.get(field))
        if cleaned:
            display_fields[field] = cleaned
    if mecan_flag:
        mecan_value = _clean_display_value(popup_raw.get('MECANIZADO')) or 'Sí'
        display_fields['MECANIZADO'] = mecan_value
    if trat_flag:
        trat_value = _clean_display_value(popup_raw.get('TRATAMIENTO')) or 'Sí'
        display_fields['TRATAMIENTO'] = trat_value

    kanban_hour_fields = [
        ('Horas Preparación', prep_hours),
        ('Horas Montaje', mont_hours),
        ('Horas Soldadura', sold_hours),
        ('Horas Montaje 2º', mont2_hours),
        ('Horas Soldadura 2º', sold2_hours),
        ('Horas Acabado', pint_hours),
    ]
    for label, value in kanban_hour_fields:
        try:
            numeric = int(value)
        except Exception:
            numeric = 0
        if numeric != 0:
            display_fields[label] = str(numeric)

    prev_display_fields = {}
    prev_fecha_cliente_display = _clean_display_value(
        prev_popup_raw.get('Fecha cliente') or prev_popup_raw.get('Fecha Cliente')
    )
    if prev_fecha_cliente_display:
        prev_display_fields['Fecha cliente'] = prev_fecha_cliente_display
    for field in ('LANZAMIENTO', 'MATERIAL', 'CALDERERIA', 'PINTADO'):
        cleaned_prev = _clean_display_value(prev_popup_raw.get(field))
        if cleaned_prev:
            prev_display_fields[field] = cleaned_prev
    if prev_mecan_flag:
        prev_mecan_value = _clean_display_value(prev_popup_raw.get('MECANIZADO')) or 'Sí'
        prev_display_fields['MECANIZADO'] = prev_mecan_value
    if prev_trat_flag:
        prev_trat_value = _clean_display_value(prev_popup_raw.get('TRATAMIENTO')) or 'Sí'
        prev_display_fields['TRATAMIENTO'] = prev_trat_value

    phase_hours_new_raw = {
        'preparar material': prep_raw,
        'montar': mont_raw,
        'soldar 2º': sold2_raw,
        'soldar': sold_raw,
        'pintar': pint_raw,
        'montar 2º': mont2_raw,
    }
    phase_hours_prev = {
        'preparar material': prev_prep_raw,
        'montar': prev_mont_raw,
        'soldar 2º': prev_sold2_raw,
        'soldar': prev_sold_raw,
        'pintar': prev_pint_raw,
        'montar 2º': prev_mont2_raw,
    }
    def field_changed(*names):
        return custom_fields_supplied and any(name in custom_changes for name in names)

    phase_update_allowed = {
        'preparar material': field_changed('Horas Preparación'),
        'montar': field_changed('Horas Montaje'),
        'montar 2º': field_changed('Horas Montaje 2º', 'Horas Montaje 2°'),
        'soldar 2º': field_changed('Horas Soldadura 2º', 'Horas Soldadura 2°'),
        'soldar': field_changed('Horas Soldadura'),
        'pintar': field_changed('Horas Acabado'),
    }
    auto_prep = False
    if (
        prep_hours <= 0
        and mont_hours <= 0
        and sold2_hours <= 0
        and sold_hours <= 0
        and pint_hours <= 0
        and mont2_hours <= 0
    ):
        prep_hours = 1
        auto_prep = True
    fases = []
    if auto_prep:
        fases.append({'nombre': 'preparar material', 'duracion': prep_hours, 'auto': True})
    elif prep_hours > 0:
        fases.append({'nombre': 'preparar material', 'duracion': prep_hours})
    if mont_hours > 0:
        fases.append({'nombre': 'montar', 'duracion': mont_hours})
    if sold2_hours > 0:
        fases.append({'nombre': 'soldar 2º', 'duracion': sold2_hours})
    if pint_hours > 0:
        fases.append({'nombre': 'pintar', 'duracion': pint_hours})
    if mont2_hours > 0:
        fases.append({'nombre': 'montar 2º', 'duracion': mont2_hours})
    if sold_hours > 0:
        fases.append({'nombre': 'soldar', 'duracion': sold_hours})
    auto_flags = {f['nombre']: True for f in fases if f.get('auto')}

    task_id = card.get('taskid') or card.get('cardId') or card.get('id')
    nombre_proyecto = (
        card.get('customCardId')
        or card.get('effectiveCardId')
        or card.get('title')
        or f"Kanbanize-{task_id or uuid.uuid4()}"
    )
    cliente = card.get('title') or "Sin cliente"

    prev_task_id = (
        prev_card.get('taskid')
        or prev_card.get('cardId')
        or prev_card.get('id')
    )
    prev_nombre_proyecto = (
        prev_card.get('customCardId')
        or prev_card.get('effectiveCardId')
        or prev_card.get('title')
    )
    prev_cliente = prev_card.get('title') or "Sin cliente"

    if attachments_supplied or card_refreshed:
        attachments_raw = card.get('Attachments') or []
    else:
        attachments_raw = prev_card.get('Attachments') or []

    kanban_files = []
    if isinstance(attachments_raw, list):
        for a in attachments_raw:
            if isinstance(a, dict):
                name = (a.get('name') or a.get('fileName') or a.get('filename') or '').strip()
                url = (a.get('url') or a.get('fileUrl') or a.get('link') or '').strip()
                if name and url:
                    if url.startswith('/') or not re.match(r'https?://', url):
                        url = f"{KANBANIZE_BASE_URL.rstrip('/')}/{url.lstrip('/')}"
                    kanban_files.append({'name': name, 'url': url})

    prev_attachments_raw = prev_card.get('Attachments') or []
    prev_kanban_files = []
    if isinstance(prev_attachments_raw, list):
        for a in prev_attachments_raw:
            if isinstance(a, dict):
                name = (a.get('name') or a.get('fileName') or a.get('filename') or '').strip()
                url = (a.get('url') or a.get('fileUrl') or a.get('link') or '').strip()
                if name and url:
                    if url.startswith('/') or not re.match(r'https?://', url):
                        url = f"{KANBANIZE_BASE_URL.rstrip('/')}/{url.lstrip('/')}"
                    prev_kanban_files.append({'name': name, 'url': url})

    image_path = None

    projects = load_projects()
    existing = next(
        (
            p
            for p in projects
            if p.get('source') == 'api' and p.get('name') == nombre_proyecto
        ),
        None,
    )
    montar_source = _get_phase_source(existing, 'montar')
    if montar_source == 'planner':
        phase_update_allowed['montar'] = False

    new_phases = {f['nombre']: f['duracion'] for f in fases}
    new_auto = {f['nombre']: True for f in fases if f.get('auto')}
    prev_column = pick(prev_card, 'columnname', 'columnName', 'column')
    if isinstance(prev_column, str):
        prev_clean_column = prev_column.strip()
    elif prev_column:
        prev_clean_column = str(prev_column).strip()
    else:
        prev_clean_column = ''

    column_changed = clean_column != prev_clean_column
    attachments_changed = (attachments_supplied or card_refreshed) and prev_kanban_files != kanban_files
    display_fields_changed = display_fields != prev_display_fields
    normalized_tags = sorted(card_tags)
    prev_normalized_tags = sorted(prev_card_tags)
    tags_changed = (tags_supplied or card_refreshed) and normalized_tags != prev_normalized_tags
    has_no_planner_tag = 'no planificador' in card_tags

    def _extract_archived_flag(card_data):
        if not isinstance(card_data, dict):
            return False
        for key in ('isArchived', 'IsArchived', 'archived', 'Archived', 'is_archived'):
            if key in card_data:
                return bool(card_data.get(key))
        return False

    is_archived = _extract_archived_flag(card)
    prev_is_archived = _extract_archived_flag(prev_card)

    if has_no_planner_tag:
        if existing:
            removed_project = remove_project_and_preserve_schedule(projects, existing['id'])
            if removed_project:
                save_projects(projects)
        with KANBAN_CARDS_LOCK:
            cards = load_kanban_cards()
            entry_last_column = clean_column or prev_clean_column
            entry = {'timestamp': payload_timestamp, 'card': card}
            if entry_last_column:
                entry['last_column'] = entry_last_column
            cards.append(entry)
            save_kanban_cards(cards)
        broadcast_event({"type": "kanban_update"})
        return jsonify({"mensaje": "Tarjeta ignorada (tag No planificador)"}), 200

    name_changed = prev_nombre_proyecto != nombre_proyecto
    client_changed = prev_cliente != cliente

    due_fields_changed = deadline_supplied or card_refreshed or any(
        field in custom_changes for field in ('Fecha Cliente', 'Fecha cliente', 'Fecha pedido')
    )
    due_changed = (
        due_fields_changed
        and ((prev_due_date_obj != due_date_obj) or (prev_due_confirmed_flag != due_confirmed_flag))
    )

    material_changed = (
        (card_refreshed or 'Fecha material confirmado' in custom_changes)
        and prev_material_date_obj != material_date_obj
    )

    if existing:
        changed = False
        if column_changed and existing.get('kanban_column') != clean_column:
            existing['kanban_column'] = clean_column
            changed = True
            if normalize_key(clean_column) != READY_TO_ARCHIVE_COLUMN:
                if existing.pop('ready_to_archive_since', None) is not None:
                    changed = True
        archived_changed = is_archived != prev_is_archived
        if archived_changed and existing.get('kanban_archived') != is_archived:
            existing['kanban_archived'] = is_archived
            changed = True
        id_changed = task_id and task_id != prev_task_id
        if task_id and (id_changed or not existing.get('kanban_id')):
            if existing.get('kanban_id') != task_id:
                existing['kanban_id'] = task_id
                changed = True
        if name_changed and existing.get('name') != nombre_proyecto:
            existing['name'] = nombre_proyecto
            changed = True
        if client_changed and existing.get('client') != cliente:
            existing['client'] = cliente
            changed = True
        if not existing.get('color') or not re.fullmatch(r"#[0-9A-Fa-f]{6}", existing.get('color', '')):
            existing['color'] = _next_api_color()
            changed = True
        if due_changed:
            if due_date_obj:
                existing['due_date'] = due_date_obj.isoformat()
                existing['due_confirmed'] = due_confirmed_flag
                existing['due_warning'] = False
            else:
                existing['due_date'] = ''
                existing['due_confirmed'] = False
            changed = True
        if material_changed:
            existing['material_confirmed_date'] = material_date_obj.isoformat() if material_date_obj else ''
            changed = True
        if image_path and existing.get('image') != image_path:
            existing['image'] = image_path
            changed = True
        if attachments_changed and existing.get('kanban_attachments') != kanban_files:
            existing['kanban_attachments'] = kanban_files
            changed = True
        if display_fields_changed and existing.get('kanban_display_fields') != display_fields:
            existing['kanban_display_fields'] = display_fields
            changed = True
        if tags_changed or existing.get('kanban_tags') != normalized_tags:
            existing['kanban_tags'] = normalized_tags
            changed = True

        existing_phases = existing.setdefault('phases', {})
        existing_assigned = existing.setdefault('assigned', {})
        existing_auto = existing.setdefault('auto_hours', {})
        if _ensure_default_initial_phases(existing):
            changed = True

        changed_phases = {}
        for ph, new_raw in phase_hours_new_raw.items():
            if not phase_update_allowed.get(ph):
                continue
            prev_raw = phase_hours_prev.get(ph, 0)
            if new_raw != prev_raw:
                changed_phases[ph] = new_phases.get(ph, 0)

        for ph, hours in changed_phases.items():
            if ph in ("mecanizar", "tratamiento") and hours == 1:
                existing_hours = existing_phases.get(ph)
                if existing_hours not in (None, 0, ''):
                    continue
            if hours > 0:
                if existing_phases.get(ph) != hours:
                    existing_phases[ph] = hours
                    changed = True
                if ph not in existing_assigned:
                    existing_assigned[ph] = UNPLANNED
                    changed = True
                if new_auto.get(ph):
                    if not existing_auto.get(ph):
                        existing_auto[ph] = True
                        changed = True
                else:
                    if existing_auto.pop(ph, None) is not None:
                        changed = True
                if ph == 'montar' and _get_phase_source(existing, 'montar') in (None, 'kanbanize'):
                    _set_phase_source(existing, 'montar', 'kanbanize')
                    changed = True
            else:
                if existing_phases.pop(ph, None) is not None:
                    changed = True
                if existing_assigned.pop(ph, None) is not None:
                    changed = True
                if existing_auto.pop(ph, None) is not None:
                    changed = True

        if _cleanup_auto_receiving_placeholder(existing):
            changed = True

        if changed:
            save_projects(projects)
    else:
        if column_norm == 'pedidos pendiente generar of':
            with KANBAN_CARDS_LOCK:
                cards = load_kanban_cards()
                entry_last_column = clean_column or prev_clean_column
                entry = {'timestamp': payload_timestamp, 'card': card}
                if entry_last_column:
                    entry['last_column'] = entry_last_column
                cards.append(entry)
                save_kanban_cards(cards)
            broadcast_event({"type": "kanban_update"})
            return jsonify({"mensaje": "Tarjeta ignorada (columna Pedidos pendiente generar OF)"}), 200
        project = {
            'id': str(uuid.uuid4()),
            'name': nombre_proyecto,
            'client': cliente,
            'start_date': local_today().isoformat(),
            'due_date': due_date_obj.isoformat() if due_date_obj else '',
            'material_confirmed_date': material_date_obj.isoformat() if material_date_obj else '',
            'color': _next_api_color(),
            'phases': new_phases,
            'assigned': {f['nombre']: UNPLANNED for f in fases},
            'auto_hours': new_auto,
            'image': image_path,
            'kanban_attachments': kanban_files,
            'kanban_display_fields': display_fields,
            'kanban_tags': normalized_tags,
            'planned': False,
            'source': 'api',
            'kanban_id': task_id,
            'due_confirmed': due_confirmed_flag,
            'due_warning': False,
            'kanban_column': clean_column,
            'kanban_archived': False,
        }
        _ensure_default_initial_phases(project)
        if 'montar' in new_phases:
            project['phase_sources'] = {'montar': 'kanbanize'}
        projects.append(project)
        save_projects(projects)

    with KANBAN_CARDS_LOCK:
        cards = load_kanban_cards()
        entry_last_column = clean_column or prev_clean_column
        entry = {'timestamp': payload_timestamp, 'card': card}
        if entry_last_column:
            entry['last_column'] = entry_last_column
        cards.append(entry)
        save_kanban_cards(cards)

    if not existing:
        extras = load_extra_conflicts()
        extras.append({
            'id': str(uuid.uuid4()),
            'project': project['name'],
            'client': project['client'],
            'message': 'Proyecto creado desde Kanbanize',
            'key': f"kanban-{project['id']}",
            'source': 'kanbanize',
            'pid': project['id'],
        })
        save_extra_conflicts(extras)
        broadcast_event({"type": "kanban_update"})
        return jsonify({"mensaje": "Proyecto creado"}), 200
    else:
        broadcast_event({"type": "kanban_update"})
        return jsonify({"mensaje": "Proyecto actualizado"}), 200




@app.route('/kanban_attachment_proxy')
def kanban_attachment_proxy():
    raw_url = (request.args.get('url') or '').strip()
    if not raw_url:
        return jsonify({'error': 'Falta url'}), 400
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except Exception:
        return jsonify({'error': 'URL inválida'}), 400
    base = urllib.parse.urlparse(KANBANIZE_BASE_URL)
    if parsed.scheme not in ('http', 'https'):
        return jsonify({'error': 'URL inválida'}), 400
    if parsed.netloc and parsed.netloc != base.netloc:
        return jsonify({'error': 'Dominio no permitido'}), 403
    target_url = raw_url
    if raw_url.startswith('/') or not parsed.netloc:
        target_url = f"{KANBANIZE_BASE_URL.rstrip('/')}/{raw_url.lstrip('/')}"
    req = Request(target_url, headers={'apikey': KANBANIZE_API_KEY})
    try:
        with urlopen(req, timeout=15) as resp:
            payload = resp.read()
            content_type = resp.headers.get('Content-Type') or 'application/octet-stream'
            return Response(payload, content_type=content_type)
    except HTTPError as exc:
        return jsonify({'error': f'No se pudo cargar adjunto ({exc.code})'}), 502
    except Exception:
        return jsonify({'error': 'No se pudo cargar adjunto'}), 502

@app.route('/hours')
def hours():
    projects = get_visible_projects()
    schedule, _ = schedule_projects(copy.deepcopy(projects))
    rows = []
    for days in schedule.values():
        for tasks in days.values():
            for t in tasks:
                if t.get('phase') == 'vacaciones':
                    continue
                rows.append({
                    'project': t.get('project'),
                    'phase': t.get('phase'),
                    'start_time': t.get('start_time'),
                    'end_time': t.get('end_time'),
                })
    rows.sort(key=lambda r: r['start_time'])
    return render_template('hours.html', rows=rows)


@app.route('/tracker')
def tracker():
    logs = load_tracker()
    logs.sort(key=lambda l: l.get('timestamp', ''), reverse=True)
    return render_template('tracker.html', logs=logs)


def _extract_html_sections(html):
    head_content = ''
    body_content = html
    if html:
        head_match = re.search(r'<head[^>]*>(.*)</head>', html, re.IGNORECASE | re.DOTALL)
        body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.IGNORECASE | re.DOTALL)
        if head_match:
            head_content = head_match.group(1)
        if body_match:
            body_content = body_match.group(1)
    return head_content, body_content


def _render_tab(title, path, view_func):
    with app.test_request_context(path):
        html = view_func()
    if hasattr(html, 'get_data'):
        html = html.get_data(as_text=True)
    head_content, body_content = _extract_html_sections(html)
    return {
        'title': title,
        'head': head_content,
        'body': body_content,
    }


@app.get('/resumen-tabs')
def resumen_tabs():
    tabs = [
        ('Calendario pedidos', '/calendario-pedidos', calendar_pedidos),
        ('Calendario de proyectos', '/calendario-proyectos', project_calendar_view),
        ('Orden carpetas', '/orden-carpetas', orden_carpetas_view),
        ('Gantt', '/gantt', gantt_view),
        ('Gantt pedidos', '/gantt-pedidos', gantt_orders_view),
        ('Notas', '/notas', note_list),
        ('Observaciones', '/observaciones', observation_list),
        ('Vacaciones', '/vacaciones', vacation_list),
        ('Recursos', '/recursos', resources),
    ]
    try:
        rendered_tabs = [_render_tab(title, path, func) for title, path, func in tabs]
    except Exception:
        app.logger.exception('Error renderizando las pestañas para el PDF')
        return Response(
            'No se pudo generar el PDF. Revisa los registros del servidor.',
            status=500,
            mimetype='text/plain',
        )
    render_all_tabs = bool(request.args.get('pdf'))
    return render_template(
        'resumen_tabs.html',
        render_all_tabs=render_all_tabs,
        tabs=rendered_tabs,
    )


def _build_complete_pdf():
    if pdfkit is None:
        raise RuntimeError('No se pudo generar el PDF. Instala pdfkit para habilitar esta función.')

    rendered_tabs = [_render_tab('Completo', '/complete?pdf=1&pdf_range=prev3', complete)]
    print_css_href = url_for('static', filename='print.css')
    print_css_path = os.path.join(app.root_path, 'static', 'print.css')

    html = render_template_string(
        """
        <!doctype html>
        <html lang=\"es\">
        <head>
          <meta charset=\"utf-8\">
          <title>{{ pdf_title }}</title>
          <link rel=\"stylesheet\" href=\"{{ print_css_href }}\">
          {{ extra_head|safe }}
          <style>
            .pdf-page { page-break-after: always; }
            .pdf-page:last-child { page-break-after: auto; }
            .pdf-page > h1 {
              margin: 0 0 12px 0;
              padding: 8px 0;
              font-size: 20px;
              border-bottom: 1px solid #333;
            }
          </style>
        </head>
        <body class=\"pdf-export\">
          {{ pages|safe }}
        </body>
        </html>
        """,
        pages=''.join(
            f"<section class=\"pdf-page\"><h1>{tab['title']}</h1>{tab['body']}</section>"
            for tab in rendered_tabs
        ),
        extra_head=''.join(tab['head'] for tab in rendered_tabs if tab.get('head')),
        pdf_title=datetime.now().strftime('%Y-%m-%d %H:%M'),
        print_css_href=print_css_href,
    )
    css_path = os.path.join(app.root_path, 'static', 'style.css')
    options = {
        'enable-local-file-access': '',
        'quiet': '',
        'load-error-handling': 'ignore',
        'print-media-type': '',
        'orientation': 'Landscape',
        'page-size': 'A0',
        'zoom': '0.5',
        'viewport-size': '2560x1440',
    }
    return pdfkit.from_string(
        html,
        False,
        configuration=config,
        options=options,
        css=[css_path, print_css_path],
    )


def _find_chrome_binary():
    possible_chromes = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    ]
    for candidate in possible_chromes:
        if os.path.exists(candidate):
            return candidate
    for candidate in ('google-chrome', 'chromium', 'chromium-browser'):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _build_print_url(path):
    base_url = urllib.parse.urljoin(request.url_root, path.lstrip('/'))
    parts = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query['pdf'] = '1'
    new_query = urllib.parse.urlencode(query)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _render_tab_pdf_chrome(url, output_path, chrome_binary):
    cmd = [
        chrome_binary,
        '--headless',
        '--disable-gpu',
        '--no-sandbox',
        '--hide-scrollbars',
        '--print-to-pdf-no-header',
        f'--print-to-pdf={output_path}',
        '--window-size=4000,2000',
        '--virtual-time-budget=10000',
        url,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f'PDF vacío generado para {url}')


def _json_pdf_error(message, status=500):
    return jsonify({'error': message}), status


def _summary_pdf_filename():
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
    return f"resumen_{timestamp}.pdf"


def _build_summary_pdf():
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError('No se pudo generar el PDF. Instala pypdf para habilitar esta función.')

    chrome_binary = _find_chrome_binary()
    if not chrome_binary:
        raise RuntimeError('No se encontró Chrome/Chromium para generar el PDF.')

    try:
        complete_pdf = _build_complete_pdf()
    except Exception as exc:
        raise RuntimeError('No se pudo generar el PDF. Revisa la configuración de wkhtmltopdf.') from exc

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            complete_path = os.path.join(temp_dir, 'parte_1_completo.pdf')
            with open(complete_path, 'wb') as f:
                f.write(complete_pdf)
            pdf_paths = [complete_path]

            tabs_output = os.path.join(temp_dir, 'parte_2_tabs.pdf')
            url = _build_print_url('/resumen-tabs')
            _render_tab_pdf_chrome(url, tabs_output, chrome_binary)
            pdf_paths.append(tabs_output)

            writer = PdfWriter()
            for pdf_path in pdf_paths:
                reader = PdfReader(pdf_path)
                for page in reader.pages:
                    writer.add_page(page)

            final_path = os.path.join(temp_dir, 'resumen_final.pdf')
            with open(final_path, 'wb') as f:
                writer.write(f)

            with open(final_path, 'rb') as f:
                return f.read()
    except Exception as exc:
        raise RuntimeError('No se pudo generar el PDF. Revisa los registros del servidor.') from exc


def create_summary_pdf_backup():
    """Generate the same summary PDF used by the download endpoint."""

    try:
        pdf = _build_summary_pdf()
    except Exception as exc:
        message = str(exc) or 'No se pudo generar el PDF.'
        app.logger.exception('%s Error original: %s', message, exc)
        return None, _json_pdf_error(message)

    if not pdf:
        message = 'No se pudo generar el PDF: el proceso no devolvió contenido.'
        app.logger.error(message)
        return None, _json_pdf_error(message)

    return pdf, None


def _make_pdf_download_response(pdf, filename, backup_path=None):
    response = Response(pdf, mimetype='application/pdf')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    if backup_path:
        response.headers['X-Backup-Filename'] = os.path.basename(backup_path)
    return response


@app.route('/backup-pdf', methods=['POST'])
def backup_pdf():
    pdf, error_response = create_summary_pdf_backup()
    if error_response is not None:
        return error_response

    try:
        backup_path = create_full_backup()
    except Exception as exc:
        message = 'No se pudo generar el backup. Revisa los permisos de la carpeta de backups.'
        app.logger.exception('%s Error original: %s', message, exc)
        return _json_pdf_error(message)

    app.logger.info('Backup generado antes de descargar el PDF: %s', backup_path)
    return _make_pdf_download_response(pdf, _summary_pdf_filename(), backup_path)


@app.get('/exportar_pdf')
def exportar_pdf():
    try:
        final_pdf = _build_summary_pdf()
    except Exception as exc:
        message = str(exc) or 'No se pudo generar el PDF.'
        app.logger.exception('%s Error original: %s', message, exc)
        return Response(message, status=500, mimetype='text/plain')

    return _make_pdf_download_response(final_pdf, _summary_pdf_filename())


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000, debug=True)
