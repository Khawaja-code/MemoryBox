"""Module 4: Playback Engine (Phase 5).

For each event: wait until its timestamp offset (divided by the speed multiplier),
then inject the action. Keys are never injected into password fields.
"""
import time

from capture.keys import str_to_button, str_to_key
from capture.safety import is_protected_focus


class Player:
    def __init__(self, mouse=None, keyboard=None, sleep=time.sleep, protected_check=None,
                 resolve_key=str_to_key, resolve_button=str_to_button, failsafe=True):
        # Controllers and resolvers can be injected, which keeps the logic testable
        # without moving a real mouse.
        # failsafe: move the real mouse to the top-left corner of the screen (0, 0) to stop.
        self._failsafe = failsafe
        self._mouse = mouse
        self._keyboard = keyboard
        self._sleep = sleep
        self._protected = protected_check or is_protected_focus
        self._key = resolve_key
        self._button = resolve_button

    def _ensure_controllers(self):
        if self._mouse is None:
            from pynput.mouse import Controller
            self._mouse = Controller()
        if self._keyboard is None:
            from pynput.keyboard import Controller
            self._keyboard = Controller()

    def _failsafe_tripped(self):
        """True if the user has pushed the mouse into the top-left corner (emergency stop)."""
        if not self._failsafe:
            return False
        pos = self._mouse.position
        return pos is not None and tuple(pos) == (0, 0)

    def _wait(self, seconds, stop_event):
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            self._sleep(seconds)

    def play(self, events, speed=1.0, loops=1, stop_event=None):
        """Replay events. Returns a small stats dict. Set stop_event to cancel."""
        if speed <= 0:
            raise ValueError("speed must be greater than 0")
        self._ensure_controllers()
        stats = {"played": 0, "skipped_protected": 0, "loops_done": 0, "stopped": False}
        for _ in range(loops):
            last = 0
            for event in events:
                if (stop_event is not None and stop_event.is_set()) or self._failsafe_tripped():
                    stats["stopped"] = True
                    return stats
                delay = (event["t_offset_ms"] - last) / 1000.0 / speed
                if delay > 0:
                    self._wait(delay, stop_event)
                last = event["t_offset_ms"]
                self._dispatch(event, stats)
            stats["loops_done"] += 1
        return stats

    def _dispatch(self, e, stats):
        kind = e["type"]
        if kind == "click":
            self._mouse.position = (e["x"], e["y"])
            self._mouse.click(self._button(e["button"]))
        elif kind == "scroll":
            dx, dy = (int(v) for v in (e["scroll"] or "0,0").split(","))
            self._mouse.position = (e["x"], e["y"])
            self._mouse.scroll(dx, dy)
        elif kind in ("key_down", "key_up"):
            if self._protected():                 # safety rule: never type into a password field
                stats["skipped_protected"] += 1
                return
            key = self._key(e["key"])
            (self._keyboard.press if kind == "key_down" else self._keyboard.release)(key)
        elif kind == "protected":
            stats["skipped_protected"] += 1       # a protected entry was skipped on purpose
            return
        elif kind == "device":
            return                                # informational only (could wait for the device)
        stats["played"] += 1
