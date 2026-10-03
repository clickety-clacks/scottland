#!/usr/bin/env python3
"""Add opt-in callback/frame tracing to an isolated test copy (baseline or current).
Never instrument a live checkout. Rebuild after running; trace output is enabled by
SCOTTLAND_CONVERSION_TRACE=1, and stays in the headless session's wayfire.log.
"""
from pathlib import Path
import argparse

ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path);args=ap.parse_args()
p=args.checkout/'core/plugin/src'
assert not (p/'conversion-trace.hpp').exists(), 'already instrumented'
(p/'conversion-trace.hpp').write_text('''#pragma once
#include <chrono>
#include <cstdio>
#include <cstdlib>
namespace scottland {
inline double conversion_now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
struct conversion_trace_t {
 const char *label; double start = 0;
 conversion_trace_t(const char *s): label(s) { if (getenv("SCOTTLAND_CONVERSION_TRACE")) start = conversion_now(); }
 ~conversion_trace_t() { if (start) fprintf(stderr, "CONVERSION %.6f %s %.3f\\n", start, label, (conversion_now()-start)*1000); }
};
}
''')

for file in ('scottland.cpp','widget-morph.hpp','goo.cpp','hint-overlay.cpp'):
    f=p/file;f.write_text('#include "conversion-trace.hpp"\n'+f.read_text())
for file,signature,label in [
    ('scottland.cpp','void widgetize(','widgetize'),
    ('scottland.cpp','bool step_morph()','drag-morph'),
    ('widget-morph.hpp','static widget_image_t capture(','capture'),
    ('widget-morph.hpp','widget_image_t freeze(','freeze'),
    ('widget-presentation.hpp','void begin_window_widget_transition(','begin-entry'),
    ('widget-presentation.hpp','void adopt_window_widget_transition(','adopt-entry'),
    ('hint-overlay.cpp','void hint_node::update(','hint-update')]:
    # widgetize's default arguments include {}; the body begins on its own line.
    f=p/file;s=f.read_text();start=s.index(signature)
    pos=s.index('\n    {',start)+len('\n    {') if file in ('scottland.cpp','widget-morph.hpp') else s.index('\n{',start)+2
    s=s[:pos]+f'\n        scottland::conversion_trace_t conversion_trace("{label}");'+s[pos:];f.write_text(s)
f=p/'goo.cpp';s=f.read_text().replace('pre = [this] { prepare(); };','pre = [this] { scottland::conversion_trace_t trace("frame"); prepare(); };');f.write_text(s)
f=args.checkout/'tests/headless.sh';s=f.read_text().replace('HOME|USER|LOGNAME|SHELL|','SCOTTLAND_CONVERSION_TRACE|HOME|USER|LOGNAME|SHELL|');f.write_text(s)
