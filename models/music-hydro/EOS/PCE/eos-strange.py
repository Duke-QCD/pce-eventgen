#!/usr/bin/env python3
"""Standalone generator for the custom MUSIC EOS 18 light/strange tables.

This reference tool is not called by installation or campaign commands. It
requires NumPy, SciPy, and the custom frzout.HRG implementation accepting fug_l
and fug_s. Stock frzout does not supply that interface. Use the matching HRG
particle content and dependencies when attempting to reproduce production data.

Example (choose a NEW output directory):
    python eos-strange.py --output-dir /path/to/new-tables

Defaults generate 30 strange slices, 30 light occupancies and 10,000 uniformly
spaced q=(epsilon/HBARC)**(1/4) values. Each k.dat contains little-endian float64
records [q, pressure, entropy, temperature, cs2], ordered [energy, light].
Units are fm^-1, GeV/fm^3, fm^-3, GeV, and dimensionless, respectively.

The fugacity prescription, interpolation, negative-pressure repair, sound-speed
and entropy calculations are retained from the supplied script. This tool does
not install its output or replace the checked production checksum manifest.
"""

import argparse
from pathlib import Path
import warnings

import numpy as np
from scipy.interpolate import CubicHermiteSpline, CubicSpline, KroghInterpolator, PchipInterpolator

# Temperature anchors in GeV for glue, two-light-flavor and full-QCD limits.
Tc0 = 0.260
Tc2 = 0.170
Tc3 = 0.158
HBARC = 0.1973269718  # GeV fm; retain the supplied conversion constant.
FUGACITY_POINTS = 30


def _hotqcd(
    T,
    fug_l,
    fug_s,
    quantity="p_T4",
    pid=95 * np.pi**2 / 180,
    ct=3.8706,
    t0=0.9761,
    an=-8.7704,
    bn=3.9200,
    cn=0,
    dn=0.3419,
    ad=-1.2600,
    bd=0.8425,
    cd=0,
    dd=-0.0475,
):
    """
    Compute the dimensionless pressure p/T^4 or trace anomaly (e - 3p)/T^4 for
    the HotQCD EoS (http://inspirehep.net/record/1307761).

    The pressure is parametrized in Eq. (16) and Table II.  The trace anomaly
    follows from the pressure and Eq. (5):

        (e - 3p)/T^4 = T * d/dT(p/T^4)

    """
    t = T / (fug_s * Tc3 + (fug_l - fug_s) * Tc2 + (1 - fug_l) * Tc0)
    t2 = t * t
    t3 = t2 * t
    t4 = t2 * t2

    # break parametrization into three small functions for easy differentiation
    # p/T^4 = f*g/h
    f = (1 + np.tanh(ct * (t - t0))) / 2
    g = pid + an / t + bn / t2 + cn / t3 + dn / t4
    h = 1 + ad / t + bd / t2 + cd / t3 + dd / t4

    if quantity == "p_T4":
        return f * g / h
    elif quantity == "e3p_T4":
        t5 = t3 * t2

        # derivatives of (f, g, h)
        # note: with t = T/Tc,
        #   T * d/dT = T * 1/Tc d/dt = t * d/dt
        df = ct / (2 * np.cosh(ct * (t - t0)) ** 2)
        dg = -an / t2 - 2 * bn / t3 - 3 * cn / t4 - 4 * dn / t5
        dh = -ad / t2 - 2 * bd / t3 - 3 * cd / t4 - 4 * dd / t5

        return t * (df * g / h + f / h * (dg - g * dh / h))
    else:
        raise ValueError("unknown quantity: {}".format(quantity))


