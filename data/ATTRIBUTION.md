# Physics data attribution

This bundle contains the frozen production inputs used by the pce-eventgen partial chemical equilibrium workflow, packaged by Andrew Gordeev. The accompanying manifests identify the exact EOS and two-occupancy coefficient files. The original production copies were supplied by the maintainer; packaging preserves their numerical contents without regeneration.

The EOS tables are the custom MUSIC EOS 18 set with independent light and strange occupancies. MUSIC source credits include Bjoern Schenke, Sangyong Jeon, Charles Gale and Chun Shen. The workflow's EOS reader and standalone generator are available in the pce-eventgen repository.

The iS3D runtime inputs include particle metadata, quadrature tables and delta-f coefficients. iS3D credits Michael McNelis, Derek Everett, Sameed Pervaiz and Lipei Du. The ten production `smash_pce_light_strange` coefficient files use independent light and strange occupancies; the retained legacy single-occupancy generator is not their reproduction recipe.

Project source and model references: https://github.com/Duke-QCD/pce-eventgen

The `licenses/` directory preserves the repository MIT notice, the iS3D MIT notice and the MUSIC GPL version 2 text. These existing notices are retained without relicensing third-party components. Keep this file and those notices with redistributed copies. No account registration or separate attribution acknowledgement is required by the download helper.
