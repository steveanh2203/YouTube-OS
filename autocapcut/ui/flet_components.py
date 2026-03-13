"""Reusable UI primitives for the Flet desktop shell."""
from __future__ import annotations

from typing import Any

import flet as ft

from autocapcut.ui.flet_theme import TOKENS, card_shadow, text_style


def app_text(value: str, role: str = "body", *, color: str | None = None, weight: ft.FontWeight | None = None) -> ft.Text:
    return ft.Text(value, style=text_style(role, color=color, weight=weight))


def section_header(
    title: str,
    subtitle: str | None = None,
    *,
    actions: list[ft.Control] | None = None,
    eyebrow: str | None = None,
    compact: bool = False,
) -> ft.Control:
    heading_controls: list[ft.Control] = []
    if eyebrow:
        heading_controls.append(app_text(eyebrow, "utility", color=TOKENS.text_muted))
    heading_controls.append(app_text(title, "section_title"))
    if subtitle:
        heading_controls.append(app_text(subtitle, "body", color=TOKENS.text_secondary))
    return ft.Row(
        [
            ft.Column(
                heading_controls,
                spacing=TOKENS.gap_1 if compact else TOKENS.gap_2,
                tight=True,
            ),
            ft.Container(expand=True),
            ft.Row(actions or [], spacing=TOKENS.gap_2, wrap=True),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def topbar(title: str, workspace_label: str) -> ft.Control:
    return ft.Container(
        height=TOKENS.topbar_height,
        bgcolor=TOKENS.surface,
        border=ft.border.all(1, TOKENS.border_subtle),
        border_radius=TOKENS.radius_lg,
        padding=ft.padding.symmetric(horizontal=TOKENS.gap_5),
        shadow=card_shadow(),
        content=ft.Row(
            [
                ft.Column(
                    [
                        app_text("AutoCapCut", "label", color=TOKENS.text_muted),
                        app_text(title, "title"),
                    ],
                    spacing=0,
                    tight=True,
                ),
                ft.Container(expand=True),
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=12, vertical=8),
                    border_radius=TOKENS.radius_sm,
                    bgcolor=TOKENS.surface_tint,
                    border=ft.border.all(1, TOKENS.border_subtle),
                    content=app_text(workspace_label, "small", color=TOKENS.text_secondary, weight=ft.FontWeight.W_500),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def app_card(
    content: ft.Control,
    *,
    expand: bool | int = False,
    padding: int = TOKENS.gap_5,
    bgcolor: str | None = None,
) -> ft.Control:
    return ft.Container(
        expand=expand,
        bgcolor=bgcolor or TOKENS.surface,
        border=ft.border.all(1, TOKENS.border_subtle),
        border_radius=TOKENS.radius_xl,
        padding=padding,
        shadow=card_shadow(),
        content=content,
    )


def primary_button(label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
    return ft.FilledButton(
        content=ft.Row(
            [ft.Icon(icon, size=16, color=TOKENS.text_inverse), app_text(label, "label", color=TOKENS.text_inverse, weight=ft.FontWeight.W_600)]
            if icon
            else [app_text(label, "label", color=TOKENS.text_inverse, weight=ft.FontWeight.W_600)],
            tight=True,
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        on_click=on_click,
        expand=expand,
        height=TOKENS.action_height,
        style=ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: TOKENS.primary,
                ft.ControlState.HOVERED: TOKENS.primary_hover,
                ft.ControlState.PRESSED: TOKENS.primary_active,
            },
            color=TOKENS.text_inverse,
            padding=ft.padding.symmetric(horizontal=18, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=TOKENS.radius_md),
            elevation={ft.ControlState.DEFAULT: 0, ft.ControlState.HOVERED: 0, ft.ControlState.PRESSED: 0},
        ),
    )


def secondary_button(label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
    return ft.OutlinedButton(
        content=ft.Row(
            [ft.Icon(icon, size=16, color=TOKENS.text_secondary), app_text(label, "label", color=TOKENS.text_primary, weight=ft.FontWeight.W_600)]
            if icon
            else [app_text(label, "label", color=TOKENS.text_primary, weight=ft.FontWeight.W_600)],
            tight=True,
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        on_click=on_click,
        expand=expand,
        height=TOKENS.action_height,
        style=ft.ButtonStyle(
            bgcolor=TOKENS.surface,
            side=ft.BorderSide(1, TOKENS.border_default),
            padding=ft.padding.symmetric(horizontal=18, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=TOKENS.radius_md),
        ),
    )


def ghost_button(label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
    return ft.TextButton(
        content=ft.Row(
            [ft.Icon(icon, size=16, color=TOKENS.text_secondary), app_text(label, "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)]
            if icon
            else [app_text(label, "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)],
            tight=True,
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={ft.ControlState.HOVERED: TOKENS.surface_muted},
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            shape=ft.RoundedRectangleBorder(radius=TOKENS.radius_md),
        ),
    )


def danger_button(label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
    return ft.OutlinedButton(
        content=ft.Row(
            [ft.Icon(icon, size=16, color=TOKENS.danger), app_text(label, "label", color=TOKENS.danger, weight=ft.FontWeight.W_600)]
            if icon
            else [app_text(label, "label", color=TOKENS.danger, weight=ft.FontWeight.W_600)],
            tight=True,
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        on_click=on_click,
        expand=expand,
        height=TOKENS.action_height,
        style=ft.ButtonStyle(
            bgcolor=TOKENS.surface,
            side=ft.BorderSide(1, "#F3C0C0"),
            padding=ft.padding.symmetric(horizontal=18, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=TOKENS.radius_md),
        ),
    )


def sidebar_item(label: str, *, active: bool, on_click: Any) -> ft.Control:
    return ft.Container(
        border_radius=TOKENS.radius_md,
        bgcolor=TOKENS.surface_tint if active else None,
        border=ft.border.all(1, TOKENS.primary_tint if active else ft.Colors.TRANSPARENT),
        padding=ft.padding.symmetric(horizontal=16, vertical=12),
        ink=True,
        on_click=on_click,
        content=app_text(
            label,
            "body",
            color=TOKENS.text_primary,
            weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_500,
        ),
    )


def status_badge(value: str, tone: str = "neutral") -> ft.Control:
    palette = {
        "neutral": (TOKENS.surface_muted, TOKENS.text_secondary, TOKENS.border_subtle),
        "pending": ("#F8FAFC", TOKENS.text_secondary, TOKENS.border_subtle),
        "processing": (TOKENS.surface_tint, TOKENS.primary, "#BFDBFE"),
        "done": (TOKENS.success_tint, TOKENS.success, "#BBF7D0"),
        "failed": (TOKENS.danger_tint, TOKENS.danger, "#FECACA"),
    }
    bg, fg, border = palette.get(tone, palette["neutral"])
    return ft.Container(
        padding=ft.padding.symmetric(horizontal=10, vertical=6),
        border_radius=999,
        bgcolor=bg,
        border=ft.border.all(1, border),
        content=app_text(value, "caption", color=fg, weight=ft.FontWeight.W_600),
    )


def app_input(
    *,
    label: str,
    value: str,
    on_change: Any,
    hint_text: str | None = None,
    autofocus: bool = False,
) -> ft.Control:
    return ft.TextField(
        label=label,
        value=value,
        on_change=on_change,
        hint_text=hint_text,
        autofocus=autofocus,
        border_radius=TOKENS.radius_md,
        border=TOKENS_BORDER,
        border_width=1,
        border_color=TOKENS.border_default,
        focused_border_width=1,
        focused_border_color=TOKENS.border_focus,
        text_style=text_style("body"),
        label_style=text_style("label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_500),
        hint_style=text_style("body", color=TOKENS.text_muted),
        content_padding=ft.padding.symmetric(horizontal=16, vertical=14),
        bgcolor=TOKENS.surface,
        dense=True,
    )


TOKENS_BORDER = ft.InputBorder.OUTLINE


def app_textarea(
    *,
    label: str,
    value: str,
    on_change: Any,
    min_lines: int = 6,
    max_lines: int = 8,
    hint_text: str | None = None,
) -> ft.Control:
    return ft.TextField(
        label=label,
        value=value,
        on_change=on_change,
        multiline=True,
        min_lines=min_lines,
        max_lines=max_lines,
        hint_text=hint_text,
        border_radius=TOKENS.radius_lg,
        border=TOKENS_BORDER,
        border_width=1,
        border_color=TOKENS.border_default,
        focused_border_width=1,
        focused_border_color=TOKENS.border_focus,
        text_style=text_style("body"),
        label_style=text_style("label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_500),
        hint_style=text_style("body", color=TOKENS.text_muted),
        content_padding=ft.padding.symmetric(horizontal=16, vertical=16),
        bgcolor=TOKENS.surface,
    )


def app_dropdown(
    *,
    value: str | None,
    options: list[ft.dropdown.Option],
    on_select: Any,
) -> ft.Control:
    return ft.Dropdown(
        value=value,
        options=options,
        dense=True,
        border_radius=TOKENS.radius_md,
        border_color=TOKENS.border_default,
        focused_border_color=TOKENS.border_focus,
        content_padding=12,
        text_style=text_style("small"),
        on_select=on_select,
        bgcolor=TOKENS.surface,
    )
