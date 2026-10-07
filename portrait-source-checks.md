# Portrait prototype source reconciliation checks

This is the bounded portrait prototype source candidate, based on Scottland `main` at
`8d4cf21`. The branch is `prototype/horizontal-zones-portrait-main-8d4cf21`; it is a
review candidate, not a main landing or release target.

The `vertical_outputs` load-time plugin option selects an output whose center/periphery
axis runs from top to bottom. Per-screen zone settings remain in use. Window geometry
stays unrotated; pointer-held scaling, zone memory, center/periphery placement, keyboard
rail return, and top/bottom widget placement and visibility use that axis. Horizontal
outputs retain their existing left/right behavior. The vertical work-area origin and
extent are placeholders pending product decisions. The prototype places cards on the
vertical rails but does not add rail collision or multi-card layout behavior.

## Checks run

- `git diff HEAD --check`: passed.
- Merge-marker scan of plugin C++ and metadata: no markers found.
- Python `ElementTree` parse of `core/plugin/metadata/scottland.xml`: passed.
- `make plugin`: stopped during Meson dependency discovery at `core/plugin/meson.build:4`:
  `Dependency "wayfire" not found (tried pkg-config and cmake)`. No C++ compilation ran.
- No checkout tests, runner build, VM run, or screenshots were performed.

## VM evidence still required

The runner must first admit and compile the exact committed candidate. After that, the
runner-owned VM session should capture eight measurements with screenshots: a portrait
center window; upper and lower periphery scaling; a held drag crossing the upper curve,
center band, and lower curve; and a widget on each of the top and bottom rails. Record the
screen dimensions, output name, window center along the selected axis, reported scale, and
interaction state for each capture. None of those screenshots or measurements exists yet.

R1-R4 remain product decisions. This source reconciliation does not decide them or commit
the prototype to Scottland main.
