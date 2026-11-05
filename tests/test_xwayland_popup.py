"""Check focus handoff timing without a running desktop or X11 server."""

import ctypes as C
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location(
    "xwayland_popup", Path(__file__).with_name("check-xwayland-popup.py"))
popup_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(popup_check)


class PopupFocusTests(unittest.TestCase):
    def run_check(self, restore_delay, *, manager_delay=0, initial_map_requests=1,
                  steal_popup_focus=False):
        parent, normal, popup = 0x400001, 0x400002, 0x400003
        now = 0.0
        focused = 0
        restore_at = float("inf")
        restored = False
        parent_maps = 0
        x = MagicMock()
        x.XOpenDisplay.return_value = 1
        x.XCreateSimpleWindow.side_effect = [parent, normal, popup]
        x.XCheckTypedWindowEvent.return_value = 1
        x.XInternAtom.return_value = 1
        x.XGetSelectionOwner.side_effect = lambda *_: int(now >= manager_delay)

        def sleep(seconds):
            nonlocal now
            now += seconds

        def sync(*_):
            nonlocal focused, restored
            if not restored and now >= restore_at:
                focused = parent
                restored = True

        def focus(_display, window, _revert):
            C.cast(window, C.POINTER(C.c_ulong)).contents.value = focused

        def map_window(_display, window):
            nonlocal focused, parent_maps
            if now < manager_delay:
                # An early map can be missed before the manager subscribes to
                # root-window events, leaving the client permanently unfocused.
                return
            if window == parent:
                parent_maps += 1
                if parent_maps < initial_map_requests:
                    return
            if window != popup or not restored or steal_popup_focus:
                # Without a restored toplevel Satellite treats the popup as
                # a new toplevel, which receives focus from the compositor.
                focused = window

        def destroy(_display, window):
            nonlocal focused, restore_at
            if window == normal:
                focused = 0
                restore_at = now + restore_delay

        def force_focus(_display, window, *_):
            nonlocal focused
            # XSetInputFocus changes X11 focus without restoring Satellite's
            # knowledge of which Wayland toplevel is focused.
            focused = window

        x.XSync.side_effect = sync
        x.XGetInputFocus.side_effect = focus
        x.XMapRaised.side_effect = map_window
        x.XDestroyWindow.side_effect = destroy
        x.XSetInputFocus.side_effect = force_focus
        with patch.object(popup_check.C, "CDLL", return_value=x), \
                patch.object(popup_check.time, "monotonic", side_effect=lambda: now), \
                patch.object(popup_check.time, "sleep", side_effect=sleep):
            return popup_check.check_popup()

    def test_waits_for_compositor_focus_restoration(self):
        for delay in (0, 0.1, 1.5, 5):
            with self.subTest(restore_delay=delay):
                result = self.run_check(delay)
                self.assertEqual(result["result"], "passed")
                self.assertTrue(result["managed_window_focus"])
                self.assertTrue(result["popup_preserves_parent_focus"])

    def test_missing_focus_restoration_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Managed window never received focus"):
            self.run_check(float("inf"))

    def test_waits_for_window_manager_startup(self):
        result = self.run_check(1.5, manager_delay=1.5)
        self.assertEqual(result["result"], "passed")

    def test_initial_map_keeps_startup_event_loop_awake(self):
        result = self.run_check(1.5, initial_map_requests=3)
        self.assertEqual(result["result"], "passed")

    def test_missing_window_manager_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Satellite did not acquire"):
            self.run_check(0, manager_delay=float("inf"))

    def test_popup_focus_regression_still_fails(self):
        with self.assertRaisesRegex(AssertionError, "override-redirect popup stole focus"):
            self.run_check(1.5, steal_popup_focus=True)


if __name__ == "__main__":
    unittest.main()