def _NF2(
    T,
    fug_l,
    fug_s,
    quantity="p_T4",
    pid=95 * np.pi**2 / 180,
    ct=3.8706,
    t0=0.9761,
    an=-8.7704,
    bn=3.9200,
    cn=0,
    dn=0.3419,
    ad=-1.2600,
    bd=0.8425,
    cd=0,
    dd=-0.0475,
):
    """Retained Nf=2 anchor: scale the HotQCD fit by the 37/47.5 DOF ratio."""
    t = T / (fug_s * Tc3 + (fug_l - fug_s) * Tc2 + (1 - fug_l) * Tc0)
    t2 = t * t
    t3 = t2 * t
    t4 = t2 * t2

    # break parametrization into three small functions for easy differentiation
    # p/T^4 = f*g/h
    f = (1 + np.tanh(ct * (t - t0))) / 2
    g = pid + an / t + bn / t2 + cn / t3 + dn / t4
    h = 1 + ad / t + bd / t2 + cd / t3 + dd / t4

    # Keep the original order of operations in both pressure and its derivative.
    if quantity == "p_T4":
        return (37 / 47.5) * f * g / h
    elif quantity == "e3p_T4":
        t5 = t3 * t2

        # derivatives of (f, g, h)
        # note: with t = T/Tc,
        #   T * d/dT = T * 1/Tc d/dt = t * d/dt
        df = ct / (2 * np.cosh(ct * (t - t0)) ** 2)
        dg = -an / t2 - 2 * bn / t3 - 3 * cn / t4 - 4 * dn / t5
        dh = -ad / t2 - 2 * bd / t3 - 3 * cd / t4 - 4 * dd / t5

        return (37 / 47.5) * t * (df * g / h + f / h * (dg - g * dh / h))
    else:
        raise ValueError("unknown quantity: {}".format(quantity))


def _pure_glue(T, fug_l, fug_s, quantity="p_T4"):
    newT = T * Tc0 / (fug_s * Tc3 + (fug_l - fug_s) * Tc2 + (1 - fug_l) * Tc0)
    tlat = Tc0 * np.array(
        [
            1e-10,
            0.7,
            0.74,
            0.78,
            0.82,
            0.86,
            0.9,
            0.94,
            0.98,
            1,
            1.02,
            1.06,
            1.10,
            1.14,
            1.18,
            1.22,
            1.26,
            1.30,
            1.34,
            1.38,
            1.42,
            1.46,
            1.5,
            2,
            2.5,
            3,
            3.5,
            4.0,
            4.5,
            5,
            6,
            7,
            8,
            9,
            10,
            20,
            30,
            40,
            50,
            60,
            80,
            100,
            200,
            300,
            400,
            500,
            600,
            800,
            1000,
        ]
    )
    Ilat = np.array(
        [
            0,
            0.0104,
            0.0162,
            0.0232,
            0.0318,
            0.0433,
            0.0594,
            0.0859,
            0.1433,
            1.0008,
            2.078,
            2.4309,
            2.4837,
            2.4309,
            2.3426,
            2.2342,
            2.1145,
            1.998,
            1.8867,
            1.7809,
            1.6810,
            1.5872,
            1.4995,
            0.8038,
            0.5057,
            0.3589,
            0.2736,
            0.2207,
            0.1855,
            0.1606,
            0.1266,
            0.1050,
            0.0903,
            0.0798,
            0.0720,
            0.0375,
            0.0265,
            0.0216,
            0.0191,
            0.0174,
            0.0154,
            0.0142,
            0.0112,
            0.01,
            0.0091,
            0.0085,
            0.0080,
            0.0073,
            0.0068,
        ]
    )
    plat = np.array(
        [
            0,
            0.0015,
            0.0023,
            0.0033,
            0.0046,
            0.0064,
            0.0087,
            0.0118,
            0.0164,
            0.0222,
            0.0571,
            0.1455,
            0.237,
            0.325,
            0.4074,
            0.4837,
            0.5539,
            0.6181,
            0.677,
            0.7309,
            0.7804,
            0.8258,
            0.8675,
            1.189,
            1.3319,
            1.4098,
            1.4582,
            1.491,
            1.5149,
            1.533,
            1.5591,
            1.5768,
            1.5898,
            1.5998,
            1.6078,
            1.6444,
            1.6572,
            1.6641,
            1.6686,
            1.672,
            1.6767,
            1.68,
            1.6887,
            1.693,
            1.6958,
            1.6977,
            1.6992,
            1.7014,
            1.703,
        ]
    )

    newSpline = CubicHermiteSpline(tlat, plat, Ilat / tlat)

    if quantity == "p_T4":
        return newSpline(newT)
    elif quantity == "e3p_T4":
        return PchipInterpolator(tlat, Ilat)(newT)
    else:
        raise ValueError("unknown quantity: {}".format(quantity))


def p_T4_lattice(T, fug_l, fug_s):
    """
    Lattice pressure p/T^4.

    """
    return (
        fug_s**2 * _hotqcd(T, fug_l, fug_s, quantity="p_T4")
        + (fug_l**2 - fug_s**2) * _NF2(T, fug_l, fug_s, quantity="p_T4")
        + (1.0 - fug_l**2) * _pure_glue(T, fug_l, fug_s, quantity="p_T4")
    )


