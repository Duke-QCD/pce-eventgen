# pce-eventgen

Event generation and Bayesian calibration for relativistic heavy-ion collisions
with partial chemical equilibrium (PCE). Each simulated collision runs
TRENTo (initial conditions) → MUSIC (viscous hydrodynamics with light and
strange chemical occupancies) → iS3D (particlization) → SMASH (hadronic
transport). Campaigns run on NERSC Perlmutter, optionally with events generated
on the OSPool; their output is reduced to a calibration matrix and fitted by the
standalone inference pipeline.

**New here? Start with the [handbook](docs/README.md).** It explains the
workflow, walks through a first run, and links every reference page. Unfamiliar
terms (design, oversample, cache, reap, fingerprint, …) are defined in the
[glossary](docs/glossary.md).

## Get the code and data

```bash
git clone --recurse-submodules https://github.com/andrewgordeev/music-eventgen.git pce-eventgen
```

TRENTo, MUSIC and iS3D are customized copies stored in this repository; SMASH
is a submodule pinned to an unmodified upstream commit. Two large production
datasets are kept outside Git and must be obtained from the maintainer: the
MUSIC EOS 18 tables and the iS3D two-occupancy coefficient tables. Verify any
copy with `python3 tools/check_physics_data.py {eos,is3d} DIR`. The
[first-run guide](docs/getting-started.md#1-obtain-code-and-physics-data)
explains where each goes.

## Repository layout

| Location | Contents |
|---|---|
| [`docs/`](docs/README.md) | Handbook: concepts, tutorials, campaign runbooks and reference pages |
| `models/` | Campaign dispatcher (`campaign.py`), initialization, event runner, repair and shared helpers |
| `models/cache/`, [`models/analysis/`](models/analysis/README.md), `models/maintenance/` | Event caching and raw-data reaping; observable extraction and matrix building; standalone recovery tools |
| `models/trento/`, `models/music-hydro/`, `models/iS3D/`, `models/SMASH/` | Model source trees |
| `config/` | Shared configuration: [profile templates](config/templates/), cache fingerprints, SMASH YAML, and [initialization inputs](config/initialization/README.md) |
| `nersc/`, `local/` | Installation and NERSC profile generation |
| `osg/` | OSPool staging, worker, container recipe and draining |
| [`inference/`](inference/README.md) | Standalone emulator and Bayesian inference, with its own environment |
| `tools/`, `tests/` | Repository checks, data checksums, documentation generator and tests |

## Checks

From the repository root:

```bash
python3 tools/check_workflow.py
PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests
PYTHONNOUSERSITE=1 python3 -m unittest discover -s osg/tests
python3 tools/build_docs.py --check
```

These check syntax, configured paths, workflow logic with stand-in models, and
that the generated command reference is current. They do not build or run the
physics models; see the [developer guide](docs/repository.md#verification-without-running-physics-jobs).

## License

Based on Jonah Bernhard's [hic-eventgen](https://github.com/Duke-QCD/hic-eventgen),
with substantial subsequent development by Andrew Gordeev. Repository-specific
workflow code and documentation use the [MIT license](LICENSE). Bundled models
and dependencies retain their own licenses.
