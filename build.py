#!/usr/bin/env python3
"""
Adapta — a GTK 3 / GTK 4 theme that matches whatever Omarchy theme you run.

Point it at any Omarchy theme and it rewrites the stylesheets in this directory
from that theme's colours:

    python3 build.py --omarchy ~/.config/omarchy/themes/inkypinky
    python3 build.py --omarchy ~/.config/omarchy/themes/nord
    python3 build.py --omarchy ~/.config/omarchy/themes/flexoki-light

It reads the target's `colors.toml`, resolves it into the ~25 semantic roles a
GTK stylesheet needs, and maps every colour in the template onto those roles.
The template it rewrites is `src/` — a pristine copy of Catppuccin-GnomeTheme —
so running the script twice gives the same answer every time. Nothing in
`src/` is ever modified.

Omarchy themes describe their colours in one of three shapes, and all three are
understood:

    semantic   background + lighter_background/dark_background + red/green/...
               + bright_red/bright_green/...            (nord, andromeda)
    ansi       background + color0..color15             (catppuccin-dark)
    minimal    background/foreground/accent only        (inkypinky, flexoki-light)

Anything a theme does not spell out is derived: the surface ramp is an
interpolation between its background and its foreground, and the bright
siblings of its ANSI colours are interpolations toward the foreground.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "src"

# ---------------------------------------------------------------------------
# 1. Reading the target theme
# ---------------------------------------------------------------------------
def load_theme(path: Path) -> dict[str, str]:
    """Read an Omarchy theme's colors.toml as lower-cased hex strings."""
    f = path / "colors.toml" if path.is_dir() else path
    if not f.is_file():
        sys.exit(f"error: no colors.toml at {f}")
    raw = tomllib.loads(f.read_text())
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str) and v.startswith("#"):
            out[k.lower()] = v.lower()
    if not out:
        sys.exit(f"error: {f} contains no colours")
    return out


# Keys an Omarchy theme may use. Omarchy documents this vocabulary, so a theme
# from the ecosystem will always use it -- but a hand-rolled one may not, and
# guessing silently produces a flat grey theme that looks like a real build.
KNOWN_KEYS = {
    "accent", "selection", "muted", "cursor",
    "background", "dark_background", "darker_background", "lighter_background",
    "foreground", "dark_foreground", "light_foreground", "bright_foreground",
    "red", "orange", "yellow", "green", "cyan", "blue", "magenta", "brown",
    "bright_red", "bright_orange", "bright_yellow", "bright_green",
    "bright_cyan", "bright_blue", "bright_magenta",
    "bg", "fg", "dark_bg", "darker_bg", "lighter_bg", "dark_fg", "light_fg",
    "selection_background", "selection_foreground",
} | {f"color{i}" for i in range(16)}


