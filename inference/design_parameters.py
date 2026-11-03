"""Parameter order and prior box shared by design generation and inference.

Keep the four beam-energy normalizations in every table. Each system's emulator
uses its own normalization and drops the other three columns.
"""

# These are the ranges of the bundled combined500 campaign. The older generator
# used a narrower normalization box, which must not silently replace these priors.
PARAMS_ALL = [
    ("norm5360", 25.0, 60.0),  # O-O 5.36 TeV normalization
    ("norm5020", 20.0, 50.0),  # Pb-Pb 5.02 TeV normalization
    ("norm2760", 15.0, 40.0),  # Pb-Pb 2.76 TeV normalization
    ("norm200", 5.0, 20.0),    # Au-Au 200 GeV normalization
    ("w", 0.5, 1.5),          # nucleon width [fm]
    ("k", 0.8, 2.2),          # gamma-fluctuation shape
    ("tau_l", 0.0, 10.0),     # light equilibration time [fm/c]
    ("tau_s", 0.0, 10.0),     # strange equilibration time [fm/c]
    ("gamma_l_0", 0.1, 1.0),  # initial light occupancy
    ("gamma_s_0", 0.1, 1.0),  # initial strange occupancy
    ("eta_min", 0.01, 0.2),   # eta/s at the temperature kink
    ("eta_low", -2.0, 1.0),   # low-temperature shear slope [GeV^-1]
    ("eta_high", -1.0, 2.0),  # high-temperature shear slope [GeV^-1]
    ("zeta_max", 0.01, 0.25),  # peak zeta/s
    ("zeta_width", 0.01, 0.15),  # bulk peak width [GeV]
    ("zeta_skew", -1.0, 1.0),    # bulk peak asymmetry
    ("zeta_T", 0.12, 0.3),      # bulk peak temperature [GeV]
]
