"""Semantic theme tokens and stylesheet builder for Minerva."""

from __future__ import annotations

from dataclasses import dataclass

from minerva.ui.density import Density
from minerva.ui.qss.loader import load_qss


@dataclass(frozen=True)
class ThemeTokens:
    # Core surfaces
    background: str = "#0B1220"
    surface: str = "#111827"
    surface_raised: str = "#1B2638"
    border: str = "#263244"
    text: str = "#E5E7EB"
    text_muted: str = "#8B98AA"
    disabled_text: str = "#475569"
    scrollbar: str = "#334155"

    # Fonts
    heading_font: str = "Russo One"
    body_font: str = "Chakra Petch"

    # Shape tokens
    radius_sm: str = "4px"
    radius_md: str = "8px"
    radius_lg: str = "12px"

    # Semantic accents
    accent: str = "#22C55E"
    accent_hover: str = "#16A34A"
    success: str = "#22C55E"
    warning: str = "#F0AD4E"
    error: str = "#EF4444"
    purple: str = "#A855F7"

    # Semantic surfaces (pill backgrounds)
    success_surface: str = "#0D2818"
    success_border: str = "#1A4D2E"
    warning_surface: str = "#2D2410"
    warning_border: str = "#5C4819"
    error_surface: str = "#2D1014"
    error_border: str = "#5C1F29"
    info_surface: str = "#0D2840"
    info_border: str = "#1A4D6E"
    purple_surface: str = "#1F1430"
    purple_border: str = "#4A2D6E"

    # Semantic surface aliases (fg/bg/border naming) — required by
    # StatusBadge, MetricCard, Banner, Pill semantics.
    success_fg: str = "#22C55E"
    success_bg: str = "#0D2818"
    warning_fg: str = "#F0AD4E"
    warning_bg: str = "#2D2410"
    error_fg: str = "#EF4444"
    error_bg: str = "#2D1014"
    info_fg: str = "#3B82F6"
    info_bg: str = "#0D2840"
    purple_fg: str = "#A855F7"
    purple_bg: str = "#1F1430"

    @classmethod
    def for_accent(cls, name: str) -> "ThemeTokens":
        """Return a copy with a different interactive accent colour.

        Only ``accent`` and ``accent_hover`` change.  Semantic colours
        (``success``, ``warning``, ``error``, ``info_fg``, ``purple``) are
        intentionally fixed regardless of the chosen accent — green always
        means success, blue always means info, etc.  This follows universal
        colour conventions so users never have to learn a custom mapping.
        """
        accents = {
            "blue": "#3B82F6",
            "purple": "#A855F7",
            "green": "#22C55E",
        }
        return cls(accent=accents.get(name, accents["green"]))

    def to_dict(self) -> dict[str, str]:
        return {
            "background": self.background,
            "surface": self.surface,
            "surface_raised": self.surface_raised,
            "border": self.border,
            "text": self.text,
            "text_muted": self.text_muted,
            "disabled_text": self.disabled_text,
            "scrollbar": self.scrollbar,
            "heading_font": self.heading_font,
            "body_font": self.body_font,
            "radius_sm": self.radius_sm,
            "radius_md": self.radius_md,
            "radius_lg": self.radius_lg,
            "accent": self.accent,
            "accent_hover": self.accent_hover,
            "success": self.success,
            "warning": self.warning,
            "error": self.error,
            "purple": self.purple,
            "success_surface": self.success_surface,
            "success_border": self.success_border,
            "warning_surface": self.warning_surface,
            "warning_border": self.warning_border,
            "error_surface": self.error_surface,
            "error_border": self.error_border,
            "info_surface": self.info_surface,
            "info_border": self.info_border,
            "purple_surface": self.purple_surface,
            "purple_border": self.purple_border,
            # New semantic aliases
            "success_fg": self.success_fg,
            "success_bg": self.success_bg,
            "warning_fg": self.warning_fg,
            "warning_bg": self.warning_bg,
            "error_fg": self.error_fg,
            "error_bg": self.error_bg,
            "info_fg": self.info_fg,
            "info_bg": self.info_bg,
            "purple_fg": self.purple_fg,
            "purple_bg": self.purple_bg,
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
    import re
    unresolved = re.findall(r"\{\{token\.\w+\}\}", qss)
    if unresolved:
        import logging
        logging.getLogger(__name__).warning(
            "Unresolved QSS token placeholders: %s", ", ".join(set(unresolved))
        )
    return qss


def apply_theme(qapp, tokens: ThemeTokens | None = None, density: Density | None = None) -> None:
    qapp.setStyleSheet(build_stylesheet(tokens or ThemeTokens(), density or Density.COMPACT))
