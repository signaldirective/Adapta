# Auto-matching the GTK theme on Omarchy theme switch

How Adapta is kept in step with whichever Omarchy theme is active, what it
took to get there, and what would need to change upstream in Omarchy for it to
work without a hook.

Written while implementing this on Omarchy (kernel `7.2.5-3-omarchy`). Every
path and line number below was read from the shipped `omarchy` package on that
system, not from a checkout.

---

## 1. The problem

`omarchy-theme-set-gnome` hardcodes the GTK theme on every switch:

```bash
# /usr/share/omarchy/bin/omarchy-theme-set-gnome
mode=$(omarchy-theme-color --file "$COLORS_FILE" mode)

if [[ $mode == "light" ]]; then
  gsettings set org.gnome.desktop.interface gtk-theme "Adwaita"
else
  gsettings set org.gnome.desktop.interface gtk-theme "Adwaita-dark"
fi
```

There is no override, no config check, no environment variable. Any GTK theme a
user installed is discarded the moment they pick an Omarchy theme from the
picker.

That leaves Adapta in a bind: it has to *rebuild* its stylesheets from the new
theme's `colors.toml` (0.2s of work) and then *claim* the GTK theme name back
from Adwaita. Both have to happen inside the theme switch, or the user is doing
it by hand.

---

## 2. Why it's possible at all

`omarchy-theme-set` runs the theme hooks **after** it forces the GTK theme:

```bash
# /usr/share/omarchy/bin/omarchy-theme-set
post_theme_commands=(
  omarchy-restart-terminal
  omarchy-restart-hyprctl
  ...
  omarchy-theme-set-gnome          # <-- forces Adwaita(-dark)
  ...
)

if [[ $THEME_HEADLESS != "1" ]]; then
  run_parallel "${post_theme_commands[@]}"

  # Call hook on theme set
  omarchy-hook theme-set "$THEME_NAME" >/dev/null      # <-- runs second
```

`omarchy-theme-set-gnome` is one of several `post_theme_commands`, executed in
a `run_parallel` batch. `omarchy-hook theme-set` runs *after* that batch, on the
main thread. **The ordering is the entire enabler** — the hook gets the last
word on `gtk-theme` precisely because it runs after `set-gnome`.

This is by design rather than by accident: theme-set hooks exist to customise
the result of a theme change, and `set-gnome` only claims one key among many.

### The hook contract

`/usr/share/omarchy/bin/omarchy-hook`:

```bash
HOOK=$1; shift

if [[ -f $HOOK_PATH ]]; then           # ~/.config/omarchy/hooks/<name>
  bash "$HOOK_PATH" "$@" || echo "Hook failed: $HOOK_PATH"
fi

if [[ -d $HOOK_DIR ]]; then            # ~/.config/omarchy/hooks/<name>.d/*
  for hook in "$HOOK_DIR"/*; do
    [[ -f $hook ]] || continue
    [[ $hook == *.sample ]] && continue
    bash "$hook" "$@" || echo "Hook failed: $hook"
  done
done
```

Four properties that matter:

1. **Receives the theme slug as `$1`** — `omarchy-hook theme-set "$THEME_NAME"`.
2. **Globs in sorted order** — hence the `50-`, `90-`, `95-`, `99-` prefixes.
   Numeric prefixes sort before names, so a prefixed hook reliably runs after
   the stock `90-thpm`.
3. **A failing hook is non-fatal** — it prints `Hook failed: <path>` and the
   switch continues. A broken hook cannot take the desktop down.
4. **`.sample` files are skipped**, so a hook can ship disabled by default.

### Where the theme actually lives

`omarchy-theme-set` stages the theme and then swaps it into place:

```bash
cp -r "$USER_THEMES_PATH/$THEME_NAME/"* "$NEXT_THEME_PATH/"   # overlays merged
...
mv "$NEXT_THEME_PATH" "$CURRENT_THEME_PATH"                    # -> current/theme
echo "$THEME_NAME" >"$HOME/.local/state/omarchy/current/theme.name"
```

So during the hook, `~/.local/state/omarchy/current/theme` holds the fully
resolved theme — user overlay from `~/.config/omarchy/themes/<slug>` already
merged in. That is the correct thing to read, more so than the slug, which
would point at the source directory rather than the resolved result.

---

## 3. The implementation

`tools/95-adapta`, installed with:

```bash
omarchy hook install theme-set tools/95-adapta
```

which copies it to `~/.config/omarchy/hooks/theme-set.d/95-adapta`. `95-`
places it after the stock `90-thpm` (which regenerates Omarchy's own
`gtk.css`) and before `99-omarchy-org-sync.sh`.

The hook, in full:

