# Scottland portrait prototype: current-main source packet

Work item: `wi_c6140b4c-08b1-4955-b631-36cd7642d8ec`  
Scope authority: Mike's request in `art_e2ee051c`, PO ruling `art_2e3cadb9`, and topology `art_3b77a5ae`.  
Branch: `prototype/horizontal-zones-portrait-main-8d4cf21`  
Current-main base: `8d4cf210c90642d6690c348d07c97e775ec11e78` (`8d4cf21`).

## Preserved candidate and reconciliation

The earlier bounded prototype remains preserved on `prototype/horizontal-zones-portrait` at
`ee85bdeb913d36b10252f4fcbcc6902ef8c6464b` (with its preceding implementation commit
`2b075a9`). That branch was not rewritten. This current-main branch reapplies the approved
prototype behavior against current main; its implementation commit is
`888df4d70a8fc6118faf1de72d037a6b8a64a25f`, whose parent is `8d4cf21`. The old candidate
is a preserved source reference, not an ancestor of the current-main branch.

## Bounded behavior

The load-time `vertical_outputs` plugin option selects an output whose zone axis runs from top
to bottom. The center zone spans the middle; periphery sits above and below. Scaling follows
vertical distance from center during ordinary movement and a held drag. Docked widgets can
occupy both vertical rails. Window geometry remains unrotated, and horizontal outputs retain
their existing left/right behavior. The work-area extent and top/bottom rail collision/layout
remain prototype placeholders.

The implementation adapts the earlier F1/F2/B1/C1 and small A1/A2 sample to current main's
per-screen zone settings, session initialization, and placement code. It does not implement
full R1-R4 product behavior. R2 icon-left and R4 work-area measurement remain placeholders;
R1 pairing, R3 rail growth, and a public A10 interface commitment remain out of scope. No
product decision is made by this source candidate.

## Source checks and build status

- `git diff --check`: passed.
- Merge-marker scan of plugin C++ and metadata: no markers found.
- Python `xml.etree.ElementTree` parse of `core/plugin/metadata/scottland.xml`: passed.
- `make plugin`: stopped at Meson dependency discovery (`core/plugin/meson.build:4`):
  `Dependency "wayfire" not found (tried pkg-config and cmake)`. No changed C++ source
  compiled. No dependency was installed and Gibson host setup was not changed.
- No checkout tests, runner build, VM run, or screenshot capture has been performed on this
  current-main candidate.

The earlier `ee85bde` measurement log reports 30 minutes 19 seconds of aggregate authoring and
follow-up review across F1/F2/B1/C1/Z3/A1/A2. The initial authoring interval was combined, so
there are no fair per-item actuals. The original report's 20-27 agent-day full-build range is
not revised from this uncompiled source-only sample.

## Runner gate and pending VM evidence

The runner ledger currently contains only the old `art_a137cf76` portrait intake. Its exact
record describes `ee85bde` as intake-only, with no runner wake or host authorization. There is
no candidate-specific admission or compile-first result for current-main implementation
`888df4d`; do not infer a build or pass from unrelated runner work. The runner must admit and
compile the exact implementation candidate before any VM screenshot work. A failed compile
stops the screenshot phase and returns its exact diagnostic for correction.

If the runner later admits the candidate and compile succeeds, it owns the separate VM run
under the existing VM plan `art_281c3087`. The eight planned captures are:

1. `01-portrait-baseline.png`
2. `02-center-band.png`
3. `03-upper-periphery.png`
4. `04-lower-periphery.png`
5. `05-held-drag-upper.png`
6. `06-held-drag-lower.png`
7. `07-top-rail-widget.png`
8. `08-bottom-rail-widget.png`

Each screenshot needs the manifest measurements: unrotated output dimensions and name, window
center on the zone axis, reported scale, and interaction state. None exists for this current-main
candidate. The VM plan, including its guest image/display/input procedure, resource estimate,
scratch limit, and cleanup, remains `art_281c3087`; this packet authorizes no Plumbus/KVM use.

This is a review candidate only: no main landing, release, or Osanwe work is included.
