project('scottland', 'cpp', version: '0.1.0', license: 'MIT',
  default_options: ['cpp_std=c++17', 'warning_level=2'])

wayfire = dependency('wayfire')
wlroots = dependency('wlroots-0.20')
xkbcommon = dependency('xkbcommon')

shared_module('scottland', 'src/scottland.cpp',
  dependencies: [wayfire, wlroots, xkbcommon],
  cpp_args: ['-DWLR_USE_UNSTABLE', '-DWAYFIRE_PLUGIN'],
  # Bind the plugin's own symbols to itself, so a rebuilt copy loaded next to an older one
  # (scottland-reload) runs its own code instead of the older copy's.
  link_args: ['-Wl,-Bsymbolic'],
  install: true,
  install_dir: wayfire.get_variable(pkgconfig: 'plugindir'))

install_data('metadata/scottland.xml',
  install_dir: wayfire.get_variable(pkgconfig: 'metadatadir'))
