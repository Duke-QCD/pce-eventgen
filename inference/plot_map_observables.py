#!/usr/bin/env python3
"""Estimate the physical-parameter MAP and plot each system against its fitted data.

Accepts a saved single-system or joint run. A joint fit is optimized once and
projected into each system; posterior medians are not substituted for a MAP.
The objective retains the fit's Gaussian normalization and emulator variance,
with no logit-coordinate Jacobian. No MCMC or new collision simulations run.
"""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import warnings

import numpy as np
from scipy.optimize import minimize

import inference as I
import joint_inference as J
import make_flow_plots as F


@dataclass
class PlotSystem:
    system: str
    inputs: object
    emulator: object
    projection: list


@dataclass
class Fit:
    kind: str
    posterior: Path
    config: dict
    samples: np.ndarray
    pnames: list
    ranges: np.ndarray
    fixed: dict
    free_pos: list
    free_names: list
    free_ranges: np.ndarray
    template: np.ndarray
    emulator: object
    measured: np.ndarray
    sigmas: np.ndarray
    covariance: object
    systems: list
    reconstruction: dict


def detect_posterior(run_dir, system=None):
    root = Path(run_dir).expanduser().resolve()
    if system is not None:
        return root / f"{I.system_config(system)['prefix']}_posterior.npz", system
    candidates = [(root / 'joint_posterior.npz', None)]
    candidates += [(root / f"{cfg['prefix']}_posterior.npz", name) for name, cfg in I.SYSTEMS.items()]
    found = [(path, name) for path, name in candidates if path.is_file()]
    if len(found) != 1:
        raise ValueError("Run directory must contain exactly one recognized posterior; "
                         "use --system to select a single-system posterior when several are present")
    return found[0]


def load_fit(run_dir, *, system=None, config=None, matrix=None, design=None, data_dir=None):
    """Verify the saved fit before training any emulator or creating output files."""
    posterior, name = detect_posterior(run_dir, system)
    if name is None:
        if any(value is not None for value in (matrix, design, data_dir)):
            raise ValueError("Use --config for relocated joint inputs; individual path overrides are single-system only")
        study = J.load_study(config) if config is not None else None
        problem, samples, record = J.restore(run_dir, study)
        emulators = J.restore_emulators(problem, record)
        systems = [PlotSystem(c.spec.system, c.inputs, emu, c.projection)
                   for c, emu in zip(problem.components, emulators)]
        return Fit('joint', posterior, record, samples, problem.pnames, problem.ranges,
                   problem.fixed_params, problem.free_pos, problem.free_names, problem.free_ranges,
                   problem.template, J.JointEmulator(problem, emulators), problem.measured,
                   problem.sigmas, problem.covariance, systems, dict(method='saved',
                   settings=record['emulator_settings'], inflation=record['emulator_variance_inflation']))
    if config is not None:
        raise ValueError("--config selects relocated inputs for a joint fit only")
    raw = I.read_npz(posterior)
    if 'config_json' not in raw:
        raise ValueError("MAP reconstruction requires saved input/settings provenance in the posterior")
    post, drop, feeddown = I.posterior_inputs(run_dir, name, matrix, design, data_dir)
    record = json.loads(str(post['config_json']))
    inputs = I.prepare_inputs(drop_specs=drop, feeddown=feeddown)
    if record.get('usable_design_indices') != inputs.indices.tolist():
        raise ValueError("Usable design rows differ from the saved fit")
    fixed = dict(I.FIXED_PARAMS)
    positions, names, ranges, template = I.resolve_free(inputs.pnames, inputs.ranges, fixed_params=fixed)
    samples = post['samples']
    if np.any(samples < ranges[:, 0]) or np.any(samples > ranges[:, 1]):
        raise ValueError("Saved posterior samples lie outside their parameter priors")
    has_settings = 'emulator_settings' in record
    has_inflation = 'emulator_variance_inflation' in record
    if has_settings != has_inflation:
        raise ValueError("Saved emulator settings/calibration are incomplete")
    if has_settings:
        inflation = np.asarray(record['emulator_variance_inflation'], float)
        if inflation.shape != (len(inputs.labels),) or not np.all(np.isfinite(inflation) & (inflation >= 1)):
            raise ValueError("Invalid saved emulator uncertainty calibration")
        settings = record['emulator_settings']
        emulator = I.PCAGPEmulator(inputs.X, inputs.Y, var_inflation=inflation, **settings)
        reconstruction = dict(method='saved', settings=settings, inflation=inflation.tolist())
    else:
        folds = record.get('nsplits')
        if type(folds) is not int or not 2 <= folds <= len(inputs.X):
            raise ValueError("Older fit requires its recorded closure fold count to reconstruct uncertainty")
        warnings.warn("Older fit has no saved GP settings/variance inflation: recomputing closure with "
                      "the recorded folds and current production emulator settings. The MAP is an "
                      "estimate under that reconstruction, rather than a verified original emulator.",
                      UserWarning, stacklevel=2)
        emulator, _ = I.fit_emulator(inputs, Path(run_dir)/'map_reconstruction',
                                     n_splits=folds, make_plots=False)
        reconstruction = dict(method='recomputed_legacy', settings=I.emulator_settings(), nsplits=folds,
                              inflation=emulator.var_inflation.tolist())
    return Fit('single', posterior, record, samples, inputs.pnames, inputs.ranges, fixed,
               positions, names, ranges, template, emulator, inputs.measured, inputs.sigmas,
               inputs.covariance, [PlotSystem(name, inputs, emulator, list(range(len(inputs.pnames))))],
               reconstruction)


