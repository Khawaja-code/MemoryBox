"""Module 2: Device Event Monitor (Phase 4). Windows only (needs `WMI` and `pywin32`).

Logs USB / device arrival and removal on the same timeline as the input events.
NOTE: could not be run outside Windows. Test by plugging a USB drive in and out
while a recording is active.
"""
import threading

# Win32_DeviceChangeEvent.EventType values
EVENT_NAMES = {1: "config_changed", 2: "device_arrived", 3: "device_removed", 4: "docking"}


def watch_devices(on_event, stop_event):
    """Blocks, calling on_event(detail) for each device change, until stop_event is set."""
    import pythoncom
    import wmi

    pythoncom.CoInitialize()
    try:
        watcher = wmi.WMI().Win32_DeviceChangeEvent.watch_for()
        while not stop_event.is_set():
            try:
                evt = watcher(timeout_ms=1000)       # wake every second to check stop_event
            except wmi.x_wmi_timed_out:
                continue
            on_event(EVENT_NAMES.get(int(evt.EventType), str(evt.EventType)))
    finally:
        pythoncom.CoUninitialize()


def start_monitor(recorder):
    """Run the watcher in a background thread, feeding events into a Recorder."""
    stop = threading.Event()
    thread = threading.Thread(
        target=watch_devices, args=(recorder.add_device_event, stop), daemon=True
    )
    thread.start()
    return stop            # call stop.set() to end it