```bash
#!/usr/bin/env bash
# Adapta — keep the GTK theme matched to whichever Omarchy theme was just applied.
#
# omarchy-theme-set calls omarchy-theme-set-gnome (which hardcodes gtk-theme to
# Adwaita/Adwaita-dark) and *then* runs `omarchy-hook theme-set`. That ordering
# is the whole trick: the hook runs second, so it gets the last word.
#
# $1 — the theme slug. Not used directly: the theme omarchy applied is read from
#      current/theme, which already has any
#      ~/.config/omarchy/themes/<slug> overlay merged into it.
#
# Everything fails closed. A rebuild that cannot finish leaves Omarchy's own
# Adwaita choice standing, which is the correct degradation for a system hook
# that runs on every theme switch whether or not Adapta is installed.

slug="${1:-}"

# Find Adapta, wherever it was cloned and whatever it was named. Prefer
# ~/.themes/Adapta explicitly, then fall back to any installed theme directory
# carrying a build.py — the theme name is a directory-name property, so
# hardcoding it would break if the theme were renamed.
adapta=""
for candidate in "$HOME/.themes/Adapta" "$HOME"/.themes/*/; do
  [[ -d $candidate ]] || continue
  if [[ -f "$candidate/build.py" ]]; then
    adapta="${candidate%/}"
    break
  fi
done

[[ -n $adapta ]] || exit 0
name=$(basename "$adapta")

# Rebuild from the theme omarchy just staged.
theme="$HOME/.local/state/omarchy/current/theme"
[[ -d $theme ]] || exit 0

python3 "$adapta/build.py" --omarchy "$theme" >/dev/null 2>&1 || exit 0

# Claim the GTK theme back — set-gnome already ran Adwaita(-dark) through gsettings.
gsettings set org.gnome.desktop.interface gtk-theme "$name" 2>/dev/null || true

# settings.ini is what GTK consults when no XSettings daemon is answering, so
# keep it in step rather than letting gsettings and the ini file disagree.
for ini in "$HOME/.config/gtk-3.0/settings.ini" "$HOME/.config/gtk-4.0/settings.ini"; do
  [[ -f $ini ]] || continue
  if grep -q '^gtk-theme-name=' "$ini"; then
    sed -i "s/^gtk-theme-name=.*/gtk-theme-name=$name/" "$ini"
  else
    printf 'gtk-theme-name=%s\n' "$name" >>"$ini"
  fi
done

exit 0
```

### Why each decision

**Discovery instead of a hardcoded path.** A single `[[ -d ~/.themes/Adapta ]]
|| exit 0` guard would silently stop working if the theme were renamed, which
is exactly the kind of failure that is hard to notice. Scanning for the one
installed theme carrying a `build.py` keeps working under a rename, and costs
nothing.

**`|| exit 0` on the build.** If the rebuild fails, Omarchy's own Adwaita
choice should stand. The alternative — leaving `gtk-theme` pointing at a theme
directory whose stylesheets failed to write — is worse than Adwaita.

**`current/theme`, not `$1`.** `$1` is the slug and resolves to the *source*
theme directory. `current/theme` is the staged, overlay-merged copy that
Omarchy actually applied. Reading the wrong one would silently ignore a user's
colour overrides in `~/.config/omarchy/themes/<slug>/`.

**gsettings *and* settings.ini.** `set-gnome` writes via `gsettings` (dconf).
GTK also reads `settings.ini`. patching only one leaves them able to disagree
depending on which session mechanism is answering, which is the failure mode
that makes a theme "work sometimes".

**No `set -e`.** `omarchy-hook` already wraps each hook in `|| echo "Hook
failed"`, but an unbound variable or a failed `grep` under `set -e` would abort
the hook before it reached the `gsettings` call. `set -u` plus explicit guards
gives the safety without the early exit.

---

## 4. Verified live

Both directions, on a running desktop, with the hook installed:

| Switch | Adapta's accent afterwards | `gtk-theme` afterwards |
|---|---|---|
| inkypinky → dracula | `#8be9fd` (Dracula's cyan) | `Adapta` |
| dracula → inkypinky | `#7c7ca8` (InkyPinky's lavender) | `Adapta` |

Before the hook, both switches ended with `gtk-theme` set to `Adwaita-dark`.

Each build also passes the palette trace: every colour in `gtk-3.0/`,
`gtk-4.0/` and `gnome-shell/` resolves back to the target theme's `colors.toml`,
with zero out-of-palette colours. Rebuild cost is ~0.2s.

A detail worth noting from the test: after `~/.config/gtk-4.0/gtk.css` was
removed, `thpm` recreated it as an ordinary file on the next switch. That
confirms `thpm` owns that path — see the risk below.

---

## 5. What is still wrong, and the PR that fixes it

The hook works, but it works *by cleanup*. On every switch:

1. `set-gnome` sets `gtk-theme` to Adwaita-dark.
2. The hook sets it to Adapta.

Running GTK apps repaint in that window, so there is a brief Adwaita flash
before Adapta lands. Functionally fine, visually not.

### Proposed upstream change

Add an opt-in escape hatch to `omarchy-theme-set-gnome`, before the hardcoded
pair:

```bash
if [[ -f $HOME/.config/omarchy/gtk-theme ]]; then
  gsettings set org.gnome.desktop.interface gtk-theme \
    "$(<"$HOME/.config/omarchy/gtk-theme")"
elif [[ $mode == "light" ]]; then
  gsettings set org.gnome.desktop.interface gtk-theme "Adwaita"
else
  gsettings set org.gnome.desktop.interface gtk-theme "Adwaita-dark"
fi
```

With that in place the hook only rebuilds and writes the marker file; it never
touches `gsettings` itself, and the flash disappears because `gtk-theme` is
never wrong in the first place.

**Why this shape is mergeable:**

- **Opt-in and inert by default.** No file, no behaviour change for any
  existing user. It is the same pattern Omarchy already uses for
  `icons.theme`, one line up in the same script.
- **One key, one file.** No new config namespace, no schema, no daemon.
- **It does not duplicate the hook.** The hook still owns the *rebuild*; it
  just stops having to undo `set-gnome`.

### Where the hook no longer needs to run

If the marker file lands, the hook could drop to something that only rebuilds
and writes the marker — no `gsettings`, no `settings.ini` editing. That makes it
both shorter and race-free.

### Placement detail that matters

`omarchy-theme-set-gnome` bails out early when there is no session bus:

```bash
if [[ -z ${DBUS_SESSION_BUS_ADDRESS:-} ]]; then
  exit 0
fi
```

The override check must go **after** that guard, inside the same branch as the
existing `gsettings` calls. Putting it before would apply a user's marker
during ISO chroot finalisation, where no session exists to honour it and where
the existing code deliberately does nothing.

### The other thing a PR should mention

`set-gnome` runs inside `run_parallel`, while the hook runs afterwards. So even
with the marker file, `set-gnome` finishing before the hook does is what makes
the current single-writer property hold. The PR should state that the marker
file is expected to be read by `set-gnome` and written by a hook, and that the
hook is therefore documented as running after `post_theme_commands` — an
ordering guarantee the PR is implicitly relying on, and one Omarchy would not
want to change casually.

---

## 6. Known risks and sharp edges

**`~/.config/gtk-4.0/` and `thpm`.** The stock `90-thpm` hook manages
`~/.config/gtk-*/gtk.css` and `~/.config/gtk-*/thpm-theme.css`, and its
`apply_gtk()` resolves symlinks before writing. If `~/.config/gtk-4.0/gtk.css`
is symlinked into a theme directory, `thpm` writes *into that theme's
stylesheet*. During this implementation those symlinks were removed and the
theme loads by name instead. Worth a note in any PR that ships a GTK theme:
do not symlink `~/.config/gtk-4.0/gtk.css` into a theme directory.

**The Adwaita flash** (see above) — unavoidable while the fix lives in a hook.

**`omarchy default editor`-style setters.** `omarchy-default-editor` only
accepts `code|cursor|zed|sublime_text|helix|vim|emacs|nvim`, while
`omarchy-launch-editor` additionally handles `nano|micro|hx|fresh`. Setting an
unsupported value through the command fails; the state file has to be written
directly. Not related to this PR, but an example of the setter/launcher split
being inconsistent — worth checking whether `set-gnome` has an equivalent gap
before proposing the marker file.

**Rebuild time.** ~0.2s for ~150 files, measured. It ran inside the hook with
no perceptible delay to the switch. A theme with a much larger asset set would
want the same measurement repeated.

**First switch after install.** Nothing runs until the *next* theme change, so
the GTK theme stays Adwaita until then. Installing the hook should be followed
by one `omarchy theme set <current theme>` to converge immediately.

---

## 7. Reproducing the verification

```bash
# install
omarchy hook install theme-set tools/95-adapta

# switch, and check both results
omarchy theme set dracula
grep -m1 accent_bg_color ~/.themes/Adapta/gtk-4.0/gtk.css   # -> Dracula's cyan
gsettings get org.gnome.desktop.interface gtk-theme         # -> 'Adapta'

omarchy theme set inkypinky
grep -m1 accent_bg_color ~/.themes/Adapta/gtk-4.0/gtk.css   # -> InkyPinky's lavender
```

Without the hook, the `gsettings` line returns `'Adwaita-dark'` in both cases.