def physical_logprob(fit):
    """Uniform-box posterior density in physical coordinates, including its boundaries.

    Only the constant prior density is omitted. The Gaussian determinant and
    predictive uncertainty are retained. A logit Jacobian would change the MAP.
    """
    gaussian = I.gaussian_measurement_loglike(fit.sigmas, fit.covariance)
    lower, upper = fit.free_ranges[:, 0], fit.free_ranges[:, 1]

    def evaluate(theta):
        theta = np.atleast_2d(np.asarray(theta, float))
        if theta.shape[1] != len(fit.free_names):
            raise ValueError("Expected one column for every sampled parameter")
        inside = np.all(np.isfinite(theta) & (theta >= lower) & (theta <= upper), axis=1)
        result = np.full(len(theta), -np.inf)
        if inside.any():
            full = I.expand_free(theta[inside], fit.free_pos, fit.template)
            mean, std = fit.emulator.predict(full, return_std=True)
            result[inside] = gaussian(mean-fit.measured, std)
        return result
    return evaluate


def validate_search(max_samples, starts, maxiter, seed, batch_size):
    if any(type(v) is not int or v < 1 for v in (max_samples, starts, maxiter, batch_size)):
        raise ValueError("Search counts must be positive integers")
    if type(seed) is not int or seed < 0:
        raise ValueError("Seed must be a nonnegative integer")


def estimate_map(logprob, samples, ranges, *, max_samples=1024, starts=4, maxiter=400, seed=12345,
                 batch_size=64):
    """Screen saved draws, then refine high-density candidates in bounded unit coordinates."""
    validate_search(max_samples, starts, maxiter, seed, batch_size)
    samples, ranges = np.asarray(samples, float), np.asarray(ranges, float)
    if (samples.ndim != 2 or not len(samples) or ranges.shape != (samples.shape[1], 2)
            or not np.isfinite(samples).all() or not np.isfinite(ranges).all()
            or np.any(ranges[:, 1] <= ranges[:, 0])
            or np.any(samples < ranges[:, 0]) or np.any(samples > ranges[:, 1])):
        raise ValueError("Need finite samples inside a nonempty parameter box")
    rng = np.random.default_rng(seed)
    indices = rng.choice(len(samples), min(max_samples, len(samples)), replace=False)
    candidates = np.vstack([samples[indices], np.median(samples, axis=0)])
    scores = np.concatenate([logprob(candidates[i:i+batch_size])
                             for i in range(0, len(candidates), batch_size)])
    good = np.flatnonzero(np.isfinite(scores))
    if not len(good):
        raise ValueError("No finite posterior density at the search starting points")
    order = good[np.argsort(scores[good])[::-1]]
    lower, width = ranges[:, 0], ranges[:, 1]-ranges[:, 0]
    best = dict(point=candidates[order[0]].copy(), logp=float(scores[order[0]]), converged=False)
    trials, used = [], set()

    def physical(unit):
        return np.clip(lower+width*unit, ranges[:, 0], ranges[:, 1])

    def objective(unit):
        value = float(logprob(physical(unit))[0])
        return -value if np.isfinite(value) else np.inf

    for idx in order:
        unit = (candidates[idx]-lower)/width
        key = tuple(unit)
        if key in used:
            continue
        used.add(key)
        result = minimize(objective, unit, method='L-BFGS-B', bounds=[(0., 1.)]*len(unit),
                          options=dict(maxiter=maxiter, ftol=1e-10, gtol=1e-6, maxls=30))
        point = physical(result.x)
        score = float(logprob(point)[0])
        trials.append(dict(start_log_likelihood=float(scores[idx]),
                           log_likelihood=score if np.isfinite(score) else None,
                           success=bool(result.success), message=str(result.message),
                           iterations=int(result.nit), evaluations=int(result.nfev)))
        if np.isfinite(score) and (score, bool(result.success)) >= (best['logp'], best['converged']):
            best = dict(point=point, logp=score, converged=bool(result.success))
        if len(trials) >= starts:
            break
    details = dict(method='posterior screening + bounded L-BFGS-B', seed=seed,
                   scored_samples=len(indices), requested_starts=starts, maxiter=maxiter,
                   batch_size=batch_size, best_initial_log_likelihood=float(scores[order[0]]),
                   log_likelihood=best['logp'], optimizer_converged=best['converged'], trials=trials,
                   boundary_positions=np.flatnonzero(np.minimum((best['point']-lower)/width,
                                                                1-(best['point']-lower)/width) <= 1e-5).tolist())
    return best['point'], details


