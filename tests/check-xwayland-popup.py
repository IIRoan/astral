#!/usr/bin/env python3
"""Exercise real Xwayland focus in an isolated Niri session.

Steam/Wine menus use override-redirect windows with InputHint set. Mapping
such a popup must leave keyboard focus on its parent. A regular new window
must still receive focus; otherwise a broken test session could hide the bug.
"""

import argparse
import ctypes as C
import ctypes.util
import json
from pathlib import Path
import time


class WMHints(C.Structure):
    _fields_ = [("flags", C.c_long), ("input", C.c_int), ("initial_state", C.c_int),
                ("icon_pixmap", C.c_ulong), ("icon_window", C.c_ulong),
                ("icon_x", C.c_int), ("icon_y", C.c_int), ("icon_mask", C.c_ulong),
                ("window_group", C.c_ulong)]


class WindowAttributes(C.Structure):
    _fields_ = [("background_pixmap", C.c_ulong), ("background_pixel", C.c_ulong),
                ("border_pixmap", C.c_ulong), ("border_pixel", C.c_ulong),
                ("bit_gravity", C.c_int), ("win_gravity", C.c_int),
                ("backing_store", C.c_int), ("backing_planes", C.c_ulong),
                ("backing_pixel", C.c_ulong), ("save_under", C.c_int),
                ("event_mask", C.c_long), ("do_not_propagate_mask", C.c_long),
                ("override_redirect", C.c_int), ("colormap", C.c_ulong),
                ("cursor", C.c_ulong)]


