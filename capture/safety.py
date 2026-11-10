"""Safety rule from the guide: never record or inject keys into password fields.

is_protected_focus() FAILS CLOSED. If it cannot tell whether the focused control is a
password field (library missing, not on Windows, error), it answers True and keys are
not recorded or replayed. Install `uiautomation` on Windows for it to work.

NOTE: the UI Automation calls below could not be tested outside Windows. Test them on
a real Windows machine (focus a password box, type, and check nothing is stored).
"""
import logging
import time

log = logging.getLogger("memorybox.safety")
_warned = False
_cache = {"t": 0.0, "value": True}
CACHE_SECONDS = 0.15          # UI Automation is slow; do not query on every single key


def is_protected_focus():
    global _warned
    now = time.monotonic()
    if now - _cache["t"] < CACHE_SECONDS:
        return _cache["value"]
    try:
        import uiautomation as auto
        control = auto.GetFocusedControl()
        value = bool(control and control.GetPropertyValue(auto.PropertyId.IsPasswordProperty))
    except Exception as exc:                    # fail closed
        value = True
        if not _warned:
            log.warning("Cannot check for password fields (%s). Keyboard keys will NOT be "
                        "recorded or replayed. Install 'uiautomation' on Windows.", exc)
            _warned = True
    _cache.update(t=now, value=value)
    return value
