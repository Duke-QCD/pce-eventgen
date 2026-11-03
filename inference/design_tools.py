"""Shared design I/O, Latin-hypercube generation, and maximin selection.

Adapted from the calibration workspace's make_design.py, design_subset.py,
design_subset_remainder.py, and inspect_design.py. No STAT checkout, R, pandas,
or local campaign files are required. Importing this module writes nothing.
"""

import hashlib
from io import StringIO
import json
from pathlib import Path

import numpy as np
import scipy
from scipy.spatial.distance import cdist, pdist
from scipy.stats import qmc

from design_parameters import PARAMS_ALL

PARAM_NAMES = [name for name, _, _ in PARAMS_ALL]
LOWER = np.array([low for _, low, _ in PARAMS_ALL])
UPPER = np.array([high for _, _, high in PARAMS_ALL])


def validate_design(values):
    """Reject ambiguous shapes, nonfinite entries, and a mismatched prior box."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or not len(values) or values.shape[1] != len(PARAM_NAMES):
        raise ValueError(f"Design must have at least one row and {len(PARAM_NAMES)} columns.")
    if not np.isfinite(values).all():
        raise ValueError("Design must contain finite parameter values.")
    outside = np.any((values < LOWER) | (values > UPPER), axis=0)
    if outside.any():
        names = ", ".join(np.array(PARAM_NAMES)[outside])
        raise ValueError(f"Design values outside the shared prior box: {names}")
    return values


def read_design(path):
    """Read the named 17-column table; accept commented or plain Parameter headers."""
    header = None
    rows = []
    for raw in Path(path).expanduser().read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        tokens = line.removeprefix("#").strip().split()
        if tokens and tokens[0] == "Parameter":
            if header is not None or rows:
                raise ValueError("Design must have one Parameter header before its data.")
            header = tokens[1:]
        elif not line.startswith("#"):
            if header is None:
                raise ValueError("Design is missing a Parameter header before its data.")
            rows.append(line)
    if header != PARAM_NAMES:
        raise ValueError(f"Design header mismatch: expected {' '.join(PARAM_NAMES)}")
    if not rows:
        raise ValueError("Design must contain at least one row.")
    return validate_design(np.loadtxt(StringIO("\n".join(rows)), ndmin=2))


def normalize_design(values):
    """Scale by the prior box, so subset scores use the same metric as the full design.

    Using each subset's observed extrema would change the metric between subsets
    and give misleading comparisons, especially for small subsets.
    """
    return (validate_design(values) - LOWER) / (UPPER - LOWER)


def min_pairwise_distance(unit):
    """Euclidean separation in unit-prior coordinates; undefined for a single row."""
    return float(pdist(unit).min()) if len(unit) > 1 else None


def positive_integer(value):
    value = int(value)
    if value < 1:
        raise ValueError("Value must be a positive integer.")
    return value


def nonnegative_integer(value):
    value = int(value)
    if value < 0:
        raise ValueError("Value must be a nonnegative integer.")
    return value


def generate_design(ndesign=500, seed=12345, candidates=20):
    """Keep the best of independent randomized LHS candidates by minimum separation.

    This is a reproducible best-of-candidates search, not R lhs::maximinLHS and
    not a global maximin optimizer. Every candidate retains Latin stratification.
    """
    ndesign, candidates = positive_integer(ndesign), positive_integer(candidates)
    rng = np.random.default_rng(nonnegative_integer(seed))
    best, best_score = None, -np.inf
    for _ in range(candidates):
        sampler = qmc.LatinHypercube(d=len(PARAM_NAMES), seed=int(rng.integers(2**32)))
        unit = sampler.random(n=ndesign)
        score = min_pairwise_distance(unit)
        if best is None or (score is not None and score > best_score):
            best, best_score = unit, score
    return qmc.scale(best, LOWER, UPPER)


def greedy_maximin_subset(values, n_select, n_starts=20, seed=12345):
    """Choose farthest-first subsets from multiple starting rows; return source row IDs.

    Distances are cached once. Each restart updates the nearest-selected distance
    rather than allocating a rows-by-rows-by-parameters array on every iteration.
    """
    unit = normalize_design(values)
    n_select, n_starts = positive_integer(n_select), positive_integer(n_starts)
    if n_select > len(unit):
        raise ValueError("n_select cannot exceed the number of design rows.")
    rng = np.random.default_rng(nonnegative_integer(seed))
    distances = cdist(unit, unit)
    # Trying a starting row twice gives the same greedy subset; cap at N starts.
    starts = rng.choice(len(unit), size=min(n_starts, len(unit)), replace=False)
    best, best_score = None, -np.inf
    for first in starts:
        selected = [int(first)]
        nearest = distances[first].copy()
        nearest[first] = -np.inf
        score = np.inf
        for _ in range(1, n_select):
            next_row = int(np.argmax(nearest))
            score = min(score, nearest[next_row])
            selected.append(next_row)
            nearest = np.minimum(nearest, distances[next_row])
            nearest[selected] = -np.inf
        if best is None or score > best_score:
            best, best_score = np.array(selected, dtype=int), score
    return best


def source_identity(path):
    path = Path(path).expanduser().resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write_designs(products, *, protected=()):
    """Write (path, values, provenance) bundles, refusing any existing output.

    Preflight all tables and sidecars together, so a bad remainder destination
    cannot leave behind only the selected table. Remove our partial writes on
    failure; never replace another file or the source table.
    """
    payloads = {}
    protected = {Path(path).expanduser().resolve() for path in protected}
    for path, values, provenance in products:
        path = Path(path).expanduser().resolve()
        values = validate_design(values)
        table = StringIO()
        # Seventeen significant digits preserve float64 values across reorderings.
        np.savetxt(table, values, fmt="%.17g", header="Parameter " + " ".join(PARAM_NAMES))
        content = table.getvalue()
        metadata = dict(provenance, schema="design/v1", ndesign=len(values),
                        parameter_ranges={name: [low, high] for name, low, high in PARAMS_ALL},
                        numpy_version=np.__version__, scipy_version=scipy.__version__,
                        design_sha256=hashlib.sha256(content.encode()).hexdigest())
        for target, text in ((path, content),
                             (path.with_suffix(".meta.json"), json.dumps(metadata, indent=2, allow_nan=False) + "\n")):
            if target in payloads or target in protected:
                raise ValueError(f"Output paths overlap each other or an input: {target}")
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Refusing to overwrite {target}")
            payloads[target] = text
    created = []
    try:
        for path, content in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x") as output:
                created.append(path)
                output.write(content)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise
