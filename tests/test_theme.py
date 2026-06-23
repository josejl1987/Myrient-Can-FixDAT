"""Tests for theme tokens and QSS stylesheet generation."""
import re

from minerva.ui.density import Density
from minerva.ui.theme import ThemeTokens, build_stylesheet


class TestThemeTokens:
    def test_to_dict_keys_match_dataclass_fields(self):
        """Every dataclass field should be in to_dict()."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(ThemeTokens)}
        dict_keys = set(ThemeTokens().to_dict().keys())
        assert fields == dict_keys, f"Mismatch: {fields ^ dict_keys}"

    def test_default_accent_is_green(self):
        assert ThemeTokens().accent == "#22C55E"

    def test_for_accent_supports_three_colors(self):
        for name in ("blue", "purple", "green"):
            tokens = ThemeTokens.for_accent(name)
            assert tokens.accent, f"for_accent({name!r}) returned empty accent"

    def test_for_accent_unknown_defaults_to_green(self):
        assert ThemeTokens.for_accent("nonexistent").accent == "#22C55E"

    def test_font_tokens_exist(self):
        assert ThemeTokens().heading_font == "Russo One"
        assert ThemeTokens().body_font == "Chakra Petch"

    def test_radius_tokens_exist(self):
        assert ThemeTokens().radius_sm == "4px"
        assert ThemeTokens().radius_md == "8px"
        assert ThemeTokens().radius_lg == "12px"


class TestStylesheetGeneration:
    def test_no_unresolved_placeholders(self):
        """build_stylesheet() should resolve all {{token.*}} and {{density.*}} placeholders."""
        qss = build_stylesheet(ThemeTokens(), Density.COMPACT)
        unresolved = re.findall(r"\{\{(?:token|density)\.\w+\}\}", qss)
        assert not unresolved, f"Unresolved placeholders: {unresolved}"

    def test_font_family_present(self):
        qss = build_stylesheet(ThemeTokens(), Density.COMPACT)
        assert "Russo One" in qss or "russo one" in qss.lower()
        assert "Chakra Petch" in qss or "chakra petch" in qss.lower()

    def test_accent_color_present(self):
        qss = build_stylesheet(ThemeTokens(), Density.COMPACT)
        assert "#22C55E" in qss or "#22c55e" in qss.lower()

    def test_radius_tokens_resolved(self):
        qss = build_stylesheet(ThemeTokens(), Density.COMPACT)
        assert "{{token.radius_" not in qss, "Radius tokens not resolved"
