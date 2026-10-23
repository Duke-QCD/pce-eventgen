"""Human-facing campaign status and bounded output shared by survey/review tools.

Status describes the next action. It never authorizes caching or deletion. Cache
and reap labels come from records/markers; no cache payloads are read here.
"""

from collections import Counter
import os
import textwrap

from workflow_records import classify_history


ACTIONS = {
    'needs_run': 'Run/fill remaining events',
    'waiting': 'Check active jobs or stale locks',
    'ready_to_cache': 'Cache/publish',
    'verify_cache': 'Run cache to re-verify published cache',
    'cached': 'Continue with analysis; nothing to reap',
    'ready_to_reap': 'Review, then reap explicitly',
    'reaped': 'Continue with analysis',
    'review': 'Inspect/repair before proceeding',
}
RECORD_STATES = {
    'clean_ready_for_stage2': 'ready_to_reap',
    'clean_already_reaped': 'reaped',
    'clean_nothing_to_reap': 'cached',
    'applied_clean': 'reaped',
    'stage2_stale_needs_restage1': 'verify_cache',
}


def record_status(category):
    return RECORD_STATES.get(category, 'review')


def annotate_row(row, first=None, second=None, good_buckets=(), ambiguous=False,
                 cache_cfs_root=None, target_events=None):
    """Combine current raw counts with historical evidence, preserving raw fields.

    Absent particles alone never prove a reap. A clean cache record is historical readiness
    for reap; the wrapper still performs fresh verification when actually run.
    """
    row.pop('record_issue', None)
    category, reason = classify_history(first, second, good_buckets)
    row['record_classification'] = category
    row['record_paths'] = [item[1] for item in (first, second) if item]
    state = None
    if ambiguous:
        state, reason = 'review', 'Duplicate or unreadable records; history is ambiguous'
    for item in (first, second):
        if not item or state:
            continue
        rec = item[0]
        if rec.get('raw_design') and os.path.realpath(rec['raw_design']) != os.path.realpath(row['path']):
            state, reason = 'review', 'Record raw path differs from this campaign'
        elif cache_cfs_root and rec.get('cfs_cache') and os.path.realpath(rec['cfs_cache']) != os.path.realpath(
                os.path.join(cache_cfs_root, row['design'], row['system'])):
            state, reason = 'review', 'Record cache path differs from this campaign'
        elif target_events is not None and rec.get('target_events', target_events) != target_events:
            state, reason = 'review', 'Record event target differs from this campaign'
    if state:
        row['record_issue'] = reason
    elif category:
        state = record_status(category)
        reason = reason or category.replace('_', ' ')
        if category == 'stage2_stale_needs_restage1':
            reason = 'Cache changed after verification; rerun cache/publish verification'
        if state == 'ready_to_reap' and row.get('locks_present'):
            state, reason = 'waiting', 'Cached previously; raw events now have locks'
    elif row.get('reaped_marker'):
        state, reason = 'reaped', 'reaped.done present; no wrapper reap record'
    elif row.get('error'):
        state, reason = 'review', row['error']
    elif row.get('locks_present'):
        state, reason = 'waiting', f"{row['locks_present']} event(s) have locks"
    elif any(row.get(k) for k in ('no_surface_conflict', 'both_files', 'zero_byte_particle',
                                  'extra_event_dirs', 'completed_zero', 'incomplete', 'unreadable')):
        problems = [f'{k}={row[k]}' for k in ('no_surface_conflict', 'both_files', 'zero_byte_particle',
                    'extra_event_dirs', 'completed_zero', 'incomplete', 'unreadable') if row.get(k)]
        state, reason = 'review', ', '.join(problems)
        if row.get('completed_zero'):
            reason += '; missing particles do not prove a prior reap'
    elif row.get('raw_ready_for_cache', row.get('ready_for_cache_reap')):
        state, reason = 'ready_to_cache', 'All expected events terminal; no raw blockers'
        if cache_cfs_root and os.path.isfile(os.path.join(cache_cfs_root, row['design'], row['system'], 'cache.verified')):
            state, reason = 'verify_cache', 'Published marker found without clean record; run cache to re-verify'
    elif not row.get('scanned_expected'):
        state, reason = 'review', 'No events found and no expected event target supplied'
    else:
        state = 'needs_run'
        reason = f"hydro/both={row.get('needs_hydro', 0)}, afterburner={row.get('needs_afterburner', 0)}"
    row.update(status=state, next_action=ACTIONS[state], status_reason=reason)
    return row


def add_output_args(parser, filters=True):
    parser.add_argument('--details', action='store_true', help='Show per-design reasons after the compact summary.')
    parser.add_argument('--max-rows', type=int, default=20, help='Maximum detail rows (default 20; 0 = all).')
    if filters:
        parser.add_argument('--only', choices=['all'] + list(ACTIONS), default='all',
                            help='Filter the displayed design list; full JSON/manifest selection is unchanged.')


def report(rows, title, only='all', details=False, max_rows=20):
    """Print counts before bounded design lists; full detail stays in JSON/logs."""
    counts = Counter(row['status'] for row in rows)
    print(f'\n{title}: {len(rows)} design/system points')
    print(f"{'Status':<16} {'Count':>6}  Next action")
    for state, action in ACTIONS.items():
        if counts[state]:
            print(f'{state:<16} {counts[state]:>6}  {action}')
    visible = [row for row in rows if only == 'all' or row['status'] == only]
    print('\nDesigns' + (f' ({only})' if only != 'all' else '') + ':')
    for state in ACTIONS:
        systems = sorted({r['system'] for r in visible if r['status'] == state})
        for system in systems:
            names = sorted(r['design'] for r in visible if r['status'] == state and r['system'] == system)
            # Always bounded, independent of campaign size; --details --max-rows 0 lists all.
            label = f'  {state}/{system}: '
            shown = ', '.join(names[:6])
            if len(names) > 6:
                shown += f' (+{len(names) - 6} more)'
            print(textwrap.fill(label + shown, width=100, subsequent_indent='    '))
    if not visible:
        print('  (none)')
    if details:
        print('\nDesign / system          Status           Terminal/target  Reason')
        limit = max_rows if max_rows > 0 else len(visible)
        for row in visible[:limit]:
            n, target = row.get('terminal'), row.get('scanned_expected')
            progress = f'{n}/{target}' if n is not None and target is not None and not row.get('reaped_marker') else '-'
            print(f"{row['design']}/{row['system']:<10} {row['status']:<16} {progress:>15}")
            print(textwrap.fill(str(row.get('status_reason') or '-'), width=100,
                                initial_indent='  ', subsequent_indent='  '))
        if len(visible) > limit:
            print(f'  {len(visible) - limit} detail rows omitted; use --max-rows 0 for all.')
    else:
        print('Use --only STATUS --details for reasons; --max-rows 0 lists every matching design.')
