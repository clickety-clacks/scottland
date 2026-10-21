project('scottland', 'cpp', 'c', version: '0.2.0', license: 'MIT',
  default_options: ['cpp_std=c++17', 'warning_level=2'])

wayfire = dependency('wayfire')
wlroots = dependency('wlroots-0.20')
xkbcommon = dependency('xkbcommon')
libinput = dependency('libinput')
cairo = dependency('cairo')
egl = dependency('egl')
wayland_client = dependency('wayland-client')

# wlroots' installed layer-shell header requires this generated protocol header.
wayland_scanner = find_program('wayland-scanner')
layer_shell_header = custom_target('layer-shell-header',
  input: 'protocols/wlr-layer-shell-unstable-v1.xml',
  output: 'wlr-layer-shell-unstable-v1-protocol.h',
  command: [wayland_scanner, 'server-header', '@INPUT@', '@OUTPUT@'])

shared_module('scottland', ['src/scottland.cpp', 'src/placement.cpp', 'src/declutter.cpp', 'src/peek.cpp',
  'src/goo.cpp', 'src/goo-model.cpp', 'src/goo-renderer.cpp', 'src/goo-hit.cpp', 'src/goo-shape.cpp',
  'src/key-layers.cpp', 'src/inertia.cpp', layer_shell_header, 'src/alt-mode.cpp', 'src/hint-overlay.cpp',
  'src/note-overlay.cpp', 'src/pairing.cpp', 'src/spread.cpp', 'src/navigation.cpp', 'src/session.cpp', 'src/shortcuts.cpp'],
  dependencies: [wayfire, wlroots, xkbcommon, libinput, cairo, egl],

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
scanner = wayland_scanner
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

# Every output the compositor knows, with its real metadata, through wlr-output-management.
heads_xml = 'protocols/wlr-output-management-unstable-v1.xml'
heads_header = custom_target('output-heads-client-header', input: heads_xml,
  output: 'wlr-output-management-unstable-v1-client-protocol.h',
  command: [scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
heads_code = custom_target('output-heads-code', input: heads_xml,
  output: 'wlr-output-management-unstable-v1-protocol.c',
  command: [scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
executable('scottland-output-heads', ['tools/scottland-output-heads.c', heads_header, heads_code],
  dependencies: [wayland_client],
  install: true,
  install_dir: get_option('prefix') / get_option('libdir') / 'scottland/libexec')

# Clipboard history uses the compositor's data-control and virtual-keyboard protocols. Keep their
# XML descriptions in the repository so the feature adds no protocol package dependency.
clipboard_data_header = custom_target('clipboard-data-control-client-header',
  input: 'protocols/wlr-data-control-unstable-v1.xml',
  output: 'wlr-data-control-unstable-v1-client-protocol.h',
  command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
clipboard_data_code = custom_target('clipboard-data-control-client-code',
  input: 'protocols/wlr-data-control-unstable-v1.xml',
  output: 'wlr-data-control-unstable-v1-protocol.c',
  command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
virtual_keyboard_header = custom_target('virtual-keyboard-client-header',
  input: 'protocols/virtual-keyboard-unstable-v1.xml',
  output: 'virtual-keyboard-unstable-v1-client-protocol.h',
  command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
virtual_keyboard_code = custom_target('virtual-keyboard-client-code',
  input: 'protocols/virtual-keyboard-unstable-v1.xml',
  output: 'virtual-keyboard-unstable-v1-protocol.c',
  command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
executable('scottland-clipboardd',
  ['../clipboard/clipboardd.cpp', clipboard_data_header, clipboard_data_code,
   virtual_keyboard_header, virtual_keyboard_code],
  dependencies: [wayland_client, xkbcommon],
  install: true,
  install_dir: get_option('prefix') / get_option('libdir') / 'scottland/libexec')

install_data('metadata/scottland.xml',
  install_dir: wayfire.get_variable(pkgconfig: 'metadatadir'))
