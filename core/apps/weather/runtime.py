#!/usr/bin/env python3
"""Weather's short-lived bridge to Scottland's existing location and widget services."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys
import time


def solar_helper():
    hooks = Path(os.environ.get("SCOTTLAND_HOOKS", "/usr/lib/scottland"))
    candidates = (
        hooks / "libexec/scottland-solar-theme",
        Path("/usr/lib/scottland/libexec/scottland-solar-theme"),
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise FileNotFoundError("Scottland's Sunlight location helper is unavailable")

    loader = SourceFileLoader("scottland_solar_theme", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise ImportError("could not load Scottland's Sunlight location helper")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def geoclue_location(solar):
    """Resolve the system location at city accuracy under Weather's desktop identity."""
    try:
        import dbus

        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object("org.freedesktop.GeoClue2", "/org/freedesktop/GeoClue2/Manager"),
            "org.freedesktop.GeoClue2.Manager",
        )
        client_path = manager.GetClient()
        client = bus.get_object("org.freedesktop.GeoClue2", client_path)
        properties = dbus.Interface(client, "org.freedesktop.DBus.Properties")
        properties.Set(
            "org.freedesktop.GeoClue2.Client", "DesktopId", dbus.String("scottland-weather")
        )
        properties.Set(
            "org.freedesktop.GeoClue2.Client",
            "RequestedAccuracyLevel",
            dbus.UInt32(4),
        )
        control = dbus.Interface(client, "org.freedesktop.GeoClue2.Client")
        control.Start()
        try:
            for _ in range(25):
                location_path = str(
                    properties.Get("org.freedesktop.GeoClue2.Client", "Location")
                )
                if location_path != "/":
                    location = dbus.Interface(
                        bus.get_object("org.freedesktop.GeoClue2", location_path),
                        "org.freedesktop.DBus.Properties",
                    )
                    return solar.coordinates(
                        location.Get("org.freedesktop.GeoClue2.Location", "Latitude"),
                        location.Get("org.freedesktop.GeoClue2.Location", "Longitude"),
                    )
                time.sleep(0.2)
        finally:
            control.Stop()
    except Exception as error:
        print(f"scottland-weather: system location unavailable: {error}", file=sys.stderr)
    return None


def location():
    solar = solar_helper()
    config = solar.read_config()
    coordinates = geoclue_location(solar) or config["location"]
    if coordinates is None and config["allow_ip"]:
        coordinates = solar.ip_location()

    if coordinates is None:
        print('{"status":"no-location"}')
        return 0

    latitude, longitude = coordinates
    print(
        json.dumps(
            {"status": "located", "latitude": latitude, "longitude": longitude},
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


def publish(payload):
    import dbus

    bus = dbus.SessionBus()
    obj = bus.get_object("org.scottland.Widgets", "/org/scottland/Widgets")
    interface = dbus.Interface(obj, "org.scottland.WidgetData")
    try:
        interface.Publish(dbus.String(payload))
    except dbus.DBusException as error:
        # WG11 has no destination until this app's window is a widget. The app keeps the
        # reading in memory and republishes it on WG12's widgetized signal.
        if "no widget for the calling app" in str(error):
            return 0
        print(f"scottland-weather: widget publish failed: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv):
    if argv == ["location"]:
        try:
            return location()
        except Exception as error:
            print(f"scottland-weather: location resolution failed: {error}", file=sys.stderr)
            return 1
    if len(argv) == 2 and argv[0] == "publish":
        try:
            json.loads(argv[1])
        except ValueError as error:
            print(f"scottland-weather: invalid widget reading: {error}", file=sys.stderr)
            return 2
        try:
            return publish(argv[1])
        except Exception as error:
            print(f"scottland-weather: widget publish failed: {error}", file=sys.stderr)
            return 1
    print("usage: runtime.py location | publish JSON", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
