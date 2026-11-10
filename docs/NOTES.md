# Design notes

- Events are stored as `(type, x, y, key, button, scroll, t_offset_ms)`. Types: `click`, `scroll`, `key_down`, `key_up`, `protected`, `device`.
- Schedules are stored as 5-field cron expressions. Weekdays use names (`mon`..`sun`).
- Pattern mining turns events into action tokens (`click:Button.left`, `type_text`, `key:Key.enter`, `scroll`) and mines frequent sequences with PrefixSpan (fallback: contiguous n-grams).
- Hermes clients call `/api/remote/...` with an `X-Token` header. Permissions (`read`, `run`, `edit`) are set in the dashboard; `edit` is off by default. All calls land in `remote_access_log`.
- Hermes itself is not built here; add a small client library that sends the token over HTTPS.

## AI layer (`ai/`)
- Everything works offline; the optional cloud model (`ai/llm.py`) is off until the user enables it and only receives step labels and counts.
- `nlschedule.py` owns the single rule validator (`validate_rule`). The API, the Copilot and model output all pass through it; it rejects anything more frequent than every 5 minutes.
- `habits.py` converts database timestamps (UTC) to local time before clustering, because schedules fire in local time.
- `assistant.py` returns proposals, never executes. `POST /api/ai/execute` validates again.
- Evidence thresholds are deliberate: a habit needs a dense time window plus repeated weekdays, so coincidences are not suggested.