def check_popup():
    x = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    display_type, window_type = C.c_void_p, C.c_ulong
    signatures = {
        "XOpenDisplay": ([C.c_char_p], display_type),
        "XDefaultRootWindow": ([display_type], window_type),
        "XCreateSimpleWindow": ([display_type, window_type, C.c_int, C.c_int,
                                 C.c_uint, C.c_uint, C.c_uint, C.c_ulong, C.c_ulong], window_type),
        "XStoreName": ([display_type, window_type, C.c_char_p], C.c_int),
        "XSetWMHints": ([display_type, window_type, C.POINTER(WMHints)], C.c_int),
        "XMapRaised": ([display_type, window_type], C.c_int),
        "XSync": ([display_type, C.c_int], C.c_int),
        "XGetInputFocus": ([display_type, C.POINTER(window_type), C.POINTER(C.c_int)], C.c_int),
        "XDestroyWindow": ([display_type, window_type], C.c_int),
        "XChangeWindowAttributes": ([display_type, window_type, C.c_ulong,
                                     C.POINTER(WindowAttributes)], C.c_int),
        "XSetTransientForHint": ([display_type, window_type, window_type], C.c_int),
        "XInternAtom": ([display_type, C.c_char_p, C.c_int], C.c_ulong),
        "XGetSelectionOwner": ([display_type, C.c_ulong], window_type),
        "XSetWMProtocols": ([display_type, window_type, C.POINTER(C.c_ulong), C.c_int], C.c_int),
        "XChangeProperty": ([display_type, window_type, C.c_ulong, C.c_ulong,
                             C.c_int, C.c_int, C.POINTER(C.c_ubyte), C.c_int], C.c_int),
        "XSelectInput": ([display_type, window_type, C.c_long], C.c_int),
        "XCheckTypedWindowEvent": ([display_type, window_type, C.c_int, C.c_void_p], C.c_int),
        "XCloseDisplay": ([display_type], C.c_int),
    }
    for name, (args, result) in signatures.items():
        getattr(x, name).argtypes = args
        getattr(x, name).restype = result
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot open the isolated Xwayland display")
    windows = []
    try:
        # XOpenDisplay can return before the on-demand Satellite has selected
        # root-window events. Wait for its WM ownership before creating clients.
        wm_selection = x.XInternAtom(display, b"WM_S0", 0)
        end = time.monotonic() + 10
        while not x.XGetSelectionOwner(display, wm_selection):
            if time.monotonic() >= end:
                raise RuntimeError("Satellite did not acquire the window-manager selection")
            time.sleep(0.02)
        root = x.XDefaultRootWindow(display)

        def create(name):
            window = x.XCreateSimpleWindow(display, root, 64, 64, 320, 200, 0, 0, 0x335577)
            windows.append(window)
            x.XStoreName(display, window, name.encode())
            hints = WMHints(flags=1, input=1)
            x.XSetWMHints(display, window, C.byref(hints))
            return window

        def focus():
            window, revert = window_type(), C.c_int()
            x.XGetInputFocus(display, C.byref(window), C.byref(revert))
            return window.value

        def wait_focus(expected, stage, map_window=False):
            end = time.monotonic() + 10
            while time.monotonic() < end:
                if map_window:
                    # Keep the initial MapRequest live while Satellite finishes
                    # startup; its event loop can otherwise sleep with it queued.
                    x.XMapRaised(display, expected)
                x.XSync(display, 0)
                if focus() == expected:
                    return
                time.sleep(0.02)
            raise RuntimeError(f"Managed window never received focus ({stage}): expected {expected:#x}, got {focus():#x}")

        parent = create("Astral popup test parent")
        wait_focus(parent, "initial parent", map_window=True)
        normal = create("Astral managed-focus control")
        x.XMapRaised(display, normal)
        wait_focus(normal, "managed control")
        x.XDestroyWindow(display, normal)
        windows.remove(normal)
        x.XSync(display, 0)
        # Let Niri/Satellite restore focus after the control window closes.
        # Forcing X11 focus here can pass wait_focus() before Satellite has a
        # focused toplevel again, so it maps the popup as a new toplevel.
        wait_focus(parent, "restored parent")

        popup = create("Astral Steam/Wine popup regression")
        attrs = WindowAttributes(override_redirect=1)
        x.XChangeWindowAttributes(display, popup, 1 << 9, C.byref(attrs))
        x.XSetTransientForHint(display, popup, parent)
        protocols = (C.c_ulong * 2)(*[x.XInternAtom(display, atom, 0)
                                    for atom in (b"WM_DELETE_WINDOW", b"_NET_WM_PING")])
        x.XSetWMProtocols(display, popup, protocols, 2)
        popup_type = (C.c_ulong * 1)(x.XInternAtom(display, b"_NET_WM_WINDOW_TYPE_POPUP_MENU", 0))
        x.XChangeProperty(display, popup, x.XInternAtom(display, b"_NET_WM_WINDOW_TYPE", 0),
                          4, 32, 0, C.cast(popup_type, C.POINTER(C.c_ubyte)), 1)
        x.XSelectInput(display, popup, 1 << 17)
        x.XMapRaised(display, popup)
        event = (C.c_long * 24)()
        end = time.monotonic() + 10
        while not x.XCheckTypedWindowEvent(display, popup, 19, C.byref(event)):
            if time.monotonic() >= end:
                raise RuntimeError("Popup did not map; regression was not exercised")
            time.sleep(0.02)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            x.XSync(display, 0)
            actual = focus()
            if actual != parent:
                raise AssertionError(f"override-redirect popup stole focus: expected {parent:#x}, got {actual:#x}")
            time.sleep(0.02)
        return {"result": "passed", "managed_window_focus": True,
                "override_redirect_popup_mapped": True, "popup_preserves_parent_focus": True}
    finally:
        for window in reversed(windows):
            x.XDestroyWindow(display, window)
        x.XCloseDisplay(display)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        result = check_popup()
    except Exception as error:
        result = {"result": "failed", "error": str(error),
                  "popup_regression": isinstance(error, AssertionError)}
    if args.result:
        temporary = args.result.with_suffix(".tmp")
        temporary.write_text(json.dumps(result) + "\n")
        temporary.replace(args.result)
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result["result"] == "passed" else 1)
