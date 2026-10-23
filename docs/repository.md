# Repository map and developer guide

[Handbook](README.md) · [Command index](reference/commands.md)

## Find the implementation

| Location | Role and entry points |
|---|---|
| `README.md`, `docs/` | Overview, onboarding, concepts, glossary, reference, and troubleshooting |
| [`config/initialization/`](../config/initialization/README.md) | Campaign design table, base parameter template, standalone example `parameters.dat`, MUSIC input template; see [parameter mapping](reference/physics.md) before changing |
| [`config/templates/`](../config/templates/) | The four profile files `nersc/configure_user.py` renders: paths, launcher, INI, `activate.sh` |
| `config/` | Also the SMASH workflow YAML and cache fingerprints; generated profiles go under the ignored `config/users/` |
| `local/install`, `nersc/install` | Shared installation into the active environment; NERSC delegates to the local builder |
| `nersc/configure_user.py` | Render a personal profile from `config/templates/`; no installation, data copying, initialization or submission |
| `models/campaign.py` | Preferred NERSC command dispatcher using the shared INI |
| `models/run_designs.py` | Parse design table, render per-design parameters, initialize, audit/repair and route/submit designs |
| `models/initialize.py` | Render MUSIC input and run TRENTo to create a design bundle |
| `models/run-events`, `models/run-events-wrapper` | Maintained event runner and runtime-discovery wrapper, also installed in `bin/` |
| `models/repair_stale_events.py` | Between-pass event auditing, supported repair, archive finalization, readiness/routing outputs |
| `models/select_fill.py`, `models/fill_wave.sh` | Near-complete-design planning and allocation-local execution |
| `models/workflow_config.py` | Shared INI schema, typed mappings, path expansion, scope environment |
| `models/workflow_records.py`, `models/workflow_status.py` | Record/history classification and compact next-action reporting |
| `models/submit_cache_reap_campaign.py` | Frozen batch cache jobs and local cache/reap dispatch |
| `models/summarize_cache_reap_records.py` | Review verdict records and emit clean-only reap manifest |
| `models/surface_archive.py` | The surface-archive path rule shared by repair and maintenance tools |
| `models/cache/` | [Cache backend guide](../models/cache/README.md) |
| `models/analysis/` | [Cache analysis and diagnostics](../models/analysis/README.md) |
| `models/maintenance/` | [Marker census, stuck-event quarantine, archive deletion](../models/maintenance/README.md) |
| `osg/` | AP staging/splitting/sweep/drain, Condor submit file, worker, container recipe/runtime paths and tests |
| `inference/` | Standalone emulator and Bayesian inference ("Stage B"), input preflight, measurement tables and its own environment; see its [README](../inference/README.md) |
| `tools/` | Workflow syntax/path checks, production-data checksum checks, documentation generation |
| `tests/`, `osg/tests/` | Workflow integration/unit tests with temporary fixtures |

## Model source trees

| Tree | Read first | Active custom areas / other material |
|---|---|---|
| `models/trento/` | [README](../models/trento/README.rst), [installation](../models/trento/doc/installation.rst), [usage](../models/trento/doc/usage.rst) | C++ in `src/`, ManualNucleus reader, nuclear-table extraction helper, upstream-style examples/tests/docs |
| `models/music-hydro/` | [Workflow README](../models/music-hydro/README.md) | `src/eos_PCE.*`, `evolve.cpp`, `transport_coeffs.cpp`, input renderer, one-cell surface helper, EOS generator/data manifest; `utilities/` and `tests/` retain model-specific tools |
| `models/iS3D/` | [Workflow README and PCE limitations](../models/iS3D/README.md) | Maintained CPU C++ in `src/cpp/`, `paths.sh`, sampler config/data, `iS3D_OSCAR.py`; `scripts/` for sampling/plots; coefficient generator workspaces include archived unsupported paths |
| `models/SMASH/` | [README](../models/SMASH/README.md), [INSTALL](../models/SMASH/INSTALL.md) | Git submodule pinned to unmodified upstream SMASH `0985d6b3` (SMASH-3.0-1); particle/decay inputs are compiled in; `3rdparty/` contains bundled dependencies |

Generated `build/` directories and compiled binaries are ignored by Git. The
maintained installer uses separate MUSIC/iS3D/SMASH `build-workflow/` directories;
the TRENTo build can require clearing an obsolete machine-specific cache by
moving the generated directory before a clean rebuild. Preserve source and
physics inputs. CUDA, old makefiles, upstream examples, hackathon benchmarks,
and coefficient regeneration are separate development tools with their own
assumptions, not steps required for the production chain.

