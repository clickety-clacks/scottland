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

Evaluates the schema's algorithms, applies the config's overrides, and produces the palette for
that schema's desktop. With no config, the schema's own algorithms decide everything.

## Decided (Mike, 2026-10-03)

- **`base` is just another color** defined by the schema, like accent or selection. Nothing is
  extracted from a wallpaper.
- **The algorithms are the standard color-wheel ones** (complementary, split complementary,
  triadic, analogous, and so on).
- **Light or dark is a boolean** in the palette that marks it light or dark; it is not a second
  palette or a mode-aware algorithm.
- **Desktops are schemas.** The engine is desktop-agnostic: Omarchy and Scottland are just
  different schemas. Using the same tool with Omarchy means pointing it at the Omarchy schema; its
  output is then an Omarchy theme. In a Scottland distro the output is Scottland-compatible (the
  Scottland schema), not Omarchy's `colors.toml`.
- **Packaging:** the engine is its own repository, which the distro installs. The distro contains
  the default themes. Scottland knows how to read configs made with the Scottland schema.

## Open questions

- The exact algorithm vocabulary and expression syntax (chaining such as `accent.complementary`,
  indexing such as `split_complementary[0]`, any lightness or contrast adjustments).
- The Scottland schema's semantic colors beyond attention (goo tint, halo, hint colors, ...).
