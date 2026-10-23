#!/usr/bin/env python3
"""Shared configuration entry point for the maintained NERSC campaign.

Examples: campaign.py --config "$HIC_CAMPAIGN_CONFIG" prepare-cache
          campaign.py --config "$HIC_CAMPAIGN_CONFIG" cache --submit
          campaign.py --config "$HIC_CAMPAIGN_CONFIG" analysis aggregate

Commands that submit jobs, run extraction or delete data only preview unless
given --submit (events, cache, reap, analysis run) or --apply (repair,
initialize). prepare-cache only surveys and writes a candidate manifest; review
only classifies records and writes the clean-only reap manifest.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
from workflow_config import read_config, expand_path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'status': ('models/cache/survey_raw_designs.py', []),
    'events': ('models/run_designs.py', []),
    'repair': ('models/run_designs.py', ['--repair-only']),
    'initialize': ('models/run_designs.py', ['--initialize-only']),
    'manifest': ('models/cache/make_cache_reap_manifest.py', []),
    'cache': ('models/submit_cache_reap_campaign.py', ['--stage', 'cache']),
    'reap': ('models/submit_cache_reap_campaign.py', ['--stage', 'reap']),
    'review': ('models/summarize_cache_reap_records.py', []),
    'analysis': ('models/analysis/run_analysis.py', []),
    'analysis-design': ('models/analysis/analyze_design.py', []),
    'inventory': ('models/analysis/inspect_inventory.py', []),
    'validate': ('models/analysis/check_cumulant_consistency.py', []),
    'fill-plan': ('models/select_fill.py', []),
    'fill': ('models/fill_wave.sh', []),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-h', '--help', action='store_true',
                        help='Show dispatcher help, or selected Python command/subcommand help.')
    parser.add_argument('--config', help='Shared campaign INI (required except for help).')
    parser.add_argument('--print-commands', action='store_true', help='Print commands without executing them.')
    parser.add_argument('command', nargs='?', choices=sorted(list(TARGETS) + ['prepare-cache']))
    args, extra = parser.parse_known_args()
    # argparse's automatic help would consume "analysis run --help" at this
    # outer parser. Delegate before reading configuration or performing work.
    if args.help:
        if args.command in TARGETS:
            script, fixed = TARGETS[args.command]
            if script.endswith('.py'):
                return subprocess.run([sys.executable, str(ROOT / script)] + fixed
                                      + extra + ['--help']).returncode
        parser.print_help()
        return 0
    if not args.config or not args.command:
        parser.error('--config and command are required except for help')
    try:
        config = read_config(args.config)
        config_path = expand_path(args.config)
        commands = ['status', 'manifest'] if args.command == 'prepare-cache' else [args.command]
        if args.command == 'prepare-cache' and extra:
            parser.error('prepare-cache takes settings from INI; use survey or manifest for CLI overrides')
        for command in commands:
            script, fixed = TARGETS[command]
            tail = list(fixed)
            if args.command == 'prepare-cache' and command == 'status':
                tail.append('--quiet')
            if command == 'review':
                manifest = config.get('pipeline', 'reap_manifest', fallback=None)
                if manifest:
                    tail += ['--reap-manifest', expand_path(manifest)]
            environment = os.environ.copy()
            if command == 'fill':
                for name, section, key in (
                    ('RAW_ROOT', 'run', 'directory'), ('NEVENTS', 'run', 'nevents'),
                    ('SYSTEM', 'run', 'system'), ('CPT', 'submit', 'cpus_per_task'),
                    ('MAXRETRY', 'submit', 'max_afterburner_retries'),
                    ('LOG_ROOT', 'submit', 'event_log_root'),
                    ('HIC_SURFACE_ARCHIVE_ROOT', 'repair', 'archive_root'),
                    ('HIC_SURFACE_ARCHIVE_BASE', 'repair', 'archive_base'),
                ):
                    value = config.get(section, key, fallback=None)
                    if value:
                        environment[name] = expand_path(value) if name in (
                            'RAW_ROOT', 'LOG_ROOT', 'HIC_SURFACE_ARCHIVE_ROOT',
                            'HIC_SURFACE_ARCHIVE_BASE') else value
                argv = ['bash', str(ROOT / script)] + extra
            else:
                argv = [sys.executable, str(ROOT / script), '--config', config_path] + tail + extra
            if args.print_commands:
                print(shlex.join(argv), flush=True)
            if not args.print_commands:
                result = subprocess.run(argv, env=environment)
                if result.returncode:
                    return result.returncode
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main())
