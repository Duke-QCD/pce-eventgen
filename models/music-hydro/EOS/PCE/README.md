# Standalone EOS generator

`eos-strange.py` retains the supplied generator for the custom MUSIC EOS 18.
It is available for reference and deliberate regeneration; installation,
NERSC/OSG campaigns and the smoke test do not run it.

It requires NumPy, SciPy, and the custom `frzout.HRG` accepting `fug_l` and
`fug_s`. Stock `frzout` does not implement those arguments. Use the matching
custom HRG implementation and particle content when reproducing the supplied
production tables; the script does not install dependencies.

Run from any directory, specifying a **new** output directory:

```bash
python /path/to/pce-eventgen/models/music-hydro/EOS/PCE/eos-strange.py --help
python /path/to/pce-eventgen/models/music-hydro/EOS/PCE/eos-strange.py \
  --output-dir /path/to/new-eos-tables
```

The calculation is substantial. Defaults generate 30 strange-occupancy slices
with 30 light occupancies and 10,000 energy points each, occupying 360 MB on
disk and several hundred MB in memory. Existing output directories are refused.

Each file `k.dat` corresponds to `lambda_s = k/29`. Within it, records are
ordered first by energy index, then by light occupancy `lambda_l = j/29`.
Every record contains five little-endian float64 values:

| Value | Units |
|---|---|
| `q = (epsilon/HBARC)^(1/4)` | fm^-1 |
| Pressure | GeV/fm³ |
| Entropy density | fm^-3 |
| Temperature | GeV |
| `cs2 = dp/d epsilon` at fixed occupancy | Dimensionless |

All slices share the energy grid determined by the zero-occupancy slice.
The lattice functions use square roots of the occupancy labels; HRG occupancies
use the retained `0.15 + 0.85*lambda` prescription. Connection temperatures vary
with occupancy unless explicitly overridden. Comments in the script explain
pressure matching, negative-prefix repair, and entropy derivatives at fixed T.

`--Tmin`, `--Ta`, `--Tb`, `--Tmax`, `--species` and `--res-width-off` change the
selected calculation. `--nsteps` now also sizes the output arrays; its default
10,000 points is required by the current MUSIC reader. Other grid sizes are
for standalone diagnostics.

Output is not installed automatically and the checked production manifest is
never rewritten. Before using a regenerated default-sized set, compare it with
the approved production checksums:

```bash
python /path/to/pce-eventgen/tools/check_physics_data.py eos /path/to/new-eos-tables
```

A mismatch requires scientific review of the inputs and numerical environment;
do not replace the manifest merely to make this check pass. The existing
verified production tables remain the reference dataset.
