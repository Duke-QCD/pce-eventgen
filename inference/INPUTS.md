# Inference input contract

[Inference guide](README.md) · [Design generation](DESIGN.md) · [Measurements](data/README.md)

Inference needs an observable matrix, its same-stem JSON metadata, the exact design table used for those simulations, and measurements for the selected system adapter. These are file interfaces; no event-generation source, scheduler, campaign INI, or repository-level utility is required. Use the [synthetic walkthrough](GETTING_STARTED.md) to exercise the complete interface without a completed physics campaign.

## Design table

The bundled [500-row design](model/design_params_combined500.dat) is the default for both single-system inference and the example joint configuration. [`design_parameters.py`](design_parameters.py) supplies its 17 column names, order and prior bounds. Each system uses its own normalization column and the 13 shared physics columns, dropping the other three normalizations. The [design guide](DESIGN.md) describes generating a new table and preserving row identities through subsets.

Use `--design` for a different campaign. A commented `# Parameter ...` header must list all 17 names in the shared order; generated tables use this format. Legacy headerless numeric tables are accepted by inference in that same order. Rows must have finite values. The inference priors come from `design_parameters.py`, not from a table's observed extrema or its metadata sidecar.

The row identity is zero-based: row 0 means `design_000`. Never substitute a reordered or reduced table for the original campaign table after simulation. A matrix's row position need not equal its design ID.

## Observable matrix and metadata

For a matrix named `campaign.npz`, supply `campaign.meta.json` in the same directory. Default staging paths are described in [model/README.md](model/README.md). The NPZ contains these arrays, readable with `np.load(path, allow_pickle=False)`:

| Key | Shape | Meaning |
|---|---|---|
| `Y` | `(n_designs, n_features)` | Model observables |
| `Y_err` | Same as `Y` | Statistical standard errors, in the same units as `Y` |
| `feature_labels` | `(n_features,)` | Unique species/bin/observable labels |
| `design_index` | `(n_designs,)` | Unique integer row IDs into the original design table |
| `design_name` | `(n_designs,)` | Corresponding names such as `design_027` |

`Y` must be nonempty and two-dimensional, with matching array dimensions. Infinite values and negative standard errors are rejected. NaNs in unselected features can remain; observable assembly excludes designs with nonfinite matched observables/errors. Inspect the checker’s retained/excluded counts. Design indices may be gapped or unsorted, but must be within the original table. Align every row-dependent array by `design_index`, including an optional feed-down inventory.

Metadata must identify the selected `system`. `analysis_profile` defaults to `baseline` when omitted:

| System | Required `analysis_profile` | Extra requirement |
|---|---|---|
| `AuAu200` | `baseline` | Matching observable definitions and acceptance |
| `OO5360` | `oo_cross_full_pt027_flat/v3` | Charged-flow sample-usage metadata for fitted flow bins |

Preserve the producer's full metadata, including design failures/counts, bin edges, acceptance, centrality estimator, event/oversample weighting, error method and source identity. These fields support scientific review even when the loader does not validate all of them. Renaming a matrix does not change its profile or definitions.

For O-O fitted flow, `charged_flow_sample_usage` must contain one entry per matrix `design_index`. Each entry contains `design_index`, `design_name`, and `bins`; each bin records `cent_low`, `cent_high`, `same_full_pair_weight_fraction`, and `same_full_fallback_events`. The default accepted same-sample fallback pair-weight fraction is zero in model bins within fitted flow bins. Missing coverage, missing usage metadata, a null pair-weight fraction in a checked bin, or an excessive fallback fraction fails preflight. Keep the complete producer metadata rather than constructing a minimal approximation.

## Feature labels and matching

Labels have the form `{species}__{cent_low}_{cent_high}__{observable}`. Examples include `pi_tot__20_30__dN_dy`, `charged__0_5__c3_gap`, and `global_ET__0_5__dET_total_visible_deta`. Centrality bounds are percentages; integer and decimal spellings describe the same numeric interval.

The system's `DATA_SPEC` in [inference.py](inference.py) defines required species, observables, files and cuts. There is no universal fixed matrix width. Keep auxiliary columns needed for bin weighting: mean-pT matching requires the corresponding yields; charged-flow matching requires charged multiplicity. Charge-combined yields must use the adapter's combined channels, such as `pi_tot`, `K_tot`, `Lambda_tot`, `Xi_tot` and `Omega_tot`.

