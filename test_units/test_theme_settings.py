import pytest

from gui.utils import theming
from gui.utils.theming import (
    DEFAULT_THEME,
    apply_saved_theme,
    available_themes,
    load_saved_theme,
    row_colors,
    save_theme,
    tag_alternating_rows,
)


class FakeColors:
    """Minimal Colors stand-in exposing only the colors the palette needs."""

    def __init__(self, bg: str, fg: str, warning: str, danger: str):
        self.bg = bg
        self.fg = fg
        self.warning = warning
        self.danger = danger


class FakeStyle:
    """Minimal Style stand-in for non-display tests."""

    def __init__(
        self,
        names: list[str],
        current: str = DEFAULT_THEME,
        bg: str = "#222222",
        fg: str = "#ffffff",
        warning: str = "#ffaa00",
        danger: str = "#ff3333",
        mode: str = "dark",
    ):
        self._names = set(names)
        self._current = current
        self.colors = FakeColors(bg, fg, warning, danger)
        self.theme_mode = mode

    def theme_names(self) -> tuple[str, ...]:
        return tuple(self._names)

    def theme_use(self, themename: str | None = None) -> str:
        if not themename:
            return self._current
        if themename not in self._names:
            import tkinter as tk

            raise tk.TclError(f"{themename} is not a valid theme")
        self._current = themename
        return self._current


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "gui_settings.yaml"
    monkeypatch.setattr(theming, "THEME_SETTINGS_PATH", str(path))
    return str(path)


class TestLoadSavedTheme:
    def test_missing_file_returns_default(self, settings_path):
        assert load_saved_theme(FakeStyle([DEFAULT_THEME])) == DEFAULT_THEME

    def test_saved_theme_returned_when_available(self, settings_path):
        save_theme("nord-dark")
        style = FakeStyle([DEFAULT_THEME, "nord-dark"])
        assert load_saved_theme(style) == "nord-dark"

    def test_unknown_theme_falls_back_to_default(self, settings_path):
        save_theme("does-not-exist")
        style = FakeStyle([DEFAULT_THEME])
        assert load_saved_theme(style) == DEFAULT_THEME

    def test_non_string_value_falls_back_to_default(self, settings_path):
        theming.save_yaml(settings_path, {"theme": 123})
        style = FakeStyle([DEFAULT_THEME])
        assert load_saved_theme(style) == DEFAULT_THEME

    def test_corrupt_file_falls_back_to_default(self, settings_path):
        with open(settings_path, "w") as f:
            f.write("{invalid yaml: [")
        style = FakeStyle([DEFAULT_THEME])
        assert load_saved_theme(style) == DEFAULT_THEME

    def test_binary_garbage_falls_back_to_default(self, settings_path):
        with open(settings_path, "wb") as f:
            f.write(b"\x00\x01\x02\xff\xfe binary garbage")
        style = FakeStyle([DEFAULT_THEME])
        assert load_saved_theme(style) == DEFAULT_THEME

    def test_non_mapping_yaml_falls_back_to_default(self, settings_path):
        with open(settings_path, "w") as f:
            f.write("- just\n- a\n- list\n")
        style = FakeStyle([DEFAULT_THEME])
        assert load_saved_theme(style) == DEFAULT_THEME


class TestSaveTheme:
    def test_roundtrip(self, settings_path):
        save_theme("nord-dark")
        style = FakeStyle([DEFAULT_THEME, "nord-dark"])
        assert load_saved_theme(style) == "nord-dark"

    def test_unwritable_path_is_logged_not_raised(self, tmp_path, monkeypatch, caplog):
        import logging

        directory = tmp_path / "missing-dir"
        monkeypatch.setattr(
            theming, "THEME_SETTINGS_PATH", str(directory / "gui_settings.yaml")
        )
        with caplog.at_level(logging.ERROR, logger="gui.utils.theming"):
            save_theme("nord-dark")  # must not raise
        assert any(record.levelno == logging.ERROR for record in caplog.records)
        assert not (directory / "gui_settings.yaml").exists()


class TestAvailableThemes:
    def test_sorted_theme_names(self):
        style = FakeStyle(["zzz-dark", "aaa-light", "mmm-dark"])
        assert available_themes(style) == ["aaa-light", "mmm-dark", "zzz-dark"]


