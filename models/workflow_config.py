"""Shared INI defaults for the maintained NERSC workflow entry points.

Precedence: explicit CLI > INI > existing script/environment defaults. Relative
paths retain run_designs.py's launch-directory convention. Action/acceptance flags
are deliberately CLI-only: a shared config cannot submit, reap, or accept a short
campaign implicitly. This module never sources shell files or runs a model.
"""

import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import sys

GENERATION_SECTIONS = {
    'files': {'params', 'design', 'launcher', 'music_input_template'},
    'run': {'directory', 'system', 'prefix', 'row_start', 'row_end', 'nevents',
            'jobs_per_design', 'initialize', 'refuse_existing'},
    'submit': {'mode', 'restore_archives', 'max_afterburner_retries', 'time', 'qos',
               'nodes', 'ntasks_per_node', 'cpus_per_task', 'event_log_root', 'slurm_log_root'},
    'repair': {'apply_repairs', 'repair_script', 'repair_log_root', 'finalize_archive_failed',
               'archive_root', 'archive_base', 'zstd_bin', 'zstd_level', 'archive_full_verify',
               'archive_remove_local', 'afterburner_done_template'},
}
EXTRA_SECTIONS = {
    'pipeline': {'record_dir', 'survey_json', 'cache_manifest', 'reap_manifest',
                 'cache_local_root', 'cache_cfs_root', 'expected_fingerprint', 'tmp_dir',
                 'analysis_out_dir', 'pdg_mass', 'baryon_dict', 'slurm_log_root'},
    'cache': {'nprocs', 'validate_events', 'events_per_shard', 'allow_malformed_rows',
              'max_dropped_events', 'designs_per_task', 'throttle', 'time', 'qos',
              'account', 'constraint', 'nodes_per_task', 'cores', 'jobs'},
    'analysis_campaign': {'jobs', 'estimator', 'bin_edges', 'y_window', 'charged_window',
                          'n_boot', 'et_eta_window', 'harmonics'},
    'feeddown': {'pids', 'combine_antiparticles', 'out'},
}
SECTION_KEYS = {**GENERATION_SECTIONS, **EXTRA_SECTIONS}
PATH_OPTIONS = {
    'raw_root', 'run_root', 'cache_root', 'cache_cfs_root', 'cache_local_root',
    'fingerprint', 'expected_fingerprint', 'record_dir', 'slurm_log_root', 'tmp_dir',
    'survey_json', 'manifest', 'json', 'out', 'out_dir', 'matrix_out', 'reap_manifest',
    'wrapper', 'wrapper_script', 'cache_script', 'publish_script', 'reaper_script',
    'pdg_mass', 'baryon_dict', 'raw_design', 'local_cache', 'cfs_cache', 'matrix',
    'cache_dir', 'artifact', 'reduce_json',
}
SCOPE_ENV = {
    'charged_eta_max': 'HIC_ANALYSIS_CHARGED_ETA_MAX',
    'identified_y_max': 'HIC_ANALYSIS_IDENTIFIED_Y_MAX',
    'identified_flow_y_max': 'HIC_ANALYSIS_IDENTIFIED_FLOW_Y_MAX',
    'flow_pt_min': 'HIC_ANALYSIS_FLOW_PT_MIN', 'flow_pt_max': 'HIC_ANALYSIS_FLOW_PT_MAX',
    'subevent_eta_gap': 'HIC_ANALYSIS_SUBEVENT_ETA_GAP',
    'inventory_eta_max': 'HIC_ANALYSIS_INVENTORY_ETA_MAX',
}


def scope_environment(fingerprint_path):
    """Cache-cut environment for backends, derived from a frozen fingerprint JSON.

    The fingerprint is the single source of truth for the cache scope; backends
    read these variables, so they are always exported from it rather than set by hand.
    """
    with open(fingerprint_path) as stream:
        fingerprint = json.load(stream)
    return {name: str(fingerprint[key]) for key, name in SCOPE_ENV.items()}


def expand_path(value):
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    if re.search(r'\$(?:[A-Za-z_]\w*|\{[^}]+\})', expanded):
        raise ValueError(f'Unresolved environment variable in path: {value}')
    return str(Path(expanded).resolve())


def read_config(path):
    parser = configparser.ConfigParser(interpolation=None)
    config_path = expand_path(path)
    if not parser.read(config_path):
        raise FileNotFoundError(f'Config file not found: {config_path}')
    validate_sections(parser)
    return parser


def validate_sections(parser):
    for section in parser.sections():
        if section not in SECTION_KEYS:
            raise ValueError(f'Unknown config section [{section}]')
        unknown = set(parser[section]) - SECTION_KEYS[section]
        if unknown:
            raise ValueError(f'Unknown key(s) in [{section}]: {", ".join(sorted(unknown))}')


