# Adapta

**A GTK 3.0 and GTK 4.0 theme that takes its colours from whatever Omarchy theme you're running.**

Install it once — then when you change Omarchy themes, retint it with one command:

```bash
python3 build.py --omarchy ~/.config/omarchy/themes/inkypinky
```

It reads the target theme's `colors.toml`, resolves that into the palette a GTK stylesheet actually needs, and rewrites `gtk-3.0/`, `gtk-4.0/` and `gnome-shell/` in place. No Omarchy theme installed is fine too — any `colors.toml` works.

```bash
git clone https://github.com/signaldirective/Adapta.git ~/.themes/Adapta
cd ~/.themes/Adapta
python3 build.py --omarchy ~/.config/omarchy/themes/<your-omarchy-theme>
gsettings set org.gnome.desktop.interface gtk-theme 'Adapta'
```

The directory name is the theme name, so clone it wherever you like and it follows. `--name` overrides it if the two ever need to differ.

Both `gtk.css` and `gtk-dark.css` ship and are identical — GTK picks one based on whether it thinks you want a light or dark window, and Adapta has no light variant, so the duplicate exists to keep GTK from falling back to Adwaita.

### If your GTK 4 apps don't change

Omarchy keeps a stylesheet at `~/.config/gtk-4.0/`, loaded *in addition to* your theme, which means it wins over it. If you have one there, point it at Adapta:

```bash
ln -sf ~/.themes/Adapta/gtk-4.0/gtk.css      ~/.config/gtk-4.0/gtk.css
ln -sf ~/.themes/Adapta/gtk-4.0/gtk-dark.css ~/.config/gtk-4.0/gtk-dark.css
ln -sf ~/.themes/Adapta/gtk-4.0/assets       ~/.config/gtk-4.0/assets
```

## Switching themes

```bash
python3 build.py --omarchy ~/.config/omarchy/themes/nord
python3 build.py --omarchy ~/.config/omarchy/themes/flexoki-light
```

It's idempotent — every build starts from `src/`, a pristine copy of the template that's never modified. Run it twice and you get the same bytes back.

## How it works

`src/` holds the stock stylesheets, a template descended from
[Catppuccin-GnomeTheme](https://github.com/Fausto-Korpsvart/Catppuccin-GnomeTheme)
by Fausto Korpsvart. Every colour in it is mapped to a *role*: that hex is playing "the accent", that one "a raised surface", that one "error text". The theme you point at supplies the value for each role, and the rewrite is a plain substitution.

The interesting half is `resolve()`, which turns a `colors.toml` into those roles. Omarchy documents a fixed vocabulary of key names, and themes use that vocabulary — just not always the same keys. All three shapes are understood:

| Shape | Keys | Example |
| --- | --- | --- |
| semantic | `lighter_background`, `red`, `bright_green`, … | nord |
| ansi | `color0` … `color15` | catppuccin-dark |
| minimal | `background`, `foreground`, `accent` | inkypinky |

Anything a theme doesn't spell out is *derived*, not guessed:

- **The surface ramp** interpolates between the theme's background and its foreground. Because it follows the foreground, light themes come out with correctly *darker* surfaces for free — flexoki-light gets `#dbd9ce` headerbars on `#fffcf0` windows, while dark themes get raised surfaces.
- **The bright siblings** of the ANSI colours interpolate toward the foreground when the theme only names the normal eight.
- **The deepest surfaces** scale the background multiplicatively, because interpolating *past* the foreground clips at `#000000` and reads as a hole.
- **Text on a fill** picks whichever of background/foreground contrasts most against the accent, so a blue accent gets white text and a lavender accent gets ink text without being told which. `selection` and `selection_background` are consulted as accent fallbacks too.

The legacy tonal ramps (`blue_1..blue_5`, `STRAWBERRY_100..`, `light_1..`) and the 14 libadwaita `avatar.colorN` gradients are rebuilt from the resolved palette as well — ramps by hue family preserving their original luminance order, avatars by cycling the theme's own hues.

If a `colors.toml` uses key names that aren't part of that vocabulary, the build **stops and tells you** rather than silently producing a flat grey theme.

### Verified against

The 45 Omarchy themes on the machine it was built on, covering all three palette shapes including light themes. Every one builds with zero CSS parse errors under GTK 4, and every colour in the output traces back to the resolved palette.

## Two things to know if you edit the template

GTK's `rgba()` takes three *numbers*. `rgba(#c8c8c8, 0.5)` is a parse error and GTK drops the whole rule without a word — use `alpha(#c8c8c8, 0.5)`.

`gtk-4.0/thpm-theme.css` is regenerated on every build; it isn't a copy of the target theme's. The template's `gtk-4.0/gtk.css` opens with `@import url("thpm-theme.css")`, which GTK resolves relative to the *theme* directory — so a file in `~/.config/gtk-4.0/` never satisfies it, and leaving the import unresolved means five parse warnings on every app launch.

## Credits

**Theme by [Signal Directive](https://github.com/signaldirective).**

Built on [Catppuccin-GnomeTheme](https://github.com/Fausto-Korpsvart/Catppuccin-GnomeTheme)
by Fausto Korpsvart.

Made to complement the Omarchy theme collection — including
[HANCORE's InkyPinky](https://github.com/HANCORE-linux/omarchy-inkypinky-theme),
which is what it was built against first.

## [Tip me on Ko-Fi!](https://ko-fi.com/signaldirective)
