project('scottland', 'cpp', 'c', version: '0.1.0', license: 'MIT',
  default_options: ['cpp_std=c++17', 'warning_level=2'])

wayfire = dependency('wayfire')
wlroots = dependency('wlroots-0.20')
xkbcommon = dependency('xkbcommon')
libinput = dependency('libinput')
cairo = dependency('cairo')

# wlroots' installed layer-shell header requires this generated protocol header.
layer_shell_header = custom_target('layer-shell-header',
  input: 'protocols/wlr-layer-shell-unstable-v1.xml',
  output: 'wlr-layer-shell-unstable-v1-protocol.h',
  command: [find_program('wayland-scanner'), 'server-header', '@INPUT@', '@OUTPUT@'])

shared_module('scottland', ['src/scottland.cpp', 'src/placement.cpp', 'src/declutter.cpp', 'src/peek.cpp',
  'src/goo.cpp', 'src/goo-model.cpp', 'src/goo-renderer.cpp', 'src/goo-hit.cpp', 'src/goo-shape.cpp',
  'src/key-layers.cpp', 'src/inertia.cpp', layer_shell_header, 'src/alt-mode.cpp', 'src/hint-overlay.cpp',
  'src/pairing.cpp', 'src/spread.cpp', 'src/session.cpp', 'src/shortcuts.cpp'],
  dependencies: [wayfire, wlroots, xkbcommon, libinput, cairo],

  # -fno-gnu-unique: without it, statics in inline functions and inline variables become
  # process-wide "unique" symbols, so a plugin reloaded in place (scottland-reload) would share
  # them with the copy it replaces, even across incompatible layouts, and the old copy could never
  # be unloaded. With it (and -Bsymbolic) each loaded copy keeps its own.
  cpp_args: ['-DWLR_USE_UNSTABLE', '-DWAYFIRE_PLUGIN', '-fno-gnu-unique'],
  # Bind the plugin's own symbols to itself, so a rebuilt copy loaded next to an older one
  # (scottland-reload) runs its own code instead of the older copy's.
  # -z now: a symbol that can't be resolved fails the plugin's load, not the session mid-use.
  link_args: ['-Wl,-Bsymbolic', '-Wl,-z,now'],
  install: true,
  install_dir: wayfire.get_variable(pkgconfig: 'plugindir'))

# DPMS through the compositor's own wlr-output-power-management path (see the tool's header).
scanner = find_program('wayland-scanner')
power_xml = 'protocols/wlr-output-power-management-unstable-v1.xml'
power_header = custom_target('output-power-client-header', input: power_xml,
  output: 'wlr-output-power-management-unstable-v1-client-protocol.h',
  command: [scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
power_code = custom_target('output-power-code', input: power_xml,
  output: 'wlr-output-power-management-unstable-v1-protocol.c',
  command: [scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
executable('scottland-output-power', ['tools/scottland-output-power.c', power_header, power_code],
  dependencies: [dependency('wayland-client')],
  install: true,
  install_dir: get_option('prefix') / get_option('libdir') / 'scottland/libexec')

install_data('metadata/scottland.xml',
  install_dir: wayfire.get_variable(pkgconfig: 'metadatadir'))
