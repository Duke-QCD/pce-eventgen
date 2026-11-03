#!/usr/bin/env python3
"""Joint Bayesian calibration with independent system emulators and shared physics.

Each system retains its own training rows, observable adapter and normalization.
The likelihood is a product of system likelihoods, conditional on one shared
parameter vector. The box prior and logit Jacobian are applied exactly once.
"""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.linalg import block_diag

import inference as I

DEFAULT_CONFIG = I.ROOT / "joint_AuAu200_OO5360.json"
DEFAULT_DESIGN = Path(I.DESIGN_DAT)   # captured at import; CLI helpers reassign I.DESIGN_DAT


@dataclass(frozen=True)
class SystemStudy:
    system: str
    matrix: Path
    data_dir: Path
    drop_specs: tuple
    feeddown: str = ""


@dataclass(frozen=True)
class Study:
    design: Path
    shear_mode: str
    systems: tuple

    def as_dict(self):
        return dict(schema=1, design=str(self.design), shear_mode=self.shear_mode,
                    systems=[dict(system=s.system, matrix=str(s.matrix), data_dir=str(s.data_dir),
                                  drop_specs=list(s.drop_specs), feeddown=s.feeddown) for s in self.systems])


def _keys(value, allowed, where):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{where} must be an object with only these keys: {sorted(allowed)}")


def _path(value, base):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Input paths must be nonempty strings")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def parse_study(raw, base, shear_mode=None):
    """Resolve paths relative to the study file; reject ambiguous or unknown options."""
    _keys(raw, {"schema", "design", "shear_mode", "systems"}, "Study")
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        raise ValueError("Study requires schema = 1")
    mode = raw.get("shear_mode", "flat") if shear_mode is None else shear_mode
    I.shear_fixed_params(mode)
    entries = raw.get("systems")
    if not isinstance(entries, list) or len(entries) < 2:
        raise ValueError("A joint study requires at least two systems")
    systems = []
    for entry in entries:
        _keys(entry, {"system", "matrix", "data_dir", "drop_specs", "feeddown"}, "System entry")
        name = entry.get("system")
        if not isinstance(name, str):
            raise ValueError("Each system entry requires its system name")
        cfg = I.system_config(name)
        drops = entry.get("drop_specs", list(cfg["primary_drop"]))
        if (not isinstance(drops, list) or not all(isinstance(s, str) for s in drops)
                or len(set(drops)) != len(drops)):
            raise ValueError(f"{name}: drop_specs must be a list of unique observable names")
        feeddown = entry.get("feeddown", "")
        if not isinstance(feeddown, str):
            raise ValueError(f"{name}: feeddown must be an inventory path or an empty string")
        systems.append(SystemStudy(name, _path(entry.get("matrix", cfg["matrix"]), base),
                                   _path(entry.get("data_dir", cfg["data_dir"]), base), tuple(drops),
                                   str(_path(feeddown, base)) if feeddown else ""))
    if len({s.system for s in systems}) != len(systems):
        raise ValueError("Duplicate systems would count the same measurements twice")
    return Study(_path(raw.get("design", str(DEFAULT_DESIGN)), base), mode, tuple(systems))


def load_study(path=DEFAULT_CONFIG, shear_mode=None):
    path = Path(path).expanduser().resolve()
    return parse_study(json.loads(path.read_text()), path.parent, shear_mode)


@dataclass
class Component:
    spec: SystemStudy
    inputs: object
    provenance: dict
    projection: list


@dataclass
class JointInputs:
    study: Study
    components: list
    pnames: list
    ranges: np.ndarray
    fixed_params: dict
    free_pos: list
    free_names: list
    free_ranges: np.ndarray
    template: np.ndarray
    measured: np.ndarray
    sigmas: np.ndarray
    covariance: object
    labels: list