def e3p_T4_lattice(T, fug_l, fug_s):
    """
    Lattice trace anomaly (e - 3p)/T^4.

    """
    return (
        fug_s**2 * _hotqcd(T, fug_l, fug_s, quantity="e3p_T4")
        + (fug_l**2 - fug_s**2) * _NF2(T, fug_l, fug_s, quantity="e3p_T4")
        + (1.0 - fug_l**2) * _pure_glue(T, fug_l, fug_s, quantity="e3p_T4")
    )


class HRGEOS:
    """
    Hadron resonance gas equation of state at a set of temperature points.

    """

    def __init__(self, T, **kwargs):
        # Required dependency: use the custom frzout whose HRG accepts fug_l and
        # fug_s. Stock frzout lacks these arguments and cannot run this generator.
        import frzout

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._hrgs = [frzout.HRG(t, **kwargs) for t in T]

        self._T4 = T**4 / HBARC**3

    def _calc(self, quantity):
        return np.array([getattr(hrg, quantity)() for hrg in self._hrgs])

    def p_T4(self):
        return self._calc("pressure") / self._T4

    def e_T4(self):
        return self._calc("energy_density") / self._T4

    def e3p_T4(self):
        return (self._calc("energy_density") - 3 * self._calc("pressure")) / self._T4

    def cs2(self):
        return self._calc("cs2")


def T_points(Tmin, Tmax, n, extra_low=0, extra_high=0):
    """
    Create evenly-spaced temperature points, with optional "extra" points
    outside the range.  Total number of points is (n + extra_low + extra_high).

    """
    T = np.arange(-extra_low, n + extra_high, dtype=float)
    T *= (Tmax - Tmin) / (n - 1)

    T += Tmin
    return T


def fix_negs(array):
    """Repair the prefix through the last negative entry with the original PCHIP.

    Keep the nonnegative origin and ten following samples as anchors. This is a
    numerical prescription from the supplied generator, not elementwise clipping.
    """
    indices = np.where(array < 0)[0]
    if indices.size == 0:
        return array
    index = indices[-1]
    if index + 11 > len(array):
        raise ValueError("Negative-prefix repair needs ten samples after the last negative value")
    x_spline = np.append(np.zeros(1), np.arange(index + 1, index + 11, 1))
    y_spline = np.append(np.array([max(0, array[0])]), array[index + 1 : index + 11])
    array[: index + 1] = PchipInterpolator(x_spline, y_spline)(np.arange(0, index + 1, 1))
    return array


def finite_diff_uniform(vals, x, idx):
    """Use centered differences inside the grid and three-point edge formulas."""
    n = len(vals)
    if idx == 0:
        return (-3 * vals[0] + 4 * vals[1] - vals[2]) / (x[2] - x[0])
    elif idx == n - 1:
        return (3 * vals[-1] - 4 * vals[-2] + vals[-3]) / (x[-1] - x[-3])
    else:
        return (vals[idx + 1] - vals[idx - 1]) / (x[idx + 1] - x[idx - 1])


def connection_temperatures(lambda_l, lambda_s):
    """Retain the occupancy-dependent HRG/lattice connection interval, in GeV."""
    root_l, root_s = np.sqrt(lambda_l), np.sqrt(lambda_s)
    anchor = Tc3 * root_s + Tc0 * (1 - root_l) + Tc2 * (root_l - root_s)
    return 0.005 + anchor, 0.010 + 0.005 * lambda_s + 0.005 * lambda_l + anchor


