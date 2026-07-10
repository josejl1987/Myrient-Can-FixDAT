"""Semantic theme tokens and stylesheet builder for Minerva.

The UI is intentionally driven by a small semantic palette rather than page-
specific colours.  Page widgets should consume these tokens through QSS and
avoid hard-coded RGB values so accent changes remain coherent application-wide.
"""

from __future__ import annotations

from dataclasses import dataclass

from minerva.ui.density import Density
from minerva.ui.qss.loader import load_qss


@dataclass(frozen=True)
class ThemeTokens:
    # Core surfaces
    background: str = "#070C14"
    surface: str = "#0D1522"
    surface_raised: str = "#152033"
    surface_hover: str = "#1B2940"
    surface_sunken: str = "#09111D"
    sidebar: str = "#09111D"
    border: str = "#223049"
    border_strong: str = "#31435F"
    text: str = "#F3F6FB"
    text_muted: str = "#9AA8BC"
    text_subtle: str = "#65758C"
    disabled_text: str = "#526077"
    scrollbar: str = "#34445E"

    # Fonts. Segoe UI is native on the primary Windows target and Qt falls
    # back to the platform sans-serif on other systems.
    heading_font: str = "Segoe UI"
    body_font: str = "Segoe UI"

    # Shape tokens
    radius_sm: str = "6px"
    radius_md: str = "10px"
    radius_lg: str = "14px"

    # Interactive accent
    accent: str = "#3B82F6"
    accent_hover: str = "#2563EB"
    accent_pressed: str = "#1D4ED8"
    focus_ring: str = "#60A5FA"

    # Semantic accents
    success: str = "#34D399"
    warning: str = "#FBBF24"
    error: str = "#FB7185"
    purple: str = "#C084FC"

    # Semantic surfaces
    success_surface: str = "#0B2923"
    success_border: str = "#1C5B4D"
    warning_surface: str = "#2C220B"
    warning_border: str = "#66501A"
    error_surface: str = "#30131B"
    error_border: str = "#6C2939"
    info_surface: str = "#0B2342"
    info_border: str = "#214D82"
    purple_surface: str = "#251538"
    purple_border: str = "#553078"

    # Semantic aliases used by badges, banners and delegates.
    success_fg: str = "#34D399"
    success_bg: str = "#0B2923"
    warning_fg: str = "#FBBF24"
    warning_bg: str = "#2C220B"
    error_fg: str = "#FB7185"
    error_bg: str = "#30131B"
    info_fg: str = "#60A5FA"
    info_bg: str = "#0B2342"
    purple_fg: str = "#C084FC"
    purple_bg: str = "#251538"

    @classmethod
    def for_accent(cls, name: str) -> "ThemeTokens":
        """Return a token set with a different interactive accent colour.

        Semantic colours remain fixed: green is success, amber is warning,
        red is destructive, and blue is informational regardless of the
        chosen interactive accent.
        """
        accents = {
            "blue": ("#3B82F6", "#2563EB", "#1D4ED8", "#60A5FA"),
            "purple": ("#A855F7", "#9333EA", "#7E22CE", "#C084FC"),
            "green": ("#22C55E", "#16A34A", "#15803D", "#4ADE80"),
        }
        accent, hover, pressed, focus = accents.get(name, accents["blue"])
        return cls(
            accent=accent,
            accent_hover=hover,
            accent_pressed=pressed,
            focus_ring=focus,
        )

    def to_dict(self) -> dict[str, str]:
        """Return every dataclass field for QSS token substitution."""
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


def _load_qss_template() -> str:
    """Load the full QSS cascade via the ordered loader."""
    return load_qss()


def build_stylesheet(tokens: ThemeTokens, density: Density) -> str:
    qss = _load_qss_template()
    for name, value in tokens.to_dict().items():
        qss = qss.replace(f"{{{{token.{name}}}}}", value)
    for name, value in {
        "row_height": density.row_height,
        "control_height": density.control_height,
        "nav_height": density.nav_height,
        "card_padding": density.card_padding,
        "page_spacing": density.page_spacing,
    }.items():
        qss = qss.replace(f"{{{{density.{name}}}}}", str(value))

    import logging
    import re

    unresolved = re.findall(r"\{\{token\.\w+\}\}", qss)
    if unresolved:
        logging.getLogger(__name__).warning(
            "Unresolved QSS token placeholders: %s", ", ".join(set(unresolved))
        )
    return qss


def apply_theme(
    qapp,
    tokens: ThemeTokens | None = None,
    density: Density | None = None,
) -> None:
    qapp.setStyleSheet(
        build_stylesheet(tokens or ThemeTokens(), density or Density.COMPACT)
    )
