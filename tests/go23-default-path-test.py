#!/usr/bin/env python3
"""Keep GO23=1 on the original Goo composite equations."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
shader = (root / "core/plugin/src/goo-shaders.hpp").read_text()
frame = (root / "core/plugin/src/frame.hpp").read_text()
helper = (root / "core/plugin/src/state-dye.hpp").read_text()

# The default field accumulation is the original A16 tint weight. The other
# branch transports the separate state mix needed only when strength changes.
assert re.search(
    r"if\(uDyeStrength==1\.\)\s*\{\s*if\(uNeutralTint>\.5&&dyeTint<\.999999\)dyeBlend\*=dyeTint;",
    shader,
)
assert "else if(uNeutralTint>.5)color+=dye*rim*.22*dyeTint;" in shader
assert "tinted+=contribution*(uDyeStrength==1.?source(i,7.).y:source(i,7.).z);" in shader
assert "stateTint=neutralStrength*(1.-stateMix)+uDyeStrength*stateMix;" in shader
assert "dyeBlend=clamp(dyeBlend*stateTint,0.,1.);" in shader

# At one, fallback state colors bypass tone adjustment exactly, and the shared
# helper keeps the neutral contribution as its independent A16 term.
assert "if (strength == 1.f) return color;" in helper
assert "if (palette.dye_strength != 1.f)" in frame
assert "state_dye::tone(neutral, tone, palette.dye_strength)" in frame
assert "state_dye::tone(neutral, *self->hint_dye, palette.dye_strength)" in frame

print("PASS GO23 strength 1 preserves original Goo dye equations and fallback tone")