def pressure_temperature(args, lambda_l, lambda_s, q_grid=None):
    """Build one occupancy slice on the shared energy grid.

    File/grid labels are lambda_l and lambda_s. The lattice functions take their
    square roots; their squared mixing weights recover the file/grid labels.
    The HRG uses the separate retained prescription 0.15 + 0.85*lambda.
    """
    fug_l, fug_s = np.sqrt(lambda_l), np.sqrt(lambda_s)
    Ta, Tb = connection_temperatures(lambda_l, lambda_s)
    Ta = Ta if args.Ta is None else args.Ta
    Tb = Tb if args.Tb is None else args.Tb
    if not args.Tmin < Ta < Tb < args.Tmax:
        raise ValueError("Each slice requires Tmin < Ta < Tb < Tmax")
    hrg_kwargs = dict(
        species=args.species,
        res_width=args.res_width,
        fug_l=0.15 + 0.85 * fug_l**2,
        fug_s=0.15 + 0.85 * fug_s**2,
    )

    # The temperature mesh is distinct from the final q mesh. Preserve the
    # 1000-point segments and two extra end samples used by the original spline.
    Tl = T_points(args.Tmin, Ta, 1000, extra_low=2)
    low = HRGEOS(Tl, **hrg_kwargs).e3p_T4()
    nd = 5

    def derivatives(function, T0, dT=0.001):
        # Five Chebyshev nodes estimate the value and four endpoint derivatives.
        points = T0 + dT * np.cos(np.linspace(0, np.pi, nd))
        return KroghInterpolator(points, function(points)).derivatives(T0)

    # Exclude Ta/Tb from the connection: the adjoining segments supply them.
    Tm = T_points(Ta, Tb, 1000, extra_low=-1, extra_high=-1)
    middle = KroghInterpolator(
        nd * [Ta] + nd * [Tb],
        np.concatenate(
            [
                derivatives(lambda T: HRGEOS(T, **hrg_kwargs).e3p_T4(), Ta),
                derivatives(lambda T: e3p_T4_lattice(T, fug_l, fug_s), Tb),
            ]
        ),
    )(Tm)
    Th = T_points(Tb, args.Tmax, 1000, extra_high=2)
    high = e3p_T4_lattice(Th, fug_l, fug_s)
    temperatures = np.concatenate([Tl, Tm, Th])
    trace_anomaly = np.concatenate([low, middle, high])

    # Integrate d(p/T^4)/dT = (epsilon-3p)/T^5. The integration constant
    # matches the high-temperature lattice pressure at Tmax, not the HRG at Tmin.
    integral = CubicSpline(temperatures, trace_anomaly / temperatures).antiderivative()
    pressure_offset = p_T4_lattice(args.Tmax, fug_l, fug_s) - integral(args.Tmax)

    def compute_p_T4(T):
        return integral(T) + pressure_offset

    p_T4 = compute_p_T4(temperatures)
    epsilon = (trace_anomaly + 3 * p_T4) * temperatures**4 / HBARC**3

    # Retain sequential nudging (including propagation through a decreasing run),
    # so epsilon is strictly increasing for the inverse temperature interpolator.
    for i in range(1, len(epsilon)):
        if epsilon[i] <= epsilon[i - 1]:
            epsilon[i] = epsilon[i - 1] + 1e-10

    # All occupancy slices use the q grid set by the first (lambda_l=lambda_s=0)
    # slice. Retain PCHIP extrapolation when other slices cover a different range.
    if q_grid is None:
        q_grid = np.linspace(
            (max(0.0, epsilon[0]) / HBARC) ** 0.25, (epsilon[-1] / HBARC) ** 0.25, args.nsteps
        )
    temperature = PchipInterpolator(epsilon, temperatures)(q_grid**4 * HBARC)
    pressure = compute_p_T4(temperature) * temperature**4 / HBARC**3
    return q_grid, fix_negs(pressure), temperature


def pressure_fugacity_derivative(interpolators, fugacities, index, temperatures):
    """Evaluate the original three-point stencil at fixed T for an entire slice.

    Only neighboring occupancy curves enter a finite difference. Evaluating them
    as arrays avoids the original nested loop over every energy sample and every
    occupancy, while keeping the centered/one-sided formulas unchanged.
    """
    start = min(max(index - 1, 0), len(fugacities) - 3)
    values = [curve(temperatures) for curve in interpolators[start : start + 3]]
    return finite_diff_uniform(values, fugacities[start : start + 3], index - start)