def defaults(config, tool, stage=None, design=None, system_override=None):
    """Map one campaign's inputs into each existing script's CLI vocabulary."""
    def value(section, key, fallback=None):
        return config.get(section, key, fallback=fallback) or fallback

    p = dict(config['pipeline']) if config.has_section('pipeline') else {}
    cache_manifest = p.get('cache_manifest')
    reap_manifest = p.get('reap_manifest')
    run_root = value('run', 'directory')
    system = system_override or value('run', 'system')
    nevents = value('run', 'nevents')
    common = {'system': system, 'target_events': nevents, 'raw_root': run_root,
              'record_dir': p.get('record_dir'), 'tmp_dir': p.get('tmp_dir')}
    prefix = value('run', 'prefix', 'design')
    if tool == 'survey':
        return {**common, 'json': p.get('survey_json'), 'design_glob': prefix + '_*',
                'cache_cfs_root': p.get('cache_cfs_root')}
    if tool == 'manifest':
        return {**common, 'survey_json': p.get('survey_json'), 'out': cache_manifest,
                'cache_cfs_root': p.get('cache_cfs_root')}
    if tool == 'summary':
        return {'record_dir': p.get('record_dir'), 'system': system}
    if tool == 'fill':
        return {'run_root': run_root, 'system': system, 'nevents': nevents, 'prefix': prefix}
    if tool == 'cache':
        cache = dict(config['cache']) if config.has_section('cache') else {}
        return {**common, **cache, 'num_events': nevents, 'max_events': nevents,
                'manifest': reap_manifest if stage == 'reap' else cache_manifest,
                'cache_cfs_root': p.get('cache_cfs_root'), 'cache_local_root': p.get('cache_local_root'),
                'fingerprint': p.get('expected_fingerprint'),
                'slurm_log_root': p.get('slurm_log_root', value('submit', 'slurm_log_root')),
                'pdg_mass': p.get('pdg_mass'), 'baryon_dict': p.get('baryon_dict')}
    if tool == 'design-cache':
        result = defaults(config, 'cache', stage, system_override=system_override)
        result['expected_fingerprint'] = result['fingerprint']
        if design and system:
            for dest, root in (('raw_design', run_root), ('local_cache', p.get('cache_local_root')),
                               ('cfs_cache', p.get('cache_cfs_root'))):
                if root:
                    result[dest] = os.path.join(root, design, system)
        return result
    if tool == 'validate':
        out = p.get('analysis_out_dir')
        return {'matrix': os.path.join(out, 'model_matrix.npz') if out else None,
                'harmonics': value('analysis_campaign', 'harmonics')}
    if tool in ('analysis', 'analysis-design', 'inventory'):
        analysis = dict(config['analysis_campaign']) if config.has_section('analysis_campaign') else {}
        result = {**analysis, 'system': system, 'cache_root': p.get('cache_cfs_root'),
                  'out_dir': p.get('analysis_out_dir'), 'pattern': prefix + '_',
                  'expected_fingerprint': p.get('expected_fingerprint')}
        if tool == 'analysis':
            feeddown = dict(config['feeddown']) if config.has_section('feeddown') else {}
            out_dir = p.get('analysis_out_dir')
            if out_dir:
                result['out'] = os.path.join(out_dir, 'feeddown_yields.npz')
            result.update(feeddown)
        return result
    raise ValueError(f'Unknown workflow tool: {tool}')


def _all_parsers(parser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _all_parsers(child)


def parse_args(parser, tool, argv=None):
    """Apply typed INI defaults without reconstructing CLI strings or action flags.

    --config works before or after a subcommand. Existing commands without it
    retain their original defaults. Required CLI inputs may be supplied by INI.
    """
    parser.add_argument('--config', help='Shared campaign INI; CLI values override it.')
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument('--config')
    known, remaining = probe.parse_known_args(sys.argv[1:] if argv is None else argv)
    if known.config is None:
        return parser.parse_args(remaining)
    try:
        config = read_config(known.config)
        stage_probe = argparse.ArgumentParser(add_help=False)
        stage_probe.add_argument('--stage')
        stage_probe.add_argument('--design')
        stage_probe.add_argument('--system')
        stage, _ = stage_probe.parse_known_args(remaining)
        options = defaults(config, tool, stage.stage, stage.design, stage.system)
        for child in _all_parsers(parser):
            for action in child._actions:
                value = options.get(action.dest)
                if value is None:
                    continue
                if action.dest in PATH_OPTIONS:
                    # Resolve only the selected subcommand's paths after parsing.
                    # Aggregate/validate do not need raw/cache/data environments.
                    pass
                elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
                    value = configparser.ConfigParser.BOOLEAN_STATES[value.lower()]
                elif action.nargs in ('*', '+'):
                    value = [(action.type or str)(item) for item in shlex.split(value)]
                elif action.type:
                    value = action.type(value)
                if action.choices and value not in action.choices:
                    raise ValueError(f'Invalid config value for {action.dest}: {value}')
                action.default = value
                action.required = False
                if not action.option_strings and action.nargs is None:
                    action.nargs = '?'
        args = parser.parse_args(remaining)
        args.config = expand_path(known.config)
        if tool in ('cache', 'design-cache'):
            explicit = {arg.split('=', 1)[0] for arg in remaining if arg.startswith('--')}
            for key in ('num_events', 'max_events'):
                if hasattr(args, key) and '--' + key.replace('_', '-') not in explicit:
                    setattr(args, key, args.target_events)
        for key in PATH_OPTIONS:
            value = getattr(args, key, None)
            if value is not None:
                setattr(args, key, expand_path(value))
        if tool in ('cache', 'design-cache'):
            # The frozen fingerprint is the authoritative cache scope.
            os.environ.update(scope_environment(
                getattr(args, 'fingerprint', None) or args.expected_fingerprint))
            os.environ['HIC_CACHE_SYSTEM'] = args.system
            for key, name in (('pdg_mass', 'HIC_PDG_MASS'), ('baryon_dict', 'HIC_BARYON_DICT')):
                if getattr(args, key, None):
                    os.environ[name] = getattr(args, key)
            if hasattr(args, 'max_dropped_events'):
                os.environ['HIC_MAX_DROPPED_EVENTS'] = str(args.max_dropped_events)
        return args
    except (OSError, ValueError, KeyError, configparser.Error) as error:
        parser.error(str(error))
