"""Module 1: Input Capture Engine (Phase 3).

Records mouse clicks, scrolls and keyboard presses with millisecond offsets.
Keys typed into password fields are NEVER stored; a single 'protected' marker is
stored instead.
"""
import threading
import time

from capture.keys import button_to_str, key_to_str
from capture.safety import is_protected_focus


class Recorder:
    def __init__(self, protected_check=None, clock=time.monotonic):
        self._protected = protected_check or is_protected_focus
        self._clock = clock
        self._lock = threading.Lock()
        self._events = []
        self._listeners = []
        self.state = "idle"            # idle | recording | paused
        self._start = 0.0
        self._paused_at = None
        self._paused_total = 0.0

    # ---------------------------------------------------------------- timing
    def _t(self):
        ref = self._paused_at if self._paused_at is not None else self._clock()
        return int((ref - self._start - self._paused_total) * 1000)

    def _add(self, **event):
        with self._lock:
            if self.state != "recording":
                return
            event.setdefault("x", None)
            event.setdefault("y", None)
            event.setdefault("key", None)
            event.setdefault("button", None)
            event.setdefault("scroll", None)
            event["t_offset_ms"] = self._t()
            self._events.append(event)

    # ------------------------------------------------------------- callbacks
    def on_click(self, x, y, button, pressed):
        if pressed:
            self._add(type="click", x=int(x), y=int(y), button=button_to_str(button))

    def on_scroll(self, x, y, dx, dy):
        self._add(type="scroll", x=int(x), y=int(y), scroll=f"{dx},{dy}")

    def on_press(self, key):
        self._key_event("key_down", key)

    def on_release(self, key):
        self._key_event("key_up", key)

    def _key_event(self, kind, key):
        if self._protected():                       # safety rule
            with self._lock:
                last = self._events[-1] if self._events else None
            if kind == "key_down" and not (last and last["type"] == "protected"):
                self._add(type="protected")        # note that it happened, never the key
            return
        self._add(type=kind, key=key_to_str(key))

    def add_device_event(self, detail):
        """Called by the device monitor (Module 2) so devices share the same timeline."""
        self._add(type="device", key=detail)

    # -------------------------------------------------------------- controls
    def _begin_session(self):
        with self._lock:
            self._events = []
            self._start = self._clock()
            self._paused_at = None
            self._paused_total = 0.0
            self.state = "recording"

    def start(self):
        from pynput import keyboard, mouse           # imported late so tests run headless
        self._begin_session()
        self._listeners = [
            mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll),
            keyboard.Listener(on_press=self.on_press, on_release=self.on_release),
        ]
        for listener in self._listeners:
            listener.start()

    def pause(self):
        with self._lock:
            if self.state == "recording":
                self._paused_at = self._clock()
                self.state = "paused"

    def resume(self):
        with self._lock:
            if self.state == "paused":
                self._paused_total += self._clock() - self._paused_at
                self._paused_at = None
                self.state = "recording"

    def stop(self, trim_stop_key=False):
        """Stop listening and return the ordered event list."""
        for listener in self._listeners:
            listener.stop()
        self._listeners = []
        with self._lock:
            self.state = "idle"
            events = list(self._events)
        if trim_stop_key:                            # drop the Enter used to stop from a terminal
            while events and events[-1].get("key") == "Key.enter":
                events.pop()
        return events


def record_from_terminal(name, db_path=None):
    """Simple CLI session: records until you press Enter, then saves to the database."""
    from db import dal
    rec = Recorder()
    rec.start()
    input("Recording... (your actions are being stored locally). Press Enter to stop.\n")
    events = rec.stop(trim_stop_key=True)
    rid = dal.create_recording(name, events, db_path=db_path)
    print(f"Saved recording {rid} with {len(events)} events.")
    return rid