class TestApplySavedTheme:
    def test_switches_to_saved_theme(self, settings_path):
        save_theme("nord-dark")
        style = FakeStyle([DEFAULT_THEME, "nord-dark"])
        assert apply_saved_theme(style) == "nord-dark"
        assert style.theme_use() == "nord-dark"

    def test_keeps_current_when_saved_matches(self, settings_path):
        style = FakeStyle([DEFAULT_THEME])
        assert apply_saved_theme(style) == DEFAULT_THEME
        assert style.theme_use() == DEFAULT_THEME

    def test_invalid_saved_theme_falls_back_to_default(self, settings_path):
        theming.save_yaml(settings_path, {"theme": "does-not-exist"})
        style = FakeStyle([DEFAULT_THEME, "nord-dark"], current="nord-dark")
        assert apply_saved_theme(style) == DEFAULT_THEME
        assert style.theme_use() == DEFAULT_THEME


def _hex_value(color: str) -> float:
    """HSV value (brightness) of a hex color, 0.0-1.0."""
    import colorsys

    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r, g, b)[2]


class TestRowColors:
    def test_dark_theme_rows_lighter_than_background(self):
        style = FakeStyle([DEFAULT_THEME], bg="#222222", mode="dark")
        even, odd = row_colors(style)
        assert even != odd
        assert _hex_value(even) > _hex_value("#222222")
        assert _hex_value(odd) > _hex_value(even)

    def test_light_theme_rows_darker_than_background(self):
        style = FakeStyle(["dracula-light"], bg="#f8f8f2", mode="light")
        even, odd = row_colors(style)
        assert even != odd
        assert _hex_value(even) < _hex_value("#f8f8f2")
        assert _hex_value(odd) < _hex_value(even)


class TestLogPalette:
    def test_dark_mode_uses_theme_colors(self):
        from gui.utils.theming import log_palette

        palette = log_palette(FakeStyle([DEFAULT_THEME], mode="dark"))
        assert palette["background"] == "#222222"
        assert palette["foreground"] == palette["INFO"] == "#ffffff"
        assert palette["insertbackground"] == "#ffffff"
        assert palette["WARNING"] == "#ffaa00"
        assert palette["ERROR"] == palette["CRITICAL"] == "#ff3333"

    def test_debug_dimmer_than_foreground_on_dark(self):
        from gui.utils.theming import log_palette

        palette = log_palette(FakeStyle([DEFAULT_THEME], mode="dark"))
        assert _hex_value(palette["DEBUG"]) < _hex_value(palette["foreground"])

    def test_debug_dimmer_than_foreground_on_light(self):
        from gui.utils.theming import log_palette

        palette = log_palette(FakeStyle(["dracula-light"], fg="#1a1a1a", mode="light"))
        # On light themes "dim" means lighter (closer to the background).
        assert _hex_value(palette["DEBUG"]) > _hex_value(palette["foreground"])


@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display not available: {error}")
    root.withdraw()  # never show windows on the tester's desktop
    yield root
    root.destroy()


@pytest.fixture
def fresh_style(tk_root):
    """Yield a Style bound to this module's root, not a stale singleton.

    ttkbootstrap's Style is a process-wide singleton bound to whatever Tk
    root existed when first created; earlier test modules leave one behind.
    Tests that switch themes and assert on live recoloring must run against
    a style sharing their own interpreter, so the singleton is reset here
    and the previous instance restored afterwards.
    """
    pytest.importorskip("ttkbootstrap")

    from ttkbootstrap import Style

    previous = Style.instance
    Style.instance = None
    try:
        style = Style(theme=DEFAULT_THEME)
    except BaseException:
        Style.instance = previous
        raise
    yield style
    Style.instance = previous