def prepare_study(study):
    """Prepare independent system contexts without installing module-global settings."""
    if len(study.systems) < 2 or len({s.system for s in study.systems}) != len(study.systems):
        raise ValueError("A joint study requires at least two distinct systems")
    fixed = I.shear_fixed_params(study.shear_mode)
    components = []
    for spec in study.systems:
        kwargs = dict(system=spec.system, fixed_params=fixed)
        try:
            inputs = I.prepare_inputs(spec.matrix, study.design, spec.data_dir, spec.drop_specs,
                                      spec.feeddown, **kwargs)
            provenance = I.input_config(spec.matrix, study.design, spec.data_dir, spec.drop_specs,
                                        spec.feeddown, **kwargs)
        except (OSError, ValueError, KeyError, IndexError) as error:
            raise ValueError(f"{spec.system}: {error}") from error
        provenance.update(usable_design_indices=inputs.indices.tolist(), labels=inputs.labels,
                          data_spec=I.system_config(spec.system)["data_spec"],
                          raw_shape=list(inputs.raw_shape))
        components.append(Component(spec, inputs, provenance, []))
    active = {p for component in components for p in component.inputs.pnames}
    pnames = [p for p, _, _ in I.PARAMS_ALL if p in active]
    ranges = np.array([[lo, hi] for p, lo, hi in I.PARAMS_ALL if p in active])
    for component in components:
        component.projection = [pnames.index(p) for p in component.inputs.pnames]
        if not np.array_equal(ranges[component.projection], component.inputs.ranges):
            raise ValueError(f"{component.spec.system}: inconsistent shared parameter priors")
    free_pos, free_names, free_ranges, template = I.resolve_free(pnames, ranges, fixed_params=fixed)
    measured = np.concatenate([c.inputs.measured for c in components])
    sigmas = np.concatenate([c.inputs.sigmas for c in components])
    covariance = None
    if any(c.inputs.covariance is not None for c in components):
        covariance = block_diag(*[c.inputs.covariance if c.inputs.covariance is not None
                                  else np.diag(c.inputs.sigmas**2) for c in components])
    I.gaussian_measurement_loglike(sigmas, covariance)  # Validate covariance before any writes.
    labels = [f"{c.spec.system}::{label}" for c in components for label in c.inputs.labels]
    return JointInputs(study, components, pnames, ranges, fixed, free_pos, free_names,
                       free_ranges, template, measured, sigmas, covariance, labels)


class JointEmulator:
    """Project one full joint vector into each trained emulator and concatenate outputs."""
    def __init__(self, problem, emulators):
        if len(problem.components) != len(emulators):
            raise ValueError("One emulator is required for each system")
        self.problem, self.emulators = problem, tuple(emulators)

    def predict(self, X, return_std=False):
        X = np.atleast_2d(np.asarray(X, float))
        if X.shape[1] != len(self.problem.pnames):
            raise ValueError("Joint emulator requires the full joint parameter vector")
        outputs = [emu.predict(X[:, c.projection], return_std=return_std)
                   for c, emu in zip(self.problem.components, self.emulators)]
        if return_std:
            return tuple(np.concatenate([out[i] for out in outputs], axis=1) for i in (0, 1))
        return np.concatenate(outputs, axis=1)


def emulator_settings():
    return I.emulator_settings()


def study_record(problem):
    return dict(schema="joint-inference/v1", study=problem.study.as_dict(),
                parameter_names=problem.pnames, ranges=problem.ranges.tolist(),
                fixed_params=problem.fixed_params, free_names=problem.free_names,
                normalizations={c.spec.system: I.system_config(c.spec.system)["norm"] for c in problem.components},
                system_inputs=[dict(c.provenance, projection=c.projection) for c in problem.components],
                cross_system_covariance="zero; systems conditionally independent",
                emulator_uncertainty="diagonal predictive variance; per-system closure calibration",
                emulator_settings=emulator_settings(),
                joint_implementation=I.file_identity(__file__))


def report_inputs(problem):
    print(f"Joint calibration: {' + '.join(c.spec.system for c in problem.components)}")
    print(f"Shear mode: {problem.study.shear_mode}; {len(problem.free_names)} sampled parameters")
    print(f"Shared physics: {', '.join(p for p in problem.pnames if p not in I.NORM_PARAMS)}")
    print(f"Fixed parameters: {problem.fixed_params or 'none'}")
    for c in problem.components:
        print(f"  {c.spec.system}: {len(c.inputs.X)} usable designs, {len(c.inputs.labels)} data points; "
              f"profile {c.provenance['profile']}; normalization {I.system_config(c.spec.system)['norm']}")
        print(f"    matrix: {c.spec.matrix}\n    exclusions: {', '.join(c.spec.drop_specs) or 'none'}")
    print(f"Total data points: {len(problem.labels)}; each system retains its own design rows")


