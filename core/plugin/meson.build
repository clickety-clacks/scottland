project('scottland', 'cpp', version: '0.1.0', license: 'MIT',
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

shared_module('scottland', ['src/scottland.cpp', 'src/placement.cpp', 'src/declutter.cpp',
  'src/key-layers.cpp', layer_shell_header, 'src/alt-mode.cpp', 'src/hint-overlay.cpp'],
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

install_data('metadata/scottland.xml',
  install_dir: wayfire.get_variable(pkgconfig: 'metadatadir'))