def map_grid(mean, std, labels, measured, sigmas, path, system, group):
    """A MAP curve and emulator-only one-sigma band, using the existing flow display convention."""
    prediction, shown_labels, data, errors = F.to_v_space(mean[None, :], labels, measured, sigmas)
    low, _, _, _ = F.to_v_space((mean-std)[None, :], labels, measured, sigmas)
    high, _, _, _ = F.to_v_space((mean+std)[None, :], labels, measured, sigmas)
    groups = defaultdict(list)
    for j, label in enumerate(shown_labels):
        name, edges = label.split('__')
        if F.group_of(name) == group:
            groups[name].append((.5*sum(map(float, edges.split('_'))), j))
    if not groups:
        return False
    names = sorted(groups, key=lambda name: name in ('v2_charged', 'v3_charged'))
    nc = min(2, len(names)); nr = int(np.ceil(len(names)/nc))
    with I.plt.rc_context():
        F.set_pub_style()
        fig, axes = I.plt.subplots(nr, nc, figsize=(6.5*nc, 4.6*nr), squeeze=False)
        try:
            for ax, name in zip(axes.flat, names):
                points = sorted(groups[name]); xs = np.array([x for x, _ in points]); cols = [j for _, j in points]
                band = ax.fill_between(xs, low[0, cols], high[0, cols], color=F.C_BLUE, alpha=.3)
                line, = ax.plot(xs, prediction[0, cols], '-s', color=F.C_MED, ms=4)
                data_artist = ax.errorbar(xs, data[cols], yerr=errors[cols], fmt='o', color=F.C_DATA, capsize=3)
                ax.set_xlabel(r'$\langle dN_{\rm ch}/d\eta\rangle$' if name.endswith('_vs_mult') else 'centrality [%]')
                ax.set_ylabel(I.OBS_LABELS.get(name, name)); ax.margins(x=.04)
                tag = F.EXPERIMENT_TAG.get(system, {}).get(name)
                if tag:
                    left = data[cols][-1] > data[cols][0]
                    ax.text(.04 if left else .96, .94, tag, transform=ax.transAxes,
                            ha='left' if left else 'right', va='top', color='.35', fontsize=12)
            for ax in list(axes.flat)[len(names):]:
                ax.axis('off')
            fig.suptitle(f'{system}: MAP estimate')
            fig.legend([line, band, data_artist], ['MAP', 'emulator ±1σ', F.DATA_LEGEND[system]],
                       loc='lower center', ncol=3, bbox_to_anchor=(.5, 0.015))
            fig.text(.5, .005, 'Data errors include the fit uncertainty floors. Flow is displayed in v-space.',
                     ha='center', fontsize=10)
            fig.tight_layout(rect=(0, .105, 1, .94))
            fig.savefig(path)
        finally:
            I.plt.close(fig)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='Saved single-system or joint run directory.')
    parser.add_argument('--system', choices=sorted(I.SYSTEMS), help='Select a single-system posterior; normally inferred from its filename.')
    parser.add_argument('--config', type=Path, help='Joint study with relocated identical inputs.')
    parser.add_argument('--matrix', help='Single-system relocated identical matrix.')
    parser.add_argument('--design', help='Single-system relocated identical design table.')
    parser.add_argument('--data-dir', help='Single-system relocated identical measurement directory.')
    parser.add_argument('--out-dir', type=Path, help='New output directory (default: RUN_DIR/map). Existing directories are refused.')
    parser.add_argument('--format', choices=('pdf', 'png', 'both'), default='both', help='Figure formats.')
    parser.add_argument('--max-samples', type=int, default=1024, help='Maximum saved draws scored to choose optimizer starts.')
    parser.add_argument('--starts', type=int, default=4, help='Number of local optimization starts.')
    parser.add_argument('--maxiter', type=int, default=400, help='Maximum iterations per optimizer start.')
    parser.add_argument('--batch-size', type=int, default=64, help='Maximum simultaneous emulator predictions during screening.')
    parser.add_argument('--seed', type=int, default=12345, help='Deterministic screening seed.')
    args = parser.parse_args(argv)
    try:
        validate_search(args.max_samples, args.starts, args.maxiter, args.seed, args.batch_size)
        output = (args.out_dir or args.run_dir/'map').expanduser().resolve()
        if output.exists():
            raise FileExistsError(f'Output directory already exists: {output}')
        fit = load_fit(args.run_dir, system=args.system, config=args.config,
                       matrix=args.matrix, design=args.design, data_dir=args.data_dir)
        print(f'Estimating {fit.kind} MAP in {len(fit.free_names)} physical parameters', flush=True)
        point, search = estimate_map(physical_logprob(fit), fit.samples, fit.free_ranges,
                                     max_samples=args.max_samples, starts=args.starts, maxiter=args.maxiter,
                                     batch_size=args.batch_size, seed=args.seed)
        if not search['optimizer_converged']:
            warnings.warn('Best MAP candidate did not meet optimizer convergence criteria; inspect map_result.json',
                          UserWarning, stacklevel=2)
        full = I.expand_free(point, fit.free_pos, fit.template)[0]
        record = dict(schema='map-observables/v1', fit_kind=fit.kind,
                      posterior=I.file_identity(fit.posterior), fit_config=fit.config,
                      implementation=I.file_identity(__file__), emulator_reconstruction=fit.reconstruction,
                      parameters=dict(zip(fit.pnames, full.tolist())), fixed_params=fit.fixed,
                      objective='physical-coordinate log likelihood + constant uniform-box prior; no logit Jacobian',
                      search=search, systems=[])
        output.mkdir(parents=True, exist_ok=False)
        (output/'map_result.json').write_text(json.dumps(record, indent=2, allow_nan=False)+'\n')
        for c in fit.systems:
            folder = output/c.system; folder.mkdir()
            prefix = I.system_config(c.system)['prefix']
            theta = full[c.projection]
            mean, std = c.emulator.predict(theta[None, :], return_std=True)
            score = float(I.gaussian_measurement_loglike(c.inputs.sigmas, c.inputs.covariance)(
                mean-c.inputs.measured, std)[0])
            np.savez_compressed(folder/f'{prefix}_MAP_observables.npz', mean=mean[0], std=std[0],
                                labels=np.array(c.inputs.labels), measured=c.inputs.measured,
                                sigmas=c.inputs.sigmas, param_names=np.array(c.inputs.pnames), parameters=theta)
            paths = []
            formats = ('pdf', 'png') if args.format == 'both' else (args.format,)
            for group in ('yields', 'ptflow'):
                for ext in formats:
                    path = folder/f'{prefix}_MAP_{group}.{ext}'
                    if map_grid(mean[0], std[0], c.inputs.labels, c.inputs.measured, c.inputs.sigmas,
                                path, c.system, group):
                        paths.append(str(path))
            record['systems'].append(dict(system=c.system, log_likelihood=score, n_data=len(c.inputs.labels),
                                          parameters=dict(zip(c.inputs.pnames, theta.tolist())), figures=paths))
        (output/'map_result.json').write_text(json.dumps(record, indent=2, allow_nan=False)+'\n')
        print(f'MAP log likelihood: {search["log_likelihood"]:.6g}; results: {output}')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, f'MAP plotting failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
