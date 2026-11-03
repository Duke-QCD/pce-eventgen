# Create a parameter design

[Inference guide](README.md) · [Input contract](INPUTS.md) · [Joint calibration](JOINT.md)

The tools in this folder generate a named parameter table, select a space-filling first batch, and inspect its coverage. They are cleaned-up adaptations of the calibration workspace's `make_design.py`, `design_subset.py`, `design_subset_remainder.py`, and `inspect_design.py`. They need NumPy and SciPy from the [inference environment](README.md#environment-and-paths); STAT, R and pandas are unnecessary. Commands below run from this `inference/` folder with that environment active, including when it is copied out of the surrounding repository. Every output path must be new; tables and their metadata sidecars are never overwritten.

## Generate a full table

Choose a study directory outside the checkout, then generate and inspect a design:

```bash
STUDY=/absolute/path/to/new-study
python make_design.py --ndesign 500 --seed 12345 --candidates 20 \
  --out "$STUDY/design_params.dat"
python inspect_design.py --design "$STUDY/design_params.dat"
```

The output has 17 columns in the order required by event generation and inference, with a `# Parameter ...` header. Each column has one sample in each of its equal-probability Latin-hypercube strata. The generator draws independent randomized SciPy Latin hypercubes and keeps the candidate with the greatest minimum pairwise Euclidean distance in unit-prior coordinates. `--candidates 1` disables this best-of-candidates search. This is a heuristic, not a global maximin optimizer. It replaces the old STAT helper's call to R `lhs::maximinLHS`; the same seed does **not** reproduce historical campaign points. The bundled `design_params_combined500.dat` is left intact.

`design_params.meta.json` records the seed, candidate count, sampler, package versions, prior bounds and table SHA-256. Retain it with the table. A seed is reproducible within the recorded software environment; preserve the actual table rather than depending on future package versions to regenerate identical numbers.

[`design_parameters.py`](design_parameters.py) defines parameter order and prior bounds for both generation and inference. In particular, the normalization ranges match the bundled campaign: `norm5360 = [25,60]`, `norm5020 = [20,50]`, `norm2760 = [15,40]`, and `norm200 = [5,20]`. The old generator's narrower normalization ranges and commented alternatives are removed. The schema is deliberately fixed; arbitrary headers and bounds are rejected by the design tools. A future change to this file changes inference priors too, so keep the code revision with each study's inputs and do not mix old campaigns with changed parameter policies.

## Prioritize a smaller first batch

The subset helper uses repeated farthest-first selection, retaining the subset with the largest minimum separation. Distances are normalized by the full prior bounds, including all four normalizations. It is a generic shared-system design metric; the helper does not prioritize one collision system's active columns. A subset is space-filling but does not generally retain Latin stratification. `--n-starts` controls how many distinct starting rows are tried, capped at the source row count.

```bash
python design_subset.py --design "$STUDY/design_params.dat" \
  --n-select 100 --n-starts 100 --seed 12345 \
  --out "$STUDY/design_subset100.dat" \
  --remainder-out "$STUDY/design_remaining400.dat" \
  --ordered-out "$STUDY/design_campaign500.dat"
```

The selected table contains the chosen rows in selection order. The remainder contains all other rows in their original order. The ordered full table concatenates those two, so rows 0–99 form the first batch and rows 100–499 the continuation. You can omit either optional output; the separate hard-coded remainder script is folded into this command. Each output has a `.meta.json` sidecar with the source path/checksum and `source_row_indices`: output row *i* equals that source row. These mappings describe the immediate input table.

**Choose the final campaign table before running any simulations.** The event-generation and inference contract is always zero-based: row 0 is `design_000`, and a matrix's `design_index` refers to that same table. A subset or reordered table has its own row numbers. For a staged campaign, use the ordered **full** table for every batch and for inference; select row ranges when submitting work. Do not pass the subset/remainder separately to generation and later join their matrices against the original table. The sidecar mapping is provenance, not an automatic inference remapping instruction. Reordering a full Latin hypercube preserves its stratification and coordinates, but changes design IDs.

The subset distance cache uses memory proportional to the square of the source row count; the intended scale is hundreds to a few thousand rows. It avoids the older three-dimensional pairwise-difference arrays and repeated identical restarts.

## Inspect rows and coverage

```bash
python inspect_design.py --design "$STUDY/design_campaign500.dat" \
  --rows 0,10-12 --show-normalized --out "$STUDY/selected_rows.csv"
```

The inspector validates the header, dimensions, finite values and bounds, then prints prior/observed extrema, minimum separation and duplicate-row count. `--rows` uses zero-based inclusive ranges and preserves requested order. `--show-normalized` changes the printed selected values to unit-prior coordinates; the optional CSV always contains physical parameter values with local `design_index` and `design_name`. A one-row table has no defined minimum pairwise separation. Inspection alone prints to the terminal; `--out` requires an explicit row selection.

## Use the table throughout the pipeline

Generating a parameter table supplies simulator inputs; it does not supply the matching model matrix. Pass the final full table to your event-generation workflow, preserving its row IDs. In the surrounding pce-eventgen repository, `[files] design` in a campaign INI selects this table; profile creation also accepts `--design-file`. Campaign `[run] row_start` / `row_end` select zero-based inclusive batches. Simulation, caching and observable aggregation belong to that separate workflow; none is required to run the inference tools on supplied or synthetic inputs.

Pass the exact campaign table alongside the resulting matrix when checking inputs and fitting:

```bash
python check_inputs.py --system AuAu200 \
  --matrix /absolute/path/to/model_matrix_AuAu200.npz \
  --design "$STUDY/design_campaign500.dat"
python inference.py --system AuAu200 \
  --matrix /absolute/path/to/model_matrix_AuAu200.npz \
  --design "$STUDY/design_campaign500.dat" --out-dir "$STUDY/fit"
```

For an un-reordered campaign, use `design_params.dat` instead. O-O inference uses the same table with `--system OO5360`, its own model matrix and compatible measurements. A joint study accepts the selected table via its top-level `design` field; see [joint calibration](JOINT.md). Always join matrices by their recorded `design_index`, including missing or unsorted rows. The [input contract](INPUTS.md) describes the required matrix, metadata and measurement interfaces.

The old `make_fixed_chem_design.py` applied a particular study's equilibrium replacement (`tau_l = tau_s = 0`, `gamma_l_0 = gamma_s_0 = 1`) to an existing table. That special-case rewrite is not needed for general design generation and is not carried over. Flat-shear inference likewise conditions a full varying design through `--shear-mode flat`; it does not require editing design columns. Simulations with parameters held constant need a matching explicit inference policy, rather than unconstrained sampling over those parameters.

## Check the tools

```bash
python -m unittest discover -s tests -p test_design_tools.py -v
```

The tests check stratification, deterministic generation, source-row mappings, output protection and round trips through both systems' inference design readers. They need only this folder. The surrounding repository keeps separate campaign-renderer integration tests. Neither suite establishes sufficient design coverage for a particular posterior.