def _validate_plots(npost, nprior, font_scale):
    if (any(not isinstance(v, (int, np.integer)) or v < 1 for v in (npost, nprior))
            or not np.isfinite(font_scale) or font_scale <= 0):
        raise ValueError("Plot draw counts must be positive integers; font scale must be finite and positive")


def plot_results(problem, emulators, samples, out_dir, *, npost=I.PLOT_NPOST,
                 nprior=I.PLOT_NPRIOR, font_scale=I.CORNER_FONT_SCALE):
    """Use the same joint posterior/prior draws for each system's predictive figures."""
    import make_flow_plots as F
    import regen_corners as C
    _validate_plots(npost, nprior, font_scale)
    output = Path(out_dir)
    rng = np.random.default_rng(0)
    selected = rng.choice(len(samples), min(npost, len(samples)), replace=False)
    full_post = I.expand_free(samples[selected], problem.free_pos, problem.template)
    prior = rng.uniform(problem.free_ranges[:, 0], problem.free_ranges[:, 1],
                        size=(nprior, len(problem.free_names)))
    full_prior = I.expand_free(prior, problem.free_pos, problem.template)
    for c, emu in zip(problem.components, emulators):
        folder = output / c.spec.system
        folder.mkdir(parents=True, exist_ok=True)
        prefix = folder / I.system_config(c.spec.system)["prefix"]
        post = emu.predict(full_post[:, c.projection])
        pri = emu.predict(full_prior[:, c.projection])
        np.savez_compressed(f"{prefix}_predictive.npz", posterior_mean=post, prior_mean=pri,
                            posterior_sample_indices=selected, labels=np.array(c.inputs.labels),
                            measured=c.inputs.measured, sigmas=c.inputs.sigmas)
        # Bands describe variation of emulator means over parameter draws.
        with I.plt.rc_context():
            F.set_pub_style()
            for predictions, kind in ((post, "posterior"), (pri, "prior")):
                pred, labels, measured, sigmas = F.to_v_space(
                    predictions, c.inputs.labels, c.inputs.measured, c.inputs.sigmas)
                for group in ("yields", "ptflow"):
                    F.summary_grid(pred, labels, measured, sigmas,
                                   f"{prefix}_{kind.capitalize()}Summary_{group}.pdf", kind, group,
                                   system=c.spec.system, obs_labels=I.OBS_LABELS)
    engine = SimpleNamespace(**{name: getattr(I, name) for name in (
        "PARAM_LABELS", "POSTERIOR_RC", "PLOT_THIN_N", "corner_full_scale", "make_corner_plots")},
        FIXED_PARAMS=problem.fixed_params)
    C.plot_from_samples(samples, problem.free_names, problem.free_ranges, str(output / "joint"),
                        font_scale=font_scale, engine=engine)


