#!/usr/bin/env python3
"""Stage B inference with a FLAT (T-independent) shear viscosity: eta_low = eta_high = 0.

Reference posterior for the fugacity sector. Fugacity enters MUSIC's EoS (T(eps) depends on
gamma) and the eta/s kink temperature moves with gamma, so a T-dependent (eta/s)(T) is not a
fixed reference across fugacity values; a constant eta/s is the only shape invariant under that.

Both slopes are CONDITIONED at 0 in the full emulator (0 is interior to both priors), so this is
the slopes=0 slice of the 14-D primary (inference.py --system AuAu200): same matrix, likelihood,
observable set, kernel and inflation, 12 free params. With both slopes 0 the kink temperature drops out and
eta_min IS the constant eta/s (relabelled below).

    python run_flat_eta.py [--out-dir DIR] [--nsteps N]
"""
import inference as I


if __name__ == "__main__":
    raise SystemExit(I.cli(mode="flat-eta", out_dir="plots_AuAu200_flat_eta", nsteps=2 * I.NSTEPS, auau_only=True))