Where a measurement bin spans several complete model bins, yields use bin-width weights, mean pT uses width-times-yield weights, and flow uses width-times-charged-multiplicity-squared weights in cumulant space. Incomplete coverage is not silently extrapolated. Multiplicity-space O-O observables are interpolated only over the multiplicity range covered by every retained design; inspect skipped data points.

The baseline charged `c2_gap`/`c3_gap` labels mean signed two-subevent cumulants. The O-O v3 profile reuses those labels for cross-full cumulants, with a same-sample fallback when only one oversample is available; its charged mean pT is also adjusted for the profile's 0.27–5 GeV selection. This is why profile metadata is required even when labels match. Stored root observables and identified differential-flow estimators are not interchangeable with the charged cumulants used in the likelihood.

Measured flow is supplied as `v_n{2}` and mapped to `c = v**2`, with a delta-method uncertainty `sigma_c = 2*v*sigma_v` after any configured floor. The fit retains finite negative model cumulants. Displaying `sqrt(max(c, 0))` changes the plotting convention, not the calibrated observable or acceptance.

## Errors, covariance and measurements

`Y_err` holds standard errors, not variances or a covariance matrix. Model uncertainty in the current likelihood is emulator predictive variance; the GP learns model noise through its fitted white-noise term. `Y_err` is validated and carried through assembly, but is not passed as per-design GP observation noise. Experimental errors and configured discrepancy floors enter separately. Joint inference adds system likelihoods and assumes zero cross-system covariance; an enabled within-system measurement covariance is retained.

The hydro event is the statistical unit of simulation. Multiple samples from one surface share initial geometry and do not count as independent collisions. Preserve the producer's event weighting, centrality convention and uncertainty method. Diagonal errors do not supply cross-observable or cross-centrality covariance.

Measurement layouts and adapter choices are documented in [data/README.md](data/README.md) and the [O-O schema](data/OO5360.md). Centrality tables use `cent_low cent_high cent_mid value error`; O-O multiplicity-space tables use `dNch_deta value error`. Only selected groups require files. `--data-dir` changes file locations; it does not infer new observable definitions from arbitrary files. An optional proxy covariance needs its reviewed source/evidence bundle and explicit `--oo-yield-proxies` selection.

## Optional feed-down inventory

Feed-down is off by default. `--feeddown` selects the system's default inventory; `--feeddown PATH` selects another. Supply an NPZ with the same five array keys as the observable matrix and matching system metadata. Its labels are `{pidTOKEN}__{low}_{high}__N_eta{eta_max}`: for example, `pidpm3212__0_5__N_eta2` combines Sigma0 and anti-Sigma0, while `pidpm3122__0_5__N_eta2` combines Lambda and anti-Lambda. A `pid` token without `pm` denotes a single PID.

The AuAu correction multiplies the Lambda yield by `1 + Sigma0/Lambda` from an aligned inventory. It requires coverage of every selected design. These inventory quantities are equal-event mean multiplicities in a pseudorapidity window, not midrapidity identified `dN/dy`. Check acceptance and PID survival in the simulation before interpreting the correction. A wider inventory cannot be sliced into a midrapidity yield; forming a ratio does not establish a valid experimental feed-down convention. The shared-event parent/product covariance is not available from separately reduced errors.

## Preflight and preservation

Run `python check_inputs.py --matrix PATH --design PATH --data-dir PATH` from this folder, selecting the system and the same parameter/observable options intended for fitting. This uses the fitter's checks without emulator training or sampling. A pass establishes software compatibility; it does not establish experimental validity, sufficient design coverage or posterior convergence.

Saved fits record absolute input paths, content hashes, selected design IDs, parameter policy and library versions. Preserve the exact matrix, metadata, full design, measurements, optional covariance/inventory, source revision and environment. Saved predictive replay verifies recorded content and retrains an emulator. Use overrides for relocated identical inputs; never edit saved hashes to reuse changed data. Per-event simulation artifacts are not needed by inference, but the matrix cannot reconstruct them for reaggregation.
