# Repository maintenance guide

[Handbook](README.md) · [Command index](reference/commands.md)

## Code map

| Location | Responsibility |
|---|---|
| [`config/initialization/`](../config/initialization/README.md), `config/config.yaml`, `config/cache_fingerprint_*.json` | Design inputs and model templates, SMASH settings, and cache scope definitions |
| [`config/templates/`](../config/templates/), `nersc/configure_user.py` | Personal runtime paths, launcher, INI and activation script; generated profiles live in ignored `config/users/` |
| `local/install`, `nersc/install` | Build and install models and runners into the active environment |
| `nersc/stage.py` | Upload source without local artifacts; verify and stage external EOS/iS3D runtime data |
| `tools/download_physics_data.py`, [`data/`](../data/README.md) | Download the versioned data bundle with automatic checks; attribution, release manifest and packaging instructions |
| `models/campaign.py`, `models/run_designs.py` | Dispatch configured commands; select, initialize, repair and submit designs |
| `models/initialize.py` | Render MUSIC input and generate TRENTo initial conditions |
| `models/run-events`, `models/run-events-wrapper` | Run events and select runtime dependencies; also installed in `bin/` |
| `models/repair_stale_events.py`, `models/select_fill.py`, `models/fill_wave.sh` | Audit/repair interrupted events and plan/run completion passes |
| `models/workflow_*.py`, `models/surface_archive.py` | Shared configuration, records, status and archive-path rules |
| [`models/cache/`](../models/cache/README.md), `models/submit_cache_reap_campaign.py`, `models/summarize_cache_reap_records.py` | Survey, cache, publish, review and reap; dispatch jobs and summarize verdicts |
| [`models/analysis/`](../models/analysis/README.md), [`models/maintenance/`](../models/maintenance/README.md) | Extract/reduce observables and perform standalone recovery or cleanup |
| [`models/trento/`](../models/trento/README.rst) | Customized initial-condition generator, including nuclear-table input |
| [`models/music-hydro/`](../models/music-hydro/README.md) | Customized hydrodynamics; EOS, evolution and transport code in `src/` |
| [`models/iS3D/`](../models/iS3D/README.md) | Customized CPU sampler in `src/cpp/`, particle/coefficient inputs and OSCAR converter |
| [`models/SMASH/`](../models/SMASH/README.md) | Pinned upstream Git submodule; particle/decay tables are compiled into the executable |
| [`osg/`](../osg/README.md) | OSG staging, submission, workers, transfer and container build files |
| [`inference/`](../inference/README.md) | Self-contained design generation, emulator, Bayesian inference, bundled design/measurements, guides and tests |
| `tools/`, `tests/`, `osg/tests/` | Repository/data checks, documentation generator and workflow tests |

Model build directories, downloaded `physics-data/` and prepared `dist/` archives are ignored by Git. Large EOS and iS3D coefficient datasets are supplied as a versioned release; `tools/package_physics_data.py` prepares it and `tools/download_physics_data.py` downloads/verifies it. See [setup](getting-started.md) for installation and [physics inputs](reference/physics.md) before changing model conventions.

Keep personal profiles and campaign notes under ignored `config/users/`. Public configuration examples belong in `config/templates/`.

## Publication

The publication branch is `codex/publication`. Keep manuscript drafts and exports, personal profiles, credentials, recovery snapshots, generated inference matrices/results and physics-data archives local. The root and inference `.gitignore` files exclude them; `nersc/stage.py code` also excludes them from source transfers. Public measurement reference inputs and their provenance remain part of the inference source.

Before publishing, commit the intended source changes, run the checks below and audit the exact branch, including its earlier revisions:

```bash
python3 tools/check_publication.py --ref codex/publication
git status --short
git submodule status --recursive
```

The publication audit checks historical paths, recognizable credential formats, large Git blobs and legacy LFS pointers without printing secret values. It does not certify data redistribution rights or inspect upstream submodule history. Review any newly added files before committing them; ignore rules do not remove files already present in Git history.

For the first publication, create an empty public `Duke-QCD/pce-eventgen` repository without initializing a README, license or `.gitignore`. Push only the reviewed publication branch:

```bash
git push --dry-run origin codex/publication:main
git push origin codex/publication:main
```

Set `main` as the GitHub default branch. Local recovery branches retain the original history, including old personal job files, so do not publish them with `--all` or `--mirror`. Only the publication branch has those historical files and the obsolete LFS-only EOS input removed; its rewritten commit IDs differ from the preserved local originals.

Publish the prepared archive separately as described in [physics-data release instructions](../data/README.md), using the exact release tag `physics-data` and asset name `pce-eventgen-physics-data.tar.gz`. Then test a fresh recursive clone, the physics-data download and the [first-run walkthrough](getting-started.md). Source publication and data publication are separate actions.

## Verification without running physics jobs

From the repository root:

```bash
python3 tools/check_workflow.py
PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -v
PYTHONNOUSERSITE=1 python3 -m unittest discover -s osg/tests -v
python3 tools/build_docs.py --check
```

The workflow checker checks syntax and configured paths without running models or submitting jobs. Tests use temporary fixtures to check orchestration, event states, cache policy and OSG transfer/deletion behavior. Some checks require NumPy/h5py or build tools and report skips when unavailable. Passing these checks does not establish numerical or physics validity.

After building TRENTo, enable its native cache/input regressions:

```bash
TRENTO_TEST_EXECUTABLE=/absolute/path/to/trento PYTHONNOUSERSITE=1 \
  python3 -m unittest discover -s tests -p test_trento_runtime.py
```

These tests are skipped without a selected executable; archive round-trip checks also require `zstd`.

For cache/analysis arithmetic, run `python models/analysis/check_cache_profiles.py` in the analysis environment. Model or physics changes also need numerical regressions with documented inputs; synthetic workflow fixtures and EOS-generator tests cannot certify production physics data.

## Maintaining this handbook

`tools/build_docs.py` generates `docs/reference/cli-*.md` and `model-input-defaults.md` by parsing source without importing or executing tools. After changing command help or model defaults, regenerate and check the references from the repository root:

```bash
python3 tools/build_docs.py
python3 tools/build_docs.py --check
```

Inference owns its [CLI reference](../inference/CLI.md) and standalone generator, `inference/build_docs.py`. The repository generator delegates to it and keeps `docs/reference/cli-inference.md` as a pointer. From the inference folder alone, `python build_docs.py` and `python build_docs.py --check` maintain the same reference without repository-level tools.

Review the generated diff. Add new tool families to `GROUPS` in the generator. `--check` reports stale/missing pages without writing files. Handwritten guides must explain resolved defaults, configuration precedence, action boundaries and scientific limits that source declarations alone cannot convey.

Keep the root README as the entry point, tutorials in the handbook, operational procedures in the site runbooks, file/data contracts in `docs/reference/`, and model-specific limitations beside each model. Link between pages instead of duplicating procedures. Write each prose paragraph on one source line with a blank line between paragraphs; preserve formatting inside commands, tables and diagrams.
