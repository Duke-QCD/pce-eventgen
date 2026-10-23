"""Shared dissertation figure theme and paper parameter labels.

The context manager restores Matplotlib settings after each renderer. This
module contains display settings only; it does not load inputs or fit models.
"""
from contextlib import contextmanager
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_AMBER, C_BLUE, C_MED, C_DATA = "#E69F00", "#5B8FC9", "#2B2B2B", "#C0392B"
C_GREEN = "#009E73"
PAPER_LABELS = {"norm200": r"$\mathrm{Norm}_{200}$", "norm5360": r"$\mathrm{Norm}_{5360}$",
                "tau_l": r"$\tau_{\mathrm{eq},\ell}$", "tau_s": r"$\tau_{\mathrm{eq},s}$"}


def set_pub_style():
    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 300, "savefig.bbox": "tight", "pdf.fonttype": 42,
        "font.family": "serif", "mathtext.fontset": "cm",
        "font.size": 16, "axes.labelsize": 19, "axes.titlesize": 18,
        "xtick.labelsize": 15, "ytick.labelsize": 15, "legend.fontsize": 16,
        "axes.linewidth": 1.0, "lines.linewidth": 2.0,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.major.size": 5, "ytick.major.size": 5, "xtick.minor.visible": True,
        "ytick.minor.visible": True, "xtick.minor.size": 3, "ytick.minor.size": 3,
        "legend.frameon": False, "figure.facecolor": "white", "axes.facecolor": "white",
        "savefig.facecolor": "white",
    })


def set_talk_style():
    """Defense-slide styling: pub style, then bump every font and line for legibility at PowerPoint scale."""
    set_pub_style()
    plt.rcParams.update({
        "savefig.dpi": 200,
        "font.size": 22, "axes.labelsize": 27, "axes.titlesize": 24,
        "xtick.labelsize": 20, "ytick.labelsize": 20, "legend.fontsize": 24,
        "axes.linewidth": 1.4, "lines.linewidth": 2.8,
        "xtick.major.size": 6, "ytick.major.size": 6,
        "xtick.minor.size": 3.5, "ytick.minor.size": 3.5,
    })



@contextmanager
def dissertation_style():
    """Use the predictive figures' publication theme without changing caller settings."""
    with plt.rc_context():
        set_pub_style()
        yield