def check_vocabulary(theme: dict[str, str], where: Path) -> None:
    """Refuse loudly rather than build a grey theme from unrecognised keys."""
    unknown = sorted(set(theme) - KNOWN_KEYS)
    recognized = set(theme) & KNOWN_KEYS
    if not recognized:
        sys.exit(
            f"error: none of the keys in {where} are ones this build understands.\n"
            f"       found: {', '.join(sorted(theme)) or '(nothing)'}\n"
            "       expected a documented Omarchy palette -- see the theme table\n"
            "       in README.md. The build stopped rather than invent colours."
        )
    if unknown:
        print(f"note: ignored unrecognised key(s) in {where}: {', '.join(unknown)}",
              file=sys.stderr)
    # a theme with neither background nor foreground leaves resolve() on its
    # hardcoded default, which is always wrong even when the rest is present
    if not (set(theme) & {"background", "bg"}):
        print(f"note: {where} has no 'background'; using step 0 of the derived ramp",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# 2. Colour maths
# ---------------------------------------------------------------------------
def hx(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def s(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def mix(a: str, b: str, t: float) -> str:
    """Blend a->b by t. t=0 gives a, t=1 gives b."""
    x, y = hx(a), hx(b)
    return s(tuple(max(0, min(255, round(x[i] + (y[i] - x[i]) * t))) for i in range(3)))


def scale(rgb_hex: str, k: float) -> str:
    """Multiply a colour's channels. Scales down without clipping at zero."""
    return s(tuple(max(0, min(255, round(c * k))) for c in hx(rgb_hex)))


def lum(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    def f(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = lum(hx(a)), lum(hx(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable(on: str, a: str, b: str) -> str:
    """Whichever of a/b contrasts most with `on` — used for text on a fill."""
    return a if contrast(on, a) >= contrast(on, b) else b


# ---------------------------------------------------------------------------
# 3. Role resolution — from a theme's colours.toml to the roles GTK needs
# ---------------------------------------------------------------------------
def first(d: dict[str, str], *keys: str, default: str | None = None) -> str | None:
    for k in keys:
        if k in d:
            return d[k]
    return default


def resolve(t: dict[str, str]) -> dict[str, str]:
    """
    Turn a theme's colours.toml into the roles the template needs.

    Preference order matters: a theme that names its own surfaces gets them,
    and one that doesn't gets a ramp interpolated from its background and
    foreground — which also means light themes come out with correctly
    *darker* surfaces for free.
    """
    p: dict[str, str] = {}

    bg = first(t, "background", "bg", "color0") or "#c8c8c8"
    fg = first(t, "foreground", "fg", "light_foreground", "color15") or bg

    # ansi roles, or the semantic names, or a blend of the two
    p["red"]     = first(t, "red", "color1") or mix(bg, fg, 0.45)
    p["green"]   = first(t, "green", "color2") or mix(bg, fg, 0.40)
    p["yellow"]  = first(t, "yellow", "color3") or mix(bg, fg, 0.50)
    p["blue"]    = first(t, "blue", "color4") or first(t, "accent") or mix(bg, fg, 0.45)
    p["magenta"] = first(t, "magenta", "color5") or mix(bg, fg, 0.50)
    p["cyan"]    = first(t, "cyan", "color6") or mix(bg, fg, 0.45)
    p["orange"]  = first(t, "orange") or p["yellow"]

    # the ANSI brights fall back to a blend toward the foreground when the
    # theme only spells out the normal eight
    for name in ("red", "green", "yellow", "blue", "magenta", "cyan"):
        bright = first(t, "bright_" + name, "color" + str(
            {"red": 9, "green": 10, "yellow": 11, "blue": 12, "magenta": 13, "cyan": 14}[name]))
        p["bright_" + name] = bright or mix(p[name], fg, 0.35)

    p["accent"] = first(t, "accent", "blue", "color4", "selection",
                        "selection_background") or mix(bg, fg, 0.5)

    # ---- surfaces ---------------------------------------------------------
    # Deeper than the window. `darker_background`/`dark_background` when the
    # theme names them, otherwise the background scaled down multiplicatively --
    # interpolating past the foreground clips at #000000, which reads as a hole.
    p["crust"]  = first(t, "darker_background", "darker_bg") or scale(bg, 0.72)
    p["mantle"] = first(t, "dark_background", "dark_bg") or scale(bg, 0.86)
    p["bg"] = bg

    # Raised above the window. `lighter_background` names the first step; the
    # rest continue in the same direction.
    step = first(t, "lighter_background", "lighter_bg")
    direction = 1 if lum(hx(fg)) > lum(hx(bg)) else -1
    p["surf1"] = step or mix(bg, fg, direction * 0.03)
    p["surf2"] = mix(p["surf1"], fg, direction * 0.11) if step else mix(bg, fg, direction * 0.13)
    p["surf3"] = mix(p["surf2"], fg, direction * 0.13) if step else mix(bg, fg, direction * 0.27)

    # ---- foreground -------------------------------------------------------
    p["fg"] = fg
    p["dim"] = first(t, "muted", "dark_foreground", "color8") or mix(bg, fg, direction * 0.45)
    p["accent_dim"] = mix(p["accent"], bg, 0.45)
    p["on_accent"] = readable(p["accent"], bg, fg)

    # gnome-software's one light "graph paper" tile. On a dark theme it is a
    # sheet of paper, on a light theme a slightly recessed one.
    p["paper"] = mix(bg, fg, direction * 0.90)

    p["_mode"] = "dark" if lum(hx(bg)) < 0.5 else "light"
    return p


# ---------------------------------------------------------------------------
# 4. The template's palette, in semantic roles
# ---------------------------------------------------------------------------
# Every colour Catppuccin-GnomeTheme uses, mapped to the role it is playing
# there. The template is the only thing that knows these hexes; the theme being
# built supplies the values.
ROLE = {
    # surfaces
    "#1e1e2e": "bg", "#181825": "mantle", "#11111b": "crust",
    "#313244": "surf1", "#393947": "surf2", "#484856": "surf2",
    "#282938": "surf1", "#333342": "surf2", "#242434": "surf1",
    "#35353d": "surf1", "#4d4d54": "surf3", "#3d3e4c": "surf2",
    "#444556": "surf3", "#34343f": "mantle", "#292936": "mantle",
    "#222234": "mantle", "#0a0a0f": "crust", "#14141f": "mantle",
    "#060609": "crust", "#0b0b12": "crust", "#292c3c": "surf1", "#393b50": "surf2",
    "#2b2c3b": "surf1", "#3e3e5f": "surf2", "#5d5d6a": "surf3",
    "#16191a": "surf1", "#2b3133": "surf2", "#515151": "surf3",
    "#292929": "surf1", "#424242": "surf3", "#202020": "mantle",
    "#1e1e1e": "mantle", "#191919": "mantle", "#605955": "surf3",
    "#6e6d71": "surf3",
    # text
    "#eff1f5": "fg", "#a6adc8": "dim", "#b0b2b9": "dim", "#c0bfbc": "dim",
    "#9a9996": "dim", "#77767b": "dim", "#d8d7d3": "dim", "#e5d6ca": "fg",
    # accent
    "#eba0ac": "accent", "#875e69": "accent_dim", "#5e5c64": "accent_dim",
    "#ce8cd7": "magenta", "#2861c6": "accent", "#eb5ec3": "magenta",
    "#6d53e0": "accent", "#393484": "bg", "#ef4e9b": "magenta", "#f77466": "red",
    # status
    "#f38ba8": "red", "#f9e2af": "yellow", "#f9e2a7": "bright_yellow",
    "#a6e3a1": "green", "#fab387": "bright_yellow", "#89b4fa": "bright_blue",
    "#cba6f7": "magenta", "#89dceb": "cyan", "#c01c28": "red",
    # lighter / darker siblings of the status colours
    "#f4cad1": "bright_red", "#e78b9a": "bright_red", "#f2a5bb": "bright_red",
    "#f0b9c2": "bright_red", "#efb5be": "bright_red", "#ecacb7": "bright_red",
    "#f7e6c1": "bright_red", "#f17497": "red", "#f7d997": "yellow",
    "#f5cd76": "bright_yellow", "#ed547e": "yellow", "#b8e7b6": "bright_green",
    "#79d572": "green", "#6ed166": "green", "#f4c868": "bright_yellow",
    "#f5d180": "bright_yellow", "#ec4674": "red", "#ae7b03": "yellow",
    "#cd9309": "yellow", "#d61f2d": "red", "#a11722": "red",
    # libadwaita blues
    "#337fdc": "bright_blue", "#1c71d8": "accent", "#0b2e52": "mantle",
    "#1862af": "accent", "#0f9ac8": "cyan", "#29ae74": "green",
    "#27a66c": "green", "#333": "surf3",
    # gnome-software's light "graph paper" tile
    "#fdf8d7": "paper", "#fefcef": "paper", "#fcf4bf": "paper",
}

# The same, for the rgb()/rgba() triplets in the stylesheets. rgb() is mapped
# to plain hex (the alpha, if any, is preserved by alpha()).
RGB_ROLE = {
    "239,241,245": "fg", "225,229,233": "fg", "178,191,203": "fg",
    "201,216,232": "fg", "163,178,193": "fg", "233,242,250": "fg",
    "17,17,27": "on_accent", "21,22,24": "on_accent", "30,30,46": "on_accent",
    "235,160,172": "accent", "221,151,163": "accent", "181,125,136": "accent",
    "187,109,133": "accent",
    "243,139,168": "red", "249,226,175": "yellow", "166,227,161": "green",
    "137,180,250": "bright_blue", "250,179,135": "bright_yellow",
    "205,147,9": "yellow", "192,28,40": "red",
    "49,50,68": "surf1", "33,35,37": "surf1", "33,33,50": "surf1",
    "35,38,40": "surf1", "35,50,66": "surf1", "19,56,93": "surf1",
    "48,48,48": "surf2", "47,51,54": "surf2",
    "57,63,70": "surf3", "56,63,71": "surf3", "78,89,99": "surf3",
    "60,60,66": "surf3", "38,66,94": "surf3",
    "163,209,254": "bright_cyan", "140,191,242": "bright_cyan",
    "111,173,235": "bright_blue", "65,140,214": "accent",
    "7,25,43": "mantle", "11,11,18": "crust",
}

# Absolute black and white are infrastructure, not palette. Left alone.
KEEP_HEX = {"#000", "#000000", "#ffffff", "#fff"}
KEEP_RGB = {"0,0,0", "255,255,255", "255,0,0"}

# Tonal ramp families, and which theme role each is drawn from.
RAMP_FAMILY = {
    "STRAWBERRY": "red", "red": "red",
    "ORANGE": "orange", "orange": "orange",
    "BANANA": "yellow", "yellow": "yellow",
    "LIME": "green", "green": "green",
    "BLUEBERRY": "blue", "blue": "blue",
    "GRAPE": "magenta", "purple": "magenta",
    "COCOA": "magenta", "brown": "magenta",
    "SILVER": "silver", "light": "light", "SLATE": "slate", "BLACK": "slate",
    "dark": "dark",
}


# ---------------------------------------------------------------------------
# 5. Deriving the ramps and avatars from the resolved palette
# ---------------------------------------------------------------------------
def ramp_map(root: Path, p: dict[str, str]) -> dict[str, str]:
    """
    Rebuild each legacy tonal ramp (`blue_1..blue_5`, `STRAWBERRY_100..`).

    Each family gets two anchors drawn from the theme's own colour for that
    family: a dark end blended toward the background and a light end blended
    toward the foreground. The template's stops are then reassigned in their
    original luminance order, so a ramp that ran light->dark still does.
    """
    found: dict[str, set[str]] = {n: set() for n in RAMP_FAMILY}
    pat = re.compile(
        r"@define-color\s+(" + "|".join(sorted(RAMP_FAMILY)) + r")_([0-9]+)\s+(#[0-9a-fA-F]{3,8})\b"
    )
    for f in root.rglob("*.css"):
        for m in pat.finditer(f.read_text()):
            found[m.group(1)].add(m.group(3).lower())

    out: dict[str, str] = {}
    for family, colors in found.items():
        if not colors:
            continue
        hue = RAMP_FAMILY[family]
        if hue == "silver":
            dark, light = p["surf2"], p["fg"]
        elif hue == "slate":
            dark, light = p["crust"], p["dim"]
        elif hue == "dark":
            # gnome-software prints white text on `dark_2`, so this ramp stops
            # well short of the light end
            dark, light = p["crust"], mix(p["bg"], p["fg"], 0.28)
        elif hue == "light":
            dark, light = p["surf2"], p["fg"]
        else:
            dark, light = mix(p[hue], p["bg"], 0.60), mix(p[hue], p["fg"], 0.45)

        lums = {c: lum(hx(c)) for c in colors}
        ordered = sorted(colors, key=lambda c: lums[c])   # dark -> light
        n = len(ordered)
        for i, c in enumerate(ordered):
            out[c] = light if i == n - 1 else mix(dark, light, i / (n - 1))
    return out


AVATAR_DECL = re.compile(
    r"avatar\.color\d+\s*\{\s*background-image:\s*linear-gradient\(\s*"
    r"(#[0-9a-fA-F]{3,8})\s*,\s*(#[0-9a-fA-F]{3,8})\s*\)\s*;"
    r"\s*color:\s*(#[0-9a-fA-F]{3,8})\s*;", re.S
)


def avatar_map(root: Path, p: dict[str, str]) -> dict[str, str]:
    """
    Rebuild the 14 libadwaita `avatar.colorN` gradients by cycling the theme's
    own hues, so the grid reads as the theme rather than as the stock rainbow.
    """
    text = "\n".join(f.read_text() for f in sorted(root.rglob("*.css")))
    seen: dict[tuple[str, str, str], None] = {}
    for m in AVATAR_DECL.finditer(text):
        seen.setdefault(tuple(c.lower() for c in m.groups()), None)
    decls = list(seen)

    hues = [p["red"], p["yellow"], p["green"], p["cyan"], p["blue"], p["magenta"]]
    pairs = []
    for i in range(len(decls)):
        h = hues[i % len(hues)]
        if i % 2:      # vary how far the two stops are pushed
            pairs.append((mix(h, p["fg"], 0.50), mix(h, p["bg"], 0.30)))
        else:
            pairs.append((mix(h, p["fg"], 0.22), h))

    out: dict[str, str] = {}
    for (a, b, c), (light, dark) in zip(decls, pairs):
        out[a.lower()] = light
        out[b.lower()] = dark
        out[c.lower()] = readable(light, p["bg"], p["fg"])
    return out


# ---------------------------------------------------------------------------
# 6. The rewrite engine
# ---------------------------------------------------------------------------
# A CSS hex colour needs a delimiter before the '#' -- `colorswatch#add-color-button`
# is an ID selector, and without the lookbehind the regex eats the `#add`.
HEX_RE = re.compile(r"(?<![A-Za-z0-9_\-])#[0-9a-fA-F]{3,8}\b")
RGB_RE = re.compile(
    r"rgba?\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)((?:\s*,\s*[0-9.]+)?)\s*\)"
)


def rewrite(body: str, lut: dict[str, str], rgb_lut: dict[str, str]) -> str:
    def _rgb(m: re.Match) -> str:
        key = f"{m.group(1)},{m.group(2)},{m.group(3)}"
        if key in KEEP_RGB:
            return m.group(0)
        # rgb_lut is {triplet: resolved_hex}, so unlike the hex table below it
        # needs no second hop through a role.
        new = rgb_lut.get(key)
        if new is None:
            return m.group(0)
        alpha = (m.group(4) or "").replace(" ", "")
        # GTK's rgba() takes three *numbers*; a hex literal inside it is a parse
        # error. alpha(colour, factor) is the idiom.
        if alpha:
            return f"alpha({new},{alpha.lstrip(',')})"
        return new

    # rgb() first, so the alpha() it produces is never re-scanned as a hex.
    body = RGB_RE.sub(_rgb, body)

    def _hex(m: re.Match) -> str:
        original = m.group(0)
        low = original.lower()
        if low in KEEP_HEX:
            return original
        new = lut.get(low)
        if new is None:
            return original
        return new if original.islower() else new.upper()

    return HEX_RE.sub(_hex, body)


INDEX_THEME = """Type=X-GNOME-Metatheme
[Desktop Entry]
Name={name}
Comment=GTK theme matching the active Omarchy theme
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme={name}
MetacityTheme={name}
IconTheme=Papirus-Dark
CursorTheme=default
ButtonLayout=close,minimize,maximize:menu
"""


# ---------------------------------------------------------------------------
# 7. Building
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
def omarchy_css(p: dict[str, str]) -> str:
    """
    The `@define-color` block Omarchy's own thpm plugin writes, generated from
    the resolved palette. Supplies GTK 4 the named colours (`background`,
    `accent_bg_color`, ...) that GTK 3 already gets from
    ~/.config/gtk-3.0/gtk.css when the target theme ships no gtk.css of its own.
    """
    rows = [
        ("background", p["bg"]), ("foreground", p["fg"]),
        ("black", p["crust"]), ("red", p["red"]), ("green", p["green"]),
        ("yellow", p["yellow"]), ("blue", p["blue"]), ("magenta", p["magenta"]),
        ("cyan", p["cyan"]), ("white", p["dim"]),
        ("bright_black", p["surf3"]), ("bright_red", p["bright_red"]),
        ("bright_green", p["bright_green"]), ("bright_yellow", p["bright_yellow"]),
        ("bright_blue", p["bright_blue"]), ("bright_magenta", p["bright_magenta"]),
        ("bright_cyan", p["bright_cyan"]), ("bright_white", p["fg"]),
        ("accent_bg_color", p["accent"]), ("accent_fg_color", p["on_accent"]),
        ("accent_color", p["cyan"]),
        ("window_bg_color", p["bg"]), ("window_fg_color", p["fg"]),
        ("view_bg_color", p["surf1"]), ("view_fg_color", p["fg"]),
        ("sidebar_bg_color", p["surf1"]), ("sidebar_fg_color", p["fg"]),
        ("headerbar_bg_color", p["mantle"]), ("headerbar_fg_color", p["fg"]),
        ("card_bg_color", p["surf1"]), ("card_fg_color", p["fg"]),
        ("popover_bg_color", p["surf1"]), ("popover_fg_color", p["fg"]),
        ("dialog_bg_color", p["mantle"]), ("dialog_fg_color", p["fg"]),
        ("destructive_bg_color", p["red"]), ("destructive_fg_color", p["on_accent"]),
        ("success_bg_color", p["green"]), ("success_fg_color", p["on_accent"]),
        ("warning_bg_color", p["yellow"]), ("warning_fg_color", p["on_accent"]),
        ("error_bg_color", p["red"]), ("error_fg_color", p["on_accent"]),
        ("theme_bg_color", p["bg"]), ("theme_fg_color", p["fg"]),
        ("theme_base_color", p["surf1"]),
        ("theme_selected_bg_color", p["accent"]),
        ("theme_selected_fg_color", p["on_accent"]),
    ]
    body = "\n".join(f"    @define-color {n:<26} {v};" for n, v in rows)
    return (
        "/* Generated by adapta/build.py -- do not edit by hand.\n"
        "   Satisfies @import url(\"thpm-theme.css\") in gtk-4.0/gtk.css. */\n"
        + body + "\n"
    )


def ship_thpm(dst_root: Path, omarchy: Path, p: dict[str, str]) -> str:
    """
    Make the template's `@import url("thpm-theme.css")` resolve. GTK resolves it
    relative to the *theme* directory, so the file has to live here -- not in
    ~/.config/gtk-4.0/, which is where Omarchy's own thpm plugin puts its copy
    and therefore never gets consulted for a theme in ~/.themes.

    Use the theme's own gtk.css when it has one; otherwise generate the block
    above from the resolved palette.
    """
    src = omarchy / "gtk.css" if omarchy.is_dir() else None
    if src is not None and src.is_file():
        shutil.copyfile(src, dst_root / "gtk-4.0" / "thpm-theme.css")
        return f"{src} -> gtk-4.0/thpm-theme.css"
    (dst_root / "gtk-4.0" / "thpm-theme.css").write_text(omarchy_css(p))
    return "generated from the palette -> gtk-4.0/thpm-theme.css"


def build(omarchy: Path, name: str | None, dry: bool) -> dict[str, str]:
    if not TEMPLATE.is_dir():
        sys.exit(f"error: template directory {TEMPLATE} is missing")

    # The template must still be the stock Catppuccin palette. If it is not, it
    # has been recoloured and every build from here on would be lossy.
    probe = TEMPLATE / "gtk-4.0/gtk.css"
    if probe.is_file() and "#1e1e2e" not in probe.read_text():
        sys.exit(
            "error: src/ no longer looks like Catppuccin-GnomeTheme\n"
            "       (#1e1e2e is missing from src/gtk-4.0/gtk.css).\n"
            "       Restore src/ before building -- this script never writes to\n"
            "       it, so something else did."
        )

    theme = load_theme(omarchy)
    check_vocabulary(theme, omarchy)
    p = resolve(theme)
    name = name or ROOT.name

    lut = {src: p[role] for src, role in ROLE.items() if role in p}
    lut.update(ramp_map(TEMPLATE, p))
    lut.update(avatar_map(TEMPLATE, p))
    rgb_lut = {k: p[v] for k, v in RGB_ROLE.items()}

    missing = sorted(ROLE.keys() - set(lut.keys()))
    if dry:
        print(f"theme        : {omarchy}")
        print(f"mode         : {p['_mode']}")
        print(f"theme name   : {name}")
        print(f"mapped hexes : {len(lut)}")
        print(f"unresolved   : {len(missing)}")
        print("\nroles:")
        for r in sorted(k for k in p if not k.startswith("_")):
            print(f"  {r:12} {p[r]}")
        return p

    for sub in ("gtk-3.0", "gtk-4.0", "gnome-shell"):
        dst = ROOT / sub
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(TEMPLATE / sub, dst)

    # Only the freshly copied directories are ever rewritten. Walking ROOT
    # instead would find this script itself -- `build.py` is full of template
    # hex literals, and recolouring them would turn the ROLE table back into
    # the theme it was last pointed at.
    for path in sorted(p for sub in ("gtk-3.0", "gtk-4.0", "gnome-shell")
                       for p in (ROOT / sub).rglob("*") if p.is_file()):
        try:
            body = path.read_text()
        except UnicodeDecodeError:
            continue          # real binary asset
        out = rewrite(body, lut, rgb_lut)
        if out != body:
            path.write_text(out)

    (ROOT / "index.theme").write_text(INDEX_THEME.format(name=name))
    print(f"omarchy CSS : {ship_thpm(ROOT, omarchy, p)}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the Adapta GTK theme from an Omarchy theme's colours.")
    ap.add_argument("--omarchy", required=True,
                    help="path to an Omarchy theme dir, or its colors.toml")
    ap.add_argument("--name", help="GTK theme name (default: this directory's name)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and print the palette, write nothing")
    args = ap.parse_args()

    p = build(Path(args.omarchy).expanduser(), args.name, args.dry_run)
    if not args.dry_run:
        print(f"built {ROOT.name} from {p['_mode']} theme")
        print(f"\napply with:\n  gsettings set org.gnome.desktop.interface gtk-theme '{ROOT.name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
