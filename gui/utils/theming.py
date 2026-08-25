# gui/utils/theming.py
import colorsys
import contextlib
import logging
import os
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

import ttkbootstrap
from ruamel.yaml.error import YAMLError
from ttkbootstrap import Style
from ttkbootstrap.style.theme import Colors

from definitions.yaml_utils import load_yaml, save_yaml

logger = logging.getLogger(__name__)

DEFAULT_THEME: str = "bootstrap-dark"
THEME_SETTINGS_PATH: str = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "config",
        "gui_settings.yaml",
    )
)
_THEME_SETTINGS_KEY: str = "theme"

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


def available_themes(style: Style) -> list[str]:
    """Return the sorted list of themes the dropdown can offer.

    Args:
        style: ttkbootstrap Style instance.

    Returns:
        Sorted theme names available in the current ttkbootstrap install.
    """
    return sorted(style.theme_names())


def load_saved_theme(style: Style) -> str:
    """Load the theme saved in the GUI settings file.

    Falls back to `DEFAULT_THEME` when the file is missing, unreadable or
    names a theme the installed ttkbootstrap does not provide.

    Args:
        style: ttkbootstrap Style instance used to validate the saved name.

    Returns:
        A theme name guaranteed to be available.
    """
    try:
        data = load_yaml(THEME_SETTINGS_PATH)
    except (OSError, ValueError, YAMLError) as error:
        logger.debug("No readable theme settings file (%s)", error)
        return DEFAULT_THEME
    saved = data.get(_THEME_SETTINGS_KEY) if isinstance(data, dict) else None

    if not isinstance(saved, str) or saved not in style.theme_names():
        logger.warning(
            "Saved theme %r is not available; falling back to %r", saved, DEFAULT_THEME
        )
        return DEFAULT_THEME
    return saved


def save_theme(theme: str) -> None:
    """Persist the selected theme to the GUI settings file.

    Failures are logged and swallowed: an unwritable settings file must not
    crash the GUI.

    Args:
        theme: Theme name to persist.
    """
    try:
        save_yaml(THEME_SETTINGS_PATH, {_THEME_SETTINGS_KEY: theme})
    except OSError as error:
        logger.error("Failed to save theme selection: %s", error)
        return
    logger.info("Theme '%s' saved to %s", theme, THEME_SETTINGS_PATH)


def apply_saved_theme(style: Style) -> str:
    """Apply the saved theme to `style`, falling back to the default.

    Args:
        style: ttkbootstrap Style instance with a startup theme applied.

    Returns:
        The theme name actually in use after the switch.
    """
    saved = load_saved_theme(style)
    if saved == style.theme_use():
        return saved
    try:
        style.theme_use(saved)
    except tk.TclError as error:
        logger.warning(
            "Failed to apply theme %r (%s); keeping %r",
            saved,
            error,
            style.theme_use(),
        )
        return style.theme_use()
    logger.info("Applied saved theme '%s'", saved)
    return saved


_ROW_EVEN_DELTA_DARK: float = 0.06
_ROW_ODD_DELTA_DARK: float = 0.12
_ROW_EVEN_DELTA_LIGHT: float = -0.07
_ROW_ODD_DELTA_LIGHT: float = -0.14


def _shift_brightness(color: str, delta: float) -> str:
    """Shift the brightness (HSV value) of a hex color by an absolute delta.

    Args:
        color: Hex color string (e.g. "#222222").
        delta: Absolute value delta; positive brightens, negative darkens.

    Returns:
        The resulting hex color string.
    """
    r, g, b = Colors.hex_to_rgb(color)
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    value = max(0.0, min(1.0, value + delta))
    r_, g_, b_ = colorsys.hsv_to_rgb(hue, saturation, value)
    return f"#{round(r_ * 255):02x}{round(g_ * 255):02x}{round(b_ * 255):02x}"


def row_colors(style: Style) -> tuple[str, str]:
    """Derive alternating Treeview row backgrounds from the active theme.

    Rows are shaded away from the theme background: lighter on dark themes,
    darker on light themes, so the text color supplied by the theme stays
    readable in both modes.

    Args:
        style: ttkbootstrap Style instance with a theme applied.

    Returns:
        Tuple of (evenrow background, oddrow background) hex colors.
    """
    bg = style.colors.bg
    if style.theme_mode == "light":
        return (
            _shift_brightness(bg, _ROW_EVEN_DELTA_LIGHT),
            _shift_brightness(bg, _ROW_ODD_DELTA_LIGHT),
        )
    return (
        _shift_brightness(bg, _ROW_EVEN_DELTA_DARK),
        _shift_brightness(bg, _ROW_ODD_DELTA_DARK),
    )


def apply_row_colors(tree: ttk.Treeview) -> None:
    """Configure the evenrow/oddrow tags of `tree` for the active theme.

    Args:
        tree: Treeview whose "evenrow"/"oddrow" tags are recolored.
    """
    style = Style.get_instance()
    if style is None:
        logger.debug("No Style instance available; keeping current row colors")
        return
    even_color, odd_color = row_colors(style)
    tree.tag_configure("evenrow", background=even_color)
    tree.tag_configure("oddrow", background=odd_color)


def attach_row_color_listener(tree: ttk.Treeview) -> Callable[[], None]:
    """Apply theme-aware row colors to `tree` and recolor on theme change.

    Args:
        tree: Treeview whose "evenrow"/"oddrow" tags follow the theme.

    Returns:
        A zero-arg callable that removes the theme-change listener.
    """
    apply_row_colors(tree)
    toplevel = tree.winfo_toplevel()
    bind_id = toplevel.bind(
        "<<ThemeChanged>>",
        lambda event: apply_row_colors(tree),
        add="+",
    )
    # Note: on Tk <= 8.6.13 the event only reaches the main window, so
    # secondary-toplevel trees keep their initial colors until remount.

    def remove_listener() -> None:
        with contextlib.suppress(tk.TclError):
            tree.winfo_toplevel().unbind("<<ThemeChanged>>", bind_id)

    return remove_listener


def tag_alternating_rows(tree: ttk.Treeview) -> None:
    """Assign evenrow/oddrow tags to all rows of `tree` by current position.

    Re-run this after any row mutation (insert or delete): row positions
    shift and the alternating pattern must be recomputed.

    Args:
        tree: Treeview whose rows are (re)tagged.
    """
    apply_row_colors(tree)
    for index, item_id in enumerate(tree.get_children()):
        tag = "evenrow" if index % 2 == 0 else "oddrow"
        tree.item(item_id, tags=(tag,))
