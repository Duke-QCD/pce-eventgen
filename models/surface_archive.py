"""Location of an event's archived particlization surface.

HIC_SURFACE_ARCHIVE_BASE fixes the source directory relative to which archive
paths are stored. Without it, legacy callers use $PSCRATCH or $SCRATCH:

    <archive root>/<campaign>/<design>/<system>/<event>/surface.dat.zst

Legacy callers outside a scratch root keep only the last four components, so
such campaigns need distinct names within a shared archive root. models/run-events
is installed on its own, so it carries a copy of archive_relative_path;
tests/test_model_runners.py checks that the two agree.
"""

import os
from pathlib import Path
import re

SURFACE_ARCHIVE_NAME = "surface.dat.zst"


def archive_relative_path(event_dir):
    """Use the configured archive base, with legacy scratch/tail fallback if unset."""
    event_dir = Path(event_dir).resolve()
    configured = os.environ.get("HIC_SURFACE_ARCHIVE_BASE")
    if configured:
        expanded = os.path.expandvars(os.path.expanduser(configured))
        if re.search(r'\$(?:[A-Za-z_]\w*|\{[^}]+\})', expanded):
            raise ValueError(f"Unresolved variable in HIC_SURFACE_ARCHIVE_BASE: {configured}")
        base = Path(expanded)
        if not base.is_absolute():
            raise ValueError("HIC_SURFACE_ARCHIVE_BASE must be an absolute path")
        try:
            return event_dir.relative_to(base.resolve())
        except ValueError:
            raise ValueError(f"{event_dir} is outside HIC_SURFACE_ARCHIVE_BASE={base}; "
                             "refusing to change the archive layout") from None
    roots = [os.environ.get(name) for name in ("PSCRATCH", "SCRATCH")]
    for root in roots:
        if root:
            try:
                return event_dir.relative_to(Path(root).expanduser().resolve())
            except ValueError:
                pass
    # Drop the filesystem anchor so the result always stays below the archive root.
    return Path(*event_dir.relative_to(event_dir.anchor).parts[-4:])


def configure_archive_base(value, raw_root):
    """Resolve a CLI/INI selection and reject a mismatched base before any work."""
    if value is not None:
        # CLI/INI paths, unlike environment defaults, may be launch-relative.
        expanded = os.path.expandvars(os.path.expanduser(str(value)))
        if re.search(r'\$(?:[A-Za-z_]\w*|\{[^}]+\})', expanded):
            raise ValueError(f"Unresolved variable in archive_base: {value}")
        if not expanded:
            raise ValueError("archive_base must not be empty")
        os.environ["HIC_SURFACE_ARCHIVE_BASE"] = str(Path(expanded).resolve())
    archive_relative_path(raw_root)
    configured = os.environ.get("HIC_SURFACE_ARCHIVE_BASE")
    if configured:
        return str(Path(os.path.expandvars(os.path.expanduser(configured))).resolve())
    return None


def archived_surface_path(archive_root, event_dir):
    """Return the surface.dat.zst path for event_dir below archive_root."""
    return Path(archive_root) / archive_relative_path(event_dir) / SURFACE_ARCHIVE_NAME
