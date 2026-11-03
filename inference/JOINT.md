# Joint Au–Au and O–O calibration

`joint_inference.py` combines system-specific PCA/GP emulators into one posterior. Use the [inference environment](README.md#environment-and-paths), with Python 3.11 and the same dependencies as the single-system fitter. The commands below run from this `inference/` folder.

## First trial

The supplied [study configuration](joint_AuAu200_OO5360.json) selects AuAu200 and OO5360, their primary observable exclusions, no feed-down, and flat shear viscosity. It reads the staged matrix/metadata pairs in `model/` and each adapter's measurement directory. These generated matrices and O-O measurements are local inputs, excluded from source publication; a fresh checkout needs matching inputs staged as described in the [input guide](README.md#stage-the-selected-analysis-inputs).

```bash
python joint_inference.py --check-inputs

python -u joint_inference.py \
  --out-dir plots_joint_auau_oo_flat
```

Preflight assembles each system, verifies its matrix/profile and O-O flow-fallback metadata, hashes the inputs, and prints retained-design and matched-data counts. It performs no emulator training or output writes. Fits require a new output directory and refuse an existing directory, including an empty one.

Both systems share `w`, `k`, the four chemistry parameters, the three shear parameters, and the four bulk parameters. `norm200` belongs to AuAu200 and `norm5360` belongs to OO5360; `norm2760` and `norm5020` are absent. The Au-Au fixed-parameter slice is not inherited.

| Shear choice | Sampled joint parameters | Treatment |
|---|---:|---|
| `flat` (study default) | 13 | `eta_low = eta_high = 0`; `eta_min` is constant eta/s |
| `temperature-dependent` | 15 | Both slopes and `eta_min` sampled |

Override the study's viscosity choice for a separate fit:

```bash
python joint_inference.py --shear-mode temperature-dependent \
  --out-dir plots_joint_auau_oo_temperature
```

Both modes train each emulator on its full 14-dimensional design, retaining the variation of the shear slopes. Flat inference evaluates the emulator at zero slopes. Each matrix joins its own zero-based `design_index` to the single original full design table. Training sets can have different sizes, missing rows, different row orders, and even no design IDs in common. No intersection of the two sets is required.

## Likelihood and scientific assumptions

Each joint parameter draw projects into each system's emulator inputs by parameter name. The two likelihood contributions are added, with one shared box prior and one logit Jacobian. Observable labels are qualified by system so matching names do not merge data points.

The first trial assumes conditional independence between systems: cross-system experimental, emulator, and discrepancy covariance is zero. Within a system, the existing measurement-covariance handling and diagonal emulator predictive variance are retained. Each emulator has its own PCA, GP fit, cross-validation and per-observable variance calibration. Matrices' `Y_err` values are checked when a matrix loads and are propagated through observable assembly, but the likelihood does not use them: per-design model noise enters through each GP's fitted white-noise term and therefore through the emulator predictive variance.

Current primary selections exclude Au-Au Lambda/proton yields and proton mean pT, and O-O K0S mean pT. Existing floors, bin/multiplicity-coverage cuts and data uncertainty assumptions remain in effect. In particular, some supplied O-O strange-yield uncertainties are assumed or partly assumed. A compatible input set and passing software tests do not validate those scientific assumptions. Preserve the source campaign's model conventions and any documented decisions about pooling designs.

## Study configuration

Copy the example JSON to select other matrix/data paths or observable exclusions. Relative input paths resolve against the JSON file's directory; explicit output paths resolve against the launch directory. Unknown keys, repeated systems and malformed options are rejected. The original shared design table must be the one that generated both matrices.

```json
{
  "schema": 1,
  "design": "/absolute/path/to/original/design.dat",
  "shear_mode": "flat",
  "systems": [
    {
      "system": "AuAu200",
      "matrix": "/absolute/path/to/auau_matrix.npz",
      "data_dir": "/absolute/path/to/auau/STAR",
      "drop_specs": ["dN_dy_Lambda", "dN_dy_proton", "mean_pT_proton"],
      "feeddown": ""
    },
    {
      "system": "OO5360",
      "matrix": "/absolute/path/to/oo_matrix.npz",
      "data_dir": "/absolute/path/to/oo/centrality",
      "drop_specs": ["mean_pT_K0S_vs_mult"],
      "feeddown": ""
    }
  ]
}
```

Only `system` is required in each system entry. Omitted matrix/data paths and exclusions use the adapter's defaults. An empty `drop_specs` list keeps all primary-adapter groups. Feed-down is disabled by default; enabling it requires an explicit inventory path with matching metadata. Optional O-O light-hadron yield proxies are not enabled by this study interface.

```bash
python joint_inference.py --config /path/to/study.json --check-inputs
```

## Validation and execution checks

The default fit uses eight closure folds per system, 128 walkers, 5,000 burn-in steps and 40,000 post-burn steps. The MCMC seed defaults to 12345; closure retains the single-system production seed. Use `--emulator-only` to validate/train both systems without sampling. `--no-plots` suppresses all figures while preserving closure arrays, settings, posterior and convergence diagnostics.

For a short software execution check on an appropriately small synthetic grid:

```bash
python joint_inference.py --config /path/to/synthetic/study.json \
  --out-dir plots_joint_execution_check \
  --nsplits 2 --nwalkers 8 --nburn 2 --nsteps 8 --no-plots
```

Short sampler settings still train on every retained design and do not establish convergence. The portable tests in `tests/test_joint_inference.py` construct synthetic studies with different, unsorted, disjoint design IDs and exercise fitting in both viscosity modes, normalization isolation, likelihood composition, covariance, guards, and predictive replay.

## Saved results and plotting

| Output | Contents |
|---|---|
| `joint_run.json` | Resolved study, parameter policy/projections, per-system input hashes and selected design IDs/labels, GP settings, variance inflation and sampler settings |
| `joint_posterior.npz` | Samples, sampled names/ranges, fixed values and embedded run provenance |
| `joint_diagnostics.json` | Shared rank-Rhat, bulk ESS, tail ESS and convergence verdict |
| `joint_MCMCSamples.pdf`, `joint_posterior_*.pdf` | Joint trace and parameter corners, when plots are enabled |
| `<system>/emulator_validation.npz` | Held-out truth/predictions/standard deviations, corresponding design IDs and variance inflation |
| `<system>/*_closure.pdf`, `*_PC_spectrum.pdf` | Per-system emulator checks, when plots are enabled |
| `<system>/*Summary_{yields,ptflow}.pdf` | Each system's prior/posterior observable comparisons from the joint fit |
| `<system>/*_predictive.npz` | Prior/posterior emulator means, selected joint posterior sample indices, measurements and labels |

Both systems' prediction summaries use the same joint posterior and prior draws. Flow is fitted as signed cumulants and displayed as roots of clipped nonnegative cumulants, following the existing plotting convention. Bands show variation of emulator means over parameter draws and do not include emulator uncertainty draws.

Inspect `joint_diagnostics.json` before interpreting constraints; successful execution is separate from convergence. The existing thresholds apply: rank-Rhat above 1.05 or tail ESS below 400 fails the convergence screen, with a softer warning above Rhat 1.01. There is no automatic MCMC checkpoint/resume.

Regenerate predictions and corners from a fit, including one made with `--no-plots`:

```bash
python joint_inference.py --plot-run plots_joint_auau_oo_flat
```

For a MAP comparison instead of posterior bands, run `python plot_map_observables.py --run-dir plots_joint_auau_oo_flat`. The script reconstructs both calibrated emulators, finds one joint MAP in physical coordinates, and projects that vector into both systems. It saves the MAP parameters, optimizer diagnostics, signed predictions and separate figure sets under a new `map/` directory. See the [MAP plotting guide](PRODUCTION.md#map-observable-plots) for search controls and uncertainty interpretation.

Replay verifies saved input contents, profiles, observable selection, design IDs, parameter projections and fixed values. It retrains each emulator with the recorded GP settings and variance inflation, without repeating closure or MCMC. Fitted emulator objects are not serialized. A `--config` pointing to relocated identical inputs is accepted; changed inputs or a changed viscosity policy are rejected. `--shear-mode` is not accepted with `--plot-run`: replay always uses the saved policy. Plotting preserves the saved posterior and writes figures/prediction arrays into its directory.
