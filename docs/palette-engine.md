# Palette engine (concept)

Status: concept only, not started (Mike, 2026-10-03). Recorded so the idea isn't lost while a
Scottland distro doesn't exist yet; see [distro-notes.md](distro-notes.md).

## Why

Omarchy themes are close to enough: Omarchy v4 builds every app's colors from a theme's
`colors.toml`, so a theme is little more than a palette and a wallpaper. What's missing is semantic
colors a desktop needs that the palette doesn't name, starting with an **attention** color
(Scottland borrows the theme's `yellow` today). Aether, the usual generator, can't add one: its
palette is fixed (16 colors plus accent, cursor, selection), it rewrites `colors.toml` for themes it
manages, and it carries much that doesn't belong in a palette tool (image adjustment belongs in an
image editor).

## The concept

An engine that, **with no configuration at all**, generates a complete palette dynamically from a
**schema** TOML.

### Schema

The schema defines two things:

- a **16-color palette** (the ANSI colors), and
- **semantic colors**, each specified as an **algorithm** over a base color, the palette, or other
  semantic colors, for example:

```toml
accent = "base.split_complementary[0]"
```

A desktop ships its own schema, declaring the semantic colors it needs (Scottland's would include
`attention`) and the algorithm that derives each one by default.

### Config

A config file is optional. It points at a schema and can override any semantic color in one of
three ways:

```toml
schema = "scottland"

select = "accent.complementary"   # another algorithm
select = "palette[4]"             # an index into the 16-color palette
select = "#ffaa50"                # a hard-coded value
```

(One override per key; the three lines show the three forms.)

### What the engine does

Evaluates the schema's algorithms, applies the config's overrides, and produces the palette. With
no config, the schema's own algorithms decide everything.

## Open questions

- Where `base` comes from: a wallpaper (extracted), a single seed color, or either.
- The algorithm vocabulary: complementary, split complementary, triadic, analogous, lighten/darken,
  contrast targets against background; how colors chain (`accent.complementary`).
- Light and dark: one schema producing both modes, or a mode-aware algorithm per color.
- Output: an Omarchy-compatible `colors.toml` plus the semantic keys, so Omarchy and Scottland both
  read it; whether other targets (GTK, base16) are in scope.
- Whether this lives in its own repository, desktop-agnostic, with each desktop shipping a schema.
