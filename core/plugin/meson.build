project('scottland', 'cpp', version: '0.1.0', license: 'MIT',
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

# Clipboard history uses the compositor's data-control and virtual-keyboard protocols. Both XML
# descriptions are vendored so building Scottland needs only the scanner it already uses above.
clipboard_data_header = custom_target('clipboard-data-control-client-header',
  input: 'protocols/wlr-data-control-unstable-v1.xml',
  output: 'wlr-data-control-unstable-v1-client-protocol.h',
  command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
clipboard_data_code = custom_target('clipboard-data-control-client-code',
  input: 'protocols/wlr-data-control-unstable-v1.xml',
  output: 'wlr-data-control-unstable-v1-protocol.cpp',
  command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
virtual_keyboard_header = custom_target('virtual-keyboard-client-header',
  input: 'protocols/virtual-keyboard-unstable-v1.xml',
  output: 'virtual-keyboard-unstable-v1-client-protocol.h',
  command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@'])
virtual_keyboard_code = custom_target('virtual-keyboard-client-code',
  input: 'protocols/virtual-keyboard-unstable-v1.xml',
  output: 'virtual-keyboard-unstable-v1-protocol.cpp',
  command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])

executable('scottland-clipboardd',
  ['../clipboard/clipboardd.cpp', clipboard_data_header, clipboard_data_code,
   virtual_keyboard_header, virtual_keyboard_code],
  dependencies: [wayland_client, xkbcommon],
  install: true,
  install_dir: get_option('libdir') / 'scottland/libexec')

shared_module('scottland', ['src/scottland.cpp', 'src/placement.cpp', 'src/declutter.cpp', 'src/peek.cpp',
  'src/goo.cpp', 'src/goo-model.cpp', 'src/goo-renderer.cpp', 'src/goo-hit.cpp', 'src/goo-shape.cpp',
  'src/key-layers.cpp', 'src/inertia.cpp', layer_shell_header, 'src/alt-mode.cpp', 'src/hint-overlay.cpp',
  'src/pairing.cpp', 'src/spread.cpp', 'src/navigation.cpp'],
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

install_data('metadata/scottland.xml',
  install_dir: wayfire.get_variable(pkgconfig: 'metadatadir'))
