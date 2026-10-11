#!/usr/bin/env python3
"""Isolated Scottland-session Omarchy text-size and delegation checks."""
import os
from pathlib import Path
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "omarchy/bin/omarchy-display-text-size"


def invoke(script, env, *args):
    return subprocess.run(["bash", str(script), *args], env=env, text=True, capture_output=True)


def fixture(root, ghostty="font-size = 13\n", linked=False):
    home = root / "home"
    home.mkdir()
    config = home / ".config/ghostty/config"
    config.parent.mkdir(parents=True)
    if linked:
        target = root / "personal-ghostty-config"
        target.write_text(ghostty)
        config.symlink_to(target)
    else:
        config.write_text(ghostty)
    alacritty = home / ".config/alacritty/alacritty.toml"
    alacritty.parent.mkdir(parents=True)
    alacritty.write_text("size = 9\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    gsettings = bin_dir / "gsettings"
    gsettings.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = get ] && [ "$3" = font-name ]; then echo "Cantarell 11"; exit 0; fi\n'
        'if [ "$1" = get ] && [ "$3" = text-scaling-factor ]; then cat "$FACTOR_FILE" 2>/dev/null || echo 1.0; exit 0; fi\n'
        'if [ "${FAIL_GSETTINGS:-0}" = 1 ]; then exit 55; fi\n'
        'if [ "$1" = set ]; then printf "%s\\n" "$4" > "$FACTOR_FILE"; exit 0; fi\n'
        'if [ "$1" = reset ]; then rm -f "$FACTOR_FILE"; exit 0; fi\n'
        "exit 1\n"
    )
    gsettings.chmod(0o755)
    pkill = bin_dir / "pkill"
    pkill.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PKILL_LOG"\n')
    pkill.chmod(0o755)
    env = dict(os.environ, HOME=str(home), FACTOR_FILE=str(root / "factor"),
               PKILL_LOG=str(root / "pkill.log"), PATH=f"{bin_dir}:{os.environ['PATH']}")
    return env, config, alacritty


def test_roundtrip(size, linked=False):
    with tempfile.TemporaryDirectory(prefix="omarchy-ghostty-size-") as temporary:
        root = Path(temporary)
        env, config, alacritty = fixture(root, f"font-size = {size}\n", linked)
        original = config.read_bytes()
        set_result = invoke(SCRIPT, env, "20")
        assert set_result.returncode == 0, set_result.stderr
        assert (root / "factor").read_text() == "1.6364\n"
        assert config.read_bytes() == original
        assert alacritty.read_text() == "size = 15\n"
        shell = root / "home/.config/omarchy/shell.toml"
        assert "base-size = 20" in shell.read_text()
        # A new process with the same user settings models the relogin read path.
        shown = invoke(SCRIPT, env)
        assert shown.returncode == 0 and "1.6364" in shown.stdout
        assert config.read_bytes() == original
        reset_result = invoke(SCRIPT, env, "reset")
        assert reset_result.returncode == 0, reset_result.stderr
        assert not (root / "factor").exists()
        assert config.read_bytes() == original
        assert alacritty.read_text() == "size = 9\n"
        assert "base-size" not in shell.read_text()
        assert not (root / "pkill.log").exists(), "Ghostty must not be reloaded"


def test_failed_settings_write():
    with tempfile.TemporaryDirectory(prefix="omarchy-ghostty-fail-") as temporary:
        root = Path(temporary)
        env, config, alacritty = fixture(root)
        env["FAIL_GSETTINGS"] = "1"
        for args in (("20",), ("reset",)):
            result = invoke(SCRIPT, env, *args)
            assert result.returncode != 0, args
            assert config.read_text() == "font-size = 13\n"
            assert alacritty.read_text() == "size = 9\n"
            assert not (root / "home/.config/omarchy/shell.toml").exists()


def test_session_command_routes():
    with tempfile.TemporaryDirectory(prefix="scottland-omarchy-size-route-") as temporary:
        root = Path(temporary)
        env, config, _ = fixture(root, "font-size = 12\n")
        hooks = root / "hooks"
        wrapper_dir = hooks / "omarchy-bin"
        wrapper_dir.mkdir(parents=True)
        for name in ("omarchy", "omarchy-display-text-size"):
            (wrapper_dir / name).symlink_to(REPO / "omarchy/bin" / name)
        vendor = root / "vendor/bin"
        vendor.mkdir(parents=True)
        (vendor / "omarchy").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" > "$VENDOR_LOG"\n'
        )
        (vendor / "omarchy").chmod(0o755)
        env.update(SCOTTLAND_HOOKS=str(hooks), OMARCHY_PATH=str(root / "vendor"),
                   VENDOR_LOG=str(root / "vendor.log"),
                   XDG_SESSION_DESKTOP="scottland")
        hook = REPO / "omarchy/session-env.d/06-text-size.sh"
        # Source the session hook as the launcher does; both direct shell-panel
        # and Omarchy's grouped CLI command must find the adapter entrypoint.
        command = (
            f'. "{hook}"; '
            'omarchy-display-text-size 20; '
            'omarchy display text size reset; '
            'omarchy theme next'
        )
        result = subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert config.read_text() == "font-size = 12\n"
        assert not (root / "factor").exists()
        assert (root / "vendor.log").read_text() == "theme next\n"
        env["XDG_SESSION_DESKTOP"] = "hyprland"
        negative = subprocess.run(
            ["sh", "-c", f'. "{hook}"; command -v omarchy'],
            env=env, capture_output=True, text=True)
        assert negative.returncode != 0 or str(wrapper_dir) not in negative.stdout


def test_no_ghostty_config():
    with tempfile.TemporaryDirectory(prefix="scottland-omarchy-size-empty-") as temporary:
        root = Path(temporary)
        env, config, _ = fixture(root)
        config.unlink()
        assert invoke(SCRIPT, env, "20").returncode == 0
        assert not config.exists()
        assert invoke(SCRIPT, env, "reset").returncode == 0
        assert not config.exists()


for base in (9, 13, 15):
    test_roundtrip(base)
test_roundtrip(12, linked=True)
test_failed_settings_write()
test_session_command_routes()
test_no_ghostty_config()
print("PASS: stock/personal/prior-15/symlink set-reset-relogin and failed GSettings writes")