## Data and provenance

Track source changes, small configuration and manifests in version control;
keep the large EOS and iS3D coefficient tables in separately managed storage,
verified by `tools/check_physics_data.py` against their checked-in manifests. Record the exact source revision/dirty changes, model build toolchain,
Python environment, particle/coefficient/EOS checksums, nuclear-table metadata,
design table, initialized bundle, profile/INI and cache fingerprint.

The maintained campaign procedures are the [NERSC guide](nersc-campaign.md) and
[OSG runbook](osg-campaign.md). Matrix, statistical-error and feed-down contracts
live in the [analysis-input reference](reference/analysis-inputs.md).
Use current records, audits, matrix metadata and source checks for live counts,
input compatibility and validation status.

## Preparing a Git handoff

Commit the workflow, configuration templates, documentation, tests and data
manifests together. TRENTo, MUSIC and iS3D are ordinary tracked source trees.
SMASH is the sole submodule: `.gitmodules` records its public upstream URL and
the parent commit records revision `0985d6b32fb801404c933d70c86c23759d60ff1f`.
Keep the submodule checkout clean; local changes inside it are not included in
a parent-repository commit.

Before handing over a revision, check it from a fresh recursive clone and run
the verification commands below:

```bash
git status --short
git submodule status --recursive
git diff HEAD --check
git clone --recurse-submodules --branch BRANCH REPOSITORY_URL NEW_DIRECTORY
```

Replace the last command's arguments with the published branch, repository URL
and a new directory. A source ZIP or `git archive` from the parent repository
does not include SMASH's contents; use a recursive clone. Build directories,
personal profiles and `.recovery/` stay local. Supply the separately managed
physics datasets and verify their manifests as described in the
[first-run guide](getting-started.md#1-obtain-code-and-physics-data).
Existing campaigns also need their saved configuration and
[archive base](reference/files.md#surface-archive).

## Verification without running physics jobs

From the repository root:

```bash
python3 tools/check_workflow.py
PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -v
PYTHONNOUSERSITE=1 python3 -m unittest discover -s osg/tests -v
python3 tools/build_docs.py --check
```

The workflow checker compiles Python syntax in memory, checks shell syntax and
configured script paths, without importing models or submitting jobs. Tests
exercise path resolution, orchestration, runner handoffs, marker/history policy,
cache dispatch and OSG transfer/deletion behavior with temporary fixtures.
Some tests need compatible NumPy/h5py or build tools and report skips otherwise.
Passing these checks is not a scientific regression or a full model build.

After building TRENTo, include its native cache/input regressions by pointing
`TRENTO_TEST_EXECUTABLE` at the freshly built executable:

```bash
TRENTO_TEST_EXECUTABLE=/absolute/path/to/trento PYTHONNOUSERSITE=1 \
  python3 -m unittest discover -s tests -p test_trento_runtime.py
```

These tests use temporary cache directories and bounded subprocesses. They are
skipped when no executable is selected; archive round-trip tests require `zstd`.

For cache/analysis arithmetic, `python models/analysis/check_cache_profiles.py`
runs synthetic end-to-end profile checks in an analysis environment. The EOS
generator tests use a controlled custom-HRG fixture; they do not certify a
fresh production EOS. For changed model physics, plan separate numerical
regressions and controlled data/interface runs with documented inputs.

## Maintaining this handbook

`tools/build_docs.py` parses Python source without importing or executing the
tools. It generates `docs/reference/cli-*.md` and `model-input-defaults.md`.
It uses only Python's standard library and can run from any directory:

```bash
python3 tools/build_docs.py
python3 tools/build_docs.py --check
```

Update source help when adding/changing flags, then regenerate and review the
diff. The generator captures declarations, not runtime truth: handwritten
guides must explain action boundaries, INI/environment precedence, shared
helpers, positional/non-argparse tools, scientific caveats, and subcommand
context. Add a new source directory to `GROUPS` when adding a new tool family.
The check exits nonzero for stale/missing pages and writes nothing.

Keep the root README as an entry point. Put tutorials and conceptual explanations
in the handbook, exact operational procedures in the campaign/site runbooks,
file/data contracts in `docs/reference/`,
and model-specific limitations beside the model. Link between them rather
than copying an entire deployment recipe into every page.