def run(problem, out_dir, *, nsteps=I.NSTEPS, nburn=I.NBURN, nwalkers=I.NWALKERS,
        nsplits=8, seed=I.SEED, emulator_only=False, make_plots=True,
        plot_npost=I.PLOT_NPOST, plot_nprior=I.PLOT_NPRIOR, corner_font_scale=I.CORNER_FONT_SCALE):
    """Fit a prepared study into a new directory, preserving both systems' provenance."""
    for c in problem.components:
        I.validate_run_sizes(nsteps, nburn, nwalkers, nsplits, len(c.inputs.X))
    _validate_plots(plot_npost, plot_nprior, corner_font_scale)
    record = study_record(problem)
    record.update(nsteps=nsteps, nburn=nburn, nwalkers=nwalkers, nsplits=nsplits, seed=seed,
                  emulator_only=emulator_only, sampler=dict(a=I.SAMPLER_A, p_de=I.SAMPLER_P_DE),
                  emulator_variance_inflation={},
                  plotting=dict(enabled=make_plots, npost=plot_npost, nprior=plot_nprior,
                                corner_font_scale=corner_font_scale, seed=0))
    output = Path(out_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_path = output / "joint_run.json"
    run_path.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    emulators = []
    for c in problem.components:
        print(f"== {c.spec.system}: closure and emulator training ==", flush=True)
        folder = output / c.spec.system
        folder.mkdir()
        prefix = folder / I.system_config(c.spec.system)["prefix"]
        emu, validation = I.fit_emulator(c.inputs, prefix, n_splits=nsplits, make_plots=make_plots)
        rows = validation.pop("rows")
        np.savez_compressed(folder / "emulator_validation.npz", **validation,
                            labels=np.array(c.inputs.labels), design_index=c.inputs.indices[rows])
        record["emulator_variance_inflation"][c.spec.system] = emu.var_inflation.tolist()
        run_path.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
        emulators.append(emu)
    if emulator_only:
        print(f"Joint emulator validation complete: {output}")
        return output
    print(f"== sample joint posterior ({len(problem.free_names)} parameters) ==", flush=True)
    emulator = JointEmulator(problem, emulators)
    rng = np.random.default_rng(seed)
    lo, hi = problem.free_ranges[:, 0], problem.free_ranges[:, 1]
    th0 = rng.uniform(lo + .25*(hi-lo), hi - .25*(hi-lo), size=(nwalkers, len(lo)))
    u0 = np.log((th0-lo)/(hi-th0))
    logprob = I.make_logprob_u(emulator, problem.measured, problem.sigmas, problem.free_ranges,
                               problem.free_pos, problem.template, cov_data=problem.covariance)
    chain_u = I.ensemble_sample(logprob, u0, nburn+nsteps, a=I.SAMPLER_A, p_de=I.SAMPLER_P_DE, rng=rng)
    chain = I.theta_from_u(chain_u, problem.free_ranges)
    del chain_u
    samples = chain[nburn:].reshape(-1, len(lo))
    np.savez_compressed(output / "joint_posterior.npz", samples=samples,
                        param_names=np.array(problem.free_names), ranges=problem.free_ranges,
                        fixed_names=np.array(list(problem.fixed_params), dtype=str),
                        fixed_values=np.array(list(problem.fixed_params.values()), dtype=float),
                        config_json=np.array(I.config_json(record)))
    diagnostics = I.mcmc_diagnostics(chain[nburn:], problem.free_names)
    (output / "joint_diagnostics.json").write_text(json.dumps(diagnostics, indent=2, allow_nan=False) + "\n")
    if make_plots:
        I.trace_plot(chain, problem.free_names, str(output / "joint_MCMCSamples.pdf"), nburn,
                     labels=I.param_labels(problem.fixed_params))
    del chain
    if make_plots:
        plot_results(problem, emulators, samples, output, npost=plot_npost, nprior=plot_nprior,
                      font_scale=corner_font_scale)
    print(f"Joint results: {output}")
    return output


def _physical_inputs(record):
    return [dict(system=c["system"], profile=c["profile"], drop_specs=c["drop_specs"],
                 data_spec=c["data_spec"], labels=c["labels"],
                 indices=c["usable_design_indices"], projection=c["projection"],
                 files=I.physical_input_files(c))
            for c in record["system_inputs"]]


def restore(run_dir, study=None):
    """Verify saved inputs and parameter policy before reconstructing a joint posterior."""
    result = I.read_npz(Path(run_dir) / "joint_posterior.npz")
    record = json.loads(str(result["config_json"]))
    if record.get("schema") != "joint-inference/v1":
        raise ValueError("Unsupported joint posterior schema")
    study = parse_study(record["study"], Path("/")) if study is None else study
    problem = prepare_study(study)
    current = study_record(problem)
    for key in ("parameter_names", "ranges", "fixed_params", "free_names", "cross_system_covariance"):
        if current[key] != record[key]:
            raise ValueError(f"Plot inputs differ from saved joint fit: {key}")
    if _physical_inputs(current) != _physical_inputs(record):
        raise ValueError("Plot inputs differ from saved joint fit: input contents or observable selection")
    fixed_names, fixed_values = result["fixed_names"].astype(str), result["fixed_values"]
    if (fixed_names.ndim != 1 or fixed_values.shape != fixed_names.shape
            or len(set(fixed_names)) != len(fixed_names)):
        raise ValueError("Invalid fixed-parameter metadata in joint posterior")
    fixed = dict(zip(fixed_names, fixed_values.tolist()))
    if (result["param_names"].astype(str).tolist() != problem.free_names
            or not np.array_equal(result["ranges"], problem.free_ranges) or fixed != problem.fixed_params):
        raise ValueError("Posterior parameter metadata differs from saved joint policy")
    samples = result["samples"]
    if (samples.ndim != 2 or not len(samples) or samples.shape[1] != len(problem.free_names)
            or not np.isfinite(samples).all()
            or np.any(samples < problem.free_ranges[:, 0]) or np.any(samples > problem.free_ranges[:, 1])):
        raise ValueError("Invalid joint posterior samples")
    return problem, samples, record


def restore_emulators(problem, record):
    """Rebuild likelihood-ready emulators from saved settings and uncertainty calibration."""
    emulators = []
    for c in problem.components:
        inflation = np.asarray(record["emulator_variance_inflation"][c.spec.system], float)
        if inflation.shape != (len(c.inputs.labels),) or not np.all(np.isfinite(inflation) & (inflation >= 1)):
            raise ValueError(f"Invalid saved emulator calibration for {c.spec.system}")
        emulators.append(I.PCAGPEmulator(c.inputs.X, c.inputs.Y, var_inflation=inflation,
                                         **record["emulator_settings"]))
    return emulators


def replay(run_dir, study=None, *, plot_npost=I.PLOT_NPOST, plot_nprior=I.PLOT_NPRIOR,
           corner_font_scale=I.CORNER_FONT_SCALE):
    """Retrain with saved GP settings/inflation and plot without resampling or closure."""
    _validate_plots(plot_npost, plot_nprior, corner_font_scale)
    problem, samples, record = restore(run_dir, study)
    emulators = restore_emulators(problem, record)
    plot_results(problem, emulators, samples, run_dir, npost=plot_npost, nprior=plot_nprior,
                  font_scale=corner_font_scale)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Study JSON (default: joint_AuAu200_OO5360.json beside this script).")
    parser.add_argument("--shear-mode", choices=I.SHEAR_MODES,
                        help="Override the study's viscosity choice; example study defaults to flat. "
                             "Not accepted with --plot-run, which uses the saved policy.")
    parser.add_argument("--out-dir", type=Path, help="New output directory; existing directories are refused.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-inputs", action="store_true", help="Validate both systems and parameter mapping without training or writes.")
    modes.add_argument("--emulator-only", action="store_true", help="Train and validate each emulator without sampling.")
    modes.add_argument("--plot-run", type=Path, help="Verify a saved joint fit and regenerate figures; no sampling.")
    parser.add_argument("--nsteps", type=int, default=I.NSTEPS, help="Post-burn steps per walker.")
    parser.add_argument("--nburn", type=int, default=I.NBURN, help="Burn-in steps per walker.")
    parser.add_argument("--nwalkers", type=int, default=I.NWALKERS, help="Even number of walkers, at least four.")
    parser.add_argument("--nsplits", type=int, default=8, help="Closure folds per system.")
    parser.add_argument("--seed", type=int, default=I.SEED, help="Sampler seed; closure uses the production seed.")
    I.add_plot_arguments(parser)
    args = parser.parse_args(argv)
    try:
        if args.plot_run is not None:
            if args.out_dir is not None or args.no_plots or args.shear_mode is not None:
                raise ValueError("--plot-run uses the saved directory and viscosity policy; it cannot be "
                                 "combined with --out-dir, --no-plots or --shear-mode")
            study = load_study(args.config) if args.config is not None else None
            replay(args.plot_run, study, plot_npost=args.plot_npost, plot_nprior=args.plot_nprior,
                   corner_font_scale=args.corner_font_scale)
            return 0
        study = load_study(args.config or DEFAULT_CONFIG, args.shear_mode)
        problem = prepare_study(study)
        report_inputs(problem)
        if args.check_inputs:
            print("PASS: joint inputs; no training, sampling or output writes")
            return 0
        output = args.out_dir or I.ROOT / f"plots_joint_{'_'.join(s.system for s in study.systems)}_{study.shear_mode}"
        run(problem, output, nsteps=args.nsteps, nburn=args.nburn, nwalkers=args.nwalkers,
            nsplits=args.nsplits, seed=args.seed, emulator_only=args.emulator_only,
            make_plots=not args.no_plots, plot_npost=args.plot_npost, plot_nprior=args.plot_nprior,
            corner_font_scale=args.corner_font_scale)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        parser.exit(1, f"Joint inference failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
