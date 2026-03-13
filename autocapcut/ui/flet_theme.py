"""Shared design tokens and page theme for the Flet desktop UI."""
from __future__ import annotations

from dataclasses import dataclass

import flet as ft


@dataclass(frozen=True)
class AppTokens:
    canvas: str = "#F8FAFC"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#FCFDFE"
    surface_muted: str = "#F1F5F9"
    surface_tint: str = "#EFF6FF"

    text_primary: str = "#0F172A"
    text_secondary: str = "#334155"
    text_muted: str = "#64748B"
    text_inverse: str = "#F8FAFC"

    border_subtle: str = "#E2E8F0"
    border_default: str = "#CBD5E1"
    border_strong: str = "#94A3B8"
    border_focus: str = "#2563EB"

    primary: str = "#2563EB"
    primary_hover: str = "#1D4ED8"
    primary_active: str = "#1E40AF"
    primary_tint: str = "#DBEAFE"

    success: str = "#16A34A"
    success_tint: str = "#DCFCE7"
    warning: str = "#D97706"
    warning_tint: str = "#FEF3C7"
    danger: str = "#DC2626"
    danger_tint: str = "#FEF2F2"

    sidebar_width: int = 264
    topbar_height: int = 56
    action_height: int = 42
    input_height: int = 46

    radius_sm: int = 12
    radius_md: int = 16
    radius_lg: int = 20
    radius_xl: int = 24

    gap_1: int = 4
    gap_2: int = 8
    gap_3: int = 12
    gap_4: int = 16
    gap_5: int = 20
    gap_6: int = 24
    gap_8: int = 32

    font_display: str = "Geist"
    font_body: str = "SF Pro Text"
    font_fallback: tuple[str, ...] = ("Inter", "Helvetica Neue", "Arial", "sans-serif")


TOKENS = AppTokens()


def text_style(
    role: str,
    *,
    color: str | None = None,
    weight: ft.FontWeight | None = None,
    family: str | None = None,
) -> ft.TextStyle:
    mapping = {
        "page_title": {"size": 40, "height": 1.2, "weight": ft.FontWeight.W_700, "family": TOKENS.font_display},
        "section_title": {"size": 24, "height": 1.33, "weight": ft.FontWeight.W_700, "family": TOKENS.font_display},
        "card_title": {"size": 20, "height": 1.35, "weight": ft.FontWeight.W_700, "family": TOKENS.font_display},
        "title": {"size": 18, "height": 1.4, "weight": ft.FontWeight.W_600, "family": TOKENS.font_body},
        "body": {"size": 16, "height": 1.5, "weight": ft.FontWeight.W_400, "family": TOKENS.font_body},
        "small": {"size": 14, "height": 1.43, "weight": ft.FontWeight.W_400, "family": TOKENS.font_body},
        "label": {"size": 14, "height": 1.43, "weight": ft.FontWeight.W_500, "family": TOKENS.font_body},
        "caption": {"size": 12, "height": 1.33, "weight": ft.FontWeight.W_500, "family": TOKENS.font_body},
        "utility": {"size": 12, "height": 1.33, "weight": ft.FontWeight.W_600, "family": TOKENS.font_body},
    }
    base = mapping[role]
    return ft.TextStyle(
        size=base["size"],
        height=base["height"],
        weight=weight or base["weight"],
        color=color or TOKENS.text_primary,
        font_family=family or base["family"],
        font_family_fallback=list(TOKENS.font_fallback),
    )


def page_padding(horizontal: int = TOKENS.gap_6, vertical: int = TOKENS.gap_6) -> ft.Padding:
    return ft.padding.symmetric(horizontal=horizontal, vertical=vertical)


def card_shadow() -> list[ft.BoxShadow]:
    return [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=12,
            color=ft.Colors.with_opacity(0.05, "#0F172A"),
            offset=ft.Offset(0, 2),
        )
    ]


def configure_page(page: ft.Page) -> None:
    page.fonts = {
        TOKENS.font_display: "fonts/SpaceGrotesk.ttf",
    }
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = TOKENS.canvas
    page.theme = ft.Theme(
        font_family=TOKENS.font_body,
        use_material3=True,
        color_scheme_seed=TOKENS.primary,
        scaffold_bgcolor=TOKENS.canvas,
        card_bgcolor=TOKENS.surface,
        divider_color=TOKENS.border_subtle,
    )
