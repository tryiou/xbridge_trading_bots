import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

import pytest

from gui.utils.theming import REQUIRED_STYLES, warmup_ttk_styles


@pytest.fixture(scope="module")
def tk_root():
    """A real Tk root, skipped when no display is available."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display not available: {error}")
    root.withdraw()  # never show windows on the tester's desktop
    yield root
    root.destroy()


@pytest.fixture(scope="module")
def darkly_style(tk_root):
    """Style bound to tk_root.

    ttkbootstrap Style() binds to tkinter's default root, and creating any
    ttk widget auto-creates the Style singleton. Earlier test modules leave
    both pointing at destroyed windows, so reset them before creating the
    style for this module. warmup_ttk_styles also installs the ttkbootstrap
    global widget api, which intentionally persists for the session.
    """
    import ttkbootstrap
    from ttkbootstrap import Style

    previous_root = tk._default_root
    previous_style = ttkbootstrap.Style.instance
    ttkbootstrap.Style.instance = None
    tk._default_root = tk_root
    try:
        yield Style(theme="darkly")
    finally:
        tk._default_root = previous_root
        ttkbootstrap.Style.instance = previous_style


class TestWarmupTtkStyles:
    def test_all_required_styles_resolve_after_warmup(self, tk_root, darkly_style):
        warmup_ttk_styles(darkly_style, tk_root)
        for _widget_class, style_name, option in REQUIRED_STYLES:
            assert darkly_style.lookup(style_name, option), (
                f"{style_name}.{option} did not resolve after warmup"
            )

    def test_discriminating_styles_are_built(self, tk_root, darkly_style):
        """Options that resolve empty before warmup must resolve after."""
        warmup_ttk_styles(darkly_style, tk_root)
        assert darkly_style.lookup("Treeview", "fieldbackground")
        assert darkly_style.lookup("TEntry", "fieldbackground")

    def test_destroys_warmup_widgets(self, tk_root, darkly_style):
        warmup_ttk_styles(darkly_style, tk_root)
        assert tk_root.winfo_children() == []

    def test_raises_runtime_error_when_style_missing(self, tk_root, darkly_style):
        with (
            patch.object(darkly_style, "lookup", return_value=""),
            pytest.raises(RuntimeError, match="missing required styles"),
        ):
            warmup_ttk_styles(darkly_style, tk_root)

    def test_destroys_created_widgets_when_creation_fails(self, tk_root, darkly_style):
        def failing_factory(master):
            raise tk.TclError("boom")

        classes = [
            (ttk.Frame, "TFrame", "background"),
            (failing_factory, "TFrame", "background"),
        ]
        children_before = tk_root.winfo_children()
        with (
            patch("gui.utils.theming.REQUIRED_STYLES", classes),
            pytest.raises(tk.TclError, match="boom"),
        ):
            warmup_ttk_styles(darkly_style, tk_root)
        assert tk_root.winfo_children() == children_before

    def test_warmup_without_global_api_option(self, tk_root, darkly_style):
        """The enable_global_api guard is a no-op when ttkbootstrap < 2.0."""
        with patch("ttkbootstrap.enable_global_api", None):
            warmup_ttk_styles(darkly_style, tk_root)
        assert tk_root.winfo_children() == []