def generate_tables(args, fugacity_points=FUGACITY_POINTS, progress=print):
    """Return q, pressure, entropy, temperature and cs2 without writing files.

    Thermodynamic arrays are indexed [light, strange, energy]. The optional
    reduced occupancy grid is useful for numerical checks; MUSIC requires the
    default 30 x 30 x 10000 production grid.
    """
    if fugacity_points < 3 or args.nsteps < 3:
        raise ValueError("At least three occupancy and energy points are required")
    fugacities = np.linspace(0, 1, fugacity_points)
    shape = (fugacity_points, fugacity_points, args.nsteps)
    pressure, temperature, entropy, cs2 = [np.empty(shape) for _ in range(4)]
    q_grid = None
    for j, lambda_l in enumerate(fugacities):
        progress(f"Pressure/temperature: light slice {j+1}/{fugacity_points}")
        for k, lambda_s in enumerate(fugacities):
            q_grid, pressure[j, k], temperature[j, k] = pressure_temperature(
                args, lambda_l, lambda_s, q_grid
            )

    # Calculate cs2 from the repaired final pressure at fixed occupancy, rather
    # than from the unmodified lattice fit or intermediate connection polynomial.
    epsilon = q_grid**4 * HBARC
    for j in range(fugacity_points):
        for k in range(fugacity_points):
            cs2[j, k] = fix_negs(np.gradient(pressure[j, k], epsilon, edge_order=2))

    p_of_T = [
        [
            PchipInterpolator(temperature[j, k], pressure[j, k], extrapolate=True)
            for k in range(fugacity_points)
        ]
        for j in range(fugacity_points)
    ]
    for j, lambda_l in enumerate(fugacities):
        progress(f"Entropy: light slice {j+1}/{fugacity_points}")
        for k, lambda_s in enumerate(fugacities):
            T = temperature[j, k]
            entropy[j, k] = (epsilon + pressure[j, k]) / T
            # s = (epsilon+p)/T - sum_a log(lambda_a)*lambda_a*(dp/dlambda_a)_T/T.
            # At lambda=0 the corresponding contribution is omitted, as before.
            if lambda_l > 0:
                curves = [p_of_T[jj][k] for jj in range(fugacity_points)]
                dp_dlambda = pressure_fugacity_derivative(curves, fugacities, j, T)
                entropy[j, k] -= np.log(lambda_l) * (lambda_l * dp_dlambda) / T
            if lambda_s > 0:
                dp_dlambda = pressure_fugacity_derivative(p_of_T[j], fugacities, k, T)
                entropy[j, k] -= np.log(lambda_s) * (lambda_s * dp_dlambda) / T
            entropy[j, k][entropy[j, k] < 0] = 0.0
    return q_grid, pressure, entropy, temperature, cs2


def write_tables(output_dir, q_grid, pressure, entropy, temperature, cs2):
    """Write each strange slice in the exact record order consumed by EOS 18."""
    output_dir = Path(output_dir)
    for k in range(pressure.shape[1]):
        records = np.empty((len(q_grid), pressure.shape[0], 5), dtype="<f8")
        records[:, :, 0] = q_grid[:, None]
        for column, values in enumerate((pressure, entropy, temperature, cs2), start=1):
            records[:, :, column] = values[:, k, :].T
        # Exclusive creation also protects callers using this function directly.
        with (output_dir / f"{k}.dat").open("xb") as stream:
            records.tofile(stream)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="New output directory; existing directories are refused.",
    )
    parser.add_argument("--Tmin", type=float, default=0.005, help="Minimum HRG temperature, GeV.")
    parser.add_argument(
        "--Ta", type=float, help="Connection minimum, GeV (default: occupancy dependent)."
    )
    parser.add_argument(
        "--Tb", type=float, help="Connection maximum, GeV (default: occupancy dependent)."
    )
    parser.add_argument(
        "--Tmax", type=float, default=1.0, help="High-temperature pressure anchor, GeV."
    )
    parser.add_argument(
        "--nsteps",
        type=int,
        default=10000,
        help="Energy-grid points; MUSIC requires the default 10000.",
    )
    parser.add_argument(
        "--species", choices=["all", "urqmd", "id"], default="all", help="HRG species set."
    )
    parser.add_argument(
        "--res-width-off",
        action="store_false",
        dest="res_width",
        help="Disable finite resonance widths in the HRG.",
    )
    args = parser.parse_args(argv)
    if not 0 < args.Tmin < args.Tmax or args.nsteps < 3:
        parser.error("Require 0 < Tmin < Tmax and nsteps >= 3")
    return parser, args


def main(argv=None):
    parser, args = parse_args(argv)
    output = args.output_dir.expanduser().resolve()
    if output.exists():
        parser.error(f"Output directory already exists: {output}; choose a new directory")
    # Check the custom HRG interface before reserving output or doing a full grid.
    try:
        HRGEOS(
            np.array([args.Tmin]),
            species=args.species,
            res_width=args.res_width,
            fug_l=0.15,
            fug_s=0.15,
        )
    except (ImportError, TypeError) as error:
        parser.error(f"Custom frzout.HRG with fug_l/fug_s support is required: {error}")
    try:
        output.mkdir(parents=True, exist_ok=False)
        tables = generate_tables(args)
        write_tables(output, *tables)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Wrote {FUGACITY_POINTS} EOS tables to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
