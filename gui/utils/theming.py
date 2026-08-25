# gui/utils/theming.py
import contextlib
import logging
import tkinter as tk
from tkinter import ttk

import ttkbootstrap
from ttkbootstrap import Style

logger = logging.getLogger(__name__)

# (widget class, ttk style name, option) verified after warmup. Most options
# also resolve via the "." fallback even when unbuilt, so the post-warmup
# check is a smoke test; Treeview.fieldbackground is the one option that
# reliably resolves empty when its style was never built.
REQUIRED_STYLES: tuple[tuple[type[ttk.Widget], str, str], ...] = (
    (ttk.Frame, "TFrame", "background"),
    (ttk.Label, "TLabel", "background"),
    (ttk.Button, "TButton", "background"),
    (ttk.Entry, "TEntry", "fieldbackground"),
    (ttk.Notebook, "TNotebook.Tab", "background"),
    (ttk.LabelFrame, "TLabelFrame", "background"),
    (ttk.Treeview, "Treeview", "fieldbackground"),
    (ttk.Scrollbar, "TScrollbar", "background"),
    (ttk.Checkbutton, "TCheckbutton", "background"),
)


def warmup_ttk_styles(style: Style, root: tk.Misc) -> None:
    """Force-build every ttk style used by the GUI before the UI is created.

    ttkbootstrap builds ttk widget styles lazily, the first time a widget of
    a class is constructed, and swallows some build errors. A lazy build
    that misfires leaves the application half-styled: some widgets use the
    dark theme while others silently fall back to the light base theme.

    On ttkbootstrap >= 2.0 the constructor patching that triggers those lazy
    builds is opt-in (`enable_global_api`); without it, plain tkinter.ttk
    widgets never build their styles at all. It is enabled here when
    available, then one throwaway widget per class moves every style build
    to startup, where a failure raises instead of degrading the UI.

    Args:
        style: ttkbootstrap Style instance with the theme already applied.
        root: Master for the throwaway widgets (the application root).

    Raises:
        RuntimeError: If a required style option does not resolve after
            warmup.
    """
    enable_global_api = getattr(ttkbootstrap, "enable_global_api", None)
    if enable_global_api is not None:
        enable_global_api()

    widgets: list[ttk.Widget] = []
    try:
        for widget_class, _, _ in REQUIRED_STYLES:
            widgets.append(widget_class(root))
    finally:
        for widget in widgets:
            with contextlib.suppress(tk.TclError):
                widget.destroy()

    unbuilt = [
        f"{style_name}.{option}"
        for _, style_name, option in REQUIRED_STYLES
        if not style.lookup(style_name, option)
    ]
    if unbuilt:
        raise RuntimeError(
            f"Theme '{style.theme_use()}' missing required styles after "
            f"warmup: {', '.join(unbuilt)}"
        )
    logger.info(
        "Theme '%s' ready, styles built: %s",
        style.theme_use(),
        ", ".join(style_name for _, style_name, _ in REQUIRED_STYLES),
    )
