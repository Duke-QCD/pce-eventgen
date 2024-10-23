This is a workflow for generating initial conditions using TRENTO (https://github.com/Duke-QCD/trento) and evolving hydrodynamically using a modified version of MUSIC (https://github.com/MUSIC-fluid/MUSIC/tree/public_stable). The hydro equation of state, found in models/music-hydro/EOS/PCE, is a large file and Git LFS is needed to download it.

See the local and/or NERSC directories for installation instructions.

The run-events script is adapted from https://github.com/Duke-QCD/hic-eventgen/tree/master. Though many of the usage instructions there are obsolete due to the different models used, it may still be a useful reference for general usage of the run-events executable.

There are two sets of relevant parameters: those used by MUSIC, and those used by TRENTO. MUSIC's parameters are given by the input file; see example_inputfiles for the format. TRENTO's can be passed as arguments, either within run-events (if generating initial conditions together with hydro events) or when generating the initial condition file (if pregenerating initial conditions, which is the current default setup).