class TestApplySavedThemeDisplay:
    def test_real_style_switches_theme(self, fresh_style, tmp_path, monkeypatch):
        """Integration with a real Style, skipped without a display."""
        settings_file = str(tmp_path / "gui_settings.yaml")
        monkeypatch.setattr(theming, "THEME_SETTINGS_PATH", settings_file)
        theming.save_yaml(settings_file, {"theme": "nord-dark"})

        assert apply_saved_theme(fresh_style) == "nord-dark"
        assert fresh_style.theme_use() == "nord-dark"

    def test_row_colors_follow_live_theme_switch(
        self, tk_root, fresh_style, monkeypatch
    ):
        """Row tag colors update when the theme changes at runtime."""
        from tkinter import ttk

        from gui.utils.theming import apply_row_colors

        monkeypatch.setattr(theming, "THEME_SETTINGS_PATH", "/nonexistent")
        style = fresh_style
        tree = ttk.Treeview(tk_root, columns=["a"], show="headings")
        apply_row_colors(tree)
        dark_even = tree.tag_configure("evenrow")["background"]
        dark_odd = tree.tag_configure("oddrow")["background"]

        style.theme_use("dracula-light")
        apply_row_colors(tree)
        light_even = tree.tag_configure("evenrow")["background"]
        light_odd = tree.tag_configure("oddrow")["background"]

        assert dark_even != light_even
        assert dark_odd != light_odd
        tree.destroy()


class TestTagAlternatingRowsDisplay:
    def _make_tree(self, tk_root):
        from tkinter import ttk

        return ttk.Treeview(tk_root, columns=["a"], show="headings")

    @staticmethod
    def _row_tags(tree):
        return [tree.item(item_id, "tags")[0] for item_id in tree.get_children()]

    def test_tags_alternate_by_position(self, tk_root):
        from gui.utils.theming import tag_alternating_rows

        tree = self._make_tree(tk_root)
        for index in range(4):
            tree.insert("", "end", values=(index,))
        tag_alternating_rows(tree)
        assert self._row_tags(tree) == ["evenrow", "oddrow", "evenrow", "oddrow"]
        tree.destroy()

    def test_rows_retagged_after_removal(self, tk_root):
        from gui.utils.theming import tag_alternating_rows

        tree = self._make_tree(tk_root)
        for index in range(4):
            tree.insert("", "end", values=(index,))
        tag_alternating_rows(tree)

        first_child = tree.get_children()[0]
        tree.delete(first_child)
        tag_alternating_rows(tree)
        assert self._row_tags(tree) == ["evenrow", "oddrow", "evenrow"]
        tree.destroy()

    def test_tag_colors_follow_theme_switch(self, tk_root, fresh_style, monkeypatch):
        from gui.utils.theming import attach_row_color_listener

        monkeypatch.setattr(theming, "THEME_SETTINGS_PATH", "/nonexistent")
        style = fresh_style
        tree = self._make_tree(tk_root)
        remove_listener = attach_row_color_listener(tree)
        tree.insert("", "end", values=("x",))
        tag_alternating_rows(tree)
        dark_even = tree.tag_configure("evenrow")["background"]

        style.theme_use("dracula-light")
        tk_root.update()
        light_even = tree.tag_configure("evenrow")["background"]

        assert dark_even != light_even
        remove_listener()
        tree.destroy()


class TestLogThemeListenerDisplay:
    def test_log_colors_follow_live_theme_switch(
        self, tk_root, fresh_style, monkeypatch
    ):
        """Log recolors when <<ThemeChanged>> reaches it after a theme switch."""
        import tkinter as tk

        from gui.utils.theming import attach_log_theme_listener

        monkeypatch.setattr(theming, "THEME_SETTINGS_PATH", "/nonexistent")
        style = fresh_style
        text = tk.Text(tk_root)
        attach_log_theme_listener(text)
        text.insert("end", "info line\n", ("INFO",))
        dark_bg = text.configure("background")[4]
        dark_info = text.tag_configure("INFO")["foreground"]
        assert dark_info  # tag configured at attach time

        style.theme_use("dracula-light")
        # Generate on the widget itself: synthetic delivery of the exact
        # event the listener handles, independent of Tk's per-interp,
        # per-widget-class broadcast quirks.
        text.event_generate("<<ThemeChanged>>")
        tk_root.update()
        light_bg = text.configure("background")[4]
        light_info = text.tag_configure("INFO")["foreground"]

        assert dark_bg != light_bg
        assert dark_info != light_info
        underline = text.tag_configure("CRITICAL")["underline"]
        assert underline and underline[-1] in (1, "1")
        text.destroy()
