"""Optional cloud language model (Claude) for the AI features.

OFF by default. Everything in MemoryBox works without it; the model only makes wording and
free-form requests smarter. To turn it on the user must (1) provide an API key (environment
variable ANTHROPIC_API_KEY, or the AI settings page) and (2) explicitly switch "cloud AI" on.

What is sent: recording names, step labels such as "Left-click" / "Type text", counts and
durations. NEVER sent: typed characters, key names, screen coordinates or database contents.
Model answers are treated as untrusted text: schedules are re-validated (ai/nlschedule.py)
and anything that changes state is only a *proposal* the user has to confirm.

Uses only the standard library (urllib), so there is nothing extra to install.
"""
import json
import os
import re
import urllib.error
import urllib.request

from db import dal

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"        # small and fast; change in settings or MEMORYBOX_LLM_MODEL
TIMEOUT_SECONDS = 25


class LLMError(RuntimeError):
    pass


def _default_transport(url, headers, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        raise LLMError(f"Model request failed ({exc.code}). {detail}".strip())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Could not reach the model: {exc}")


class LLM:
    def __init__(self, api_key="", model=DEFAULT_MODEL, enabled=False, transport=_default_transport):
        self.api_key, self.model, self.enabled, self._transport = api_key, model, enabled, transport

    # ------------------------------------------------------------------ config
    @classmethod
    def from_settings(cls, db_path=None, transport=_default_transport):
        key = dal.load_setting("ai_api_key", None, db_path) or os.environ.get("ANTHROPIC_API_KEY", "")
        model = dal.load_setting("ai_model", None, db_path) or os.environ.get("MEMORYBOX_LLM_MODEL", DEFAULT_MODEL)
        enabled = dal.load_setting("ai_cloud_enabled", "0", db_path) == "1"
        return cls(key, model, enabled, transport)

    @property
    def available(self):
        return bool(self.enabled and self.api_key)

    def status(self):
        return {"cloud_enabled": self.enabled, "has_key": bool(self.api_key),
                "key_hint": f"...{self.api_key[-4:]}" if self.api_key else "", "model": self.model,
                "available": self.available}

    # ------------------------------------------------------------------- calls
    def complete(self, system, user, max_tokens=500):
        if not self.available:
            raise LLMError("Cloud AI is switched off or has no API key.")
        headers = {"content-type": "application/json", "x-api-key": self.api_key,
                   "anthropic-version": API_VERSION}
        payload = {"model": self.model, "max_tokens": max_tokens, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        data = self._transport(API_URL, headers, payload, TIMEOUT_SECONDS)
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts).strip()
        if not text:
            raise LLMError("The model returned an empty answer.")
        return text

    def complete_json(self, system, user, max_tokens=500):
        text = self.complete(system, user, max_tokens)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError("The model did not return JSON.")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            raise LLMError("The model returned malformed JSON.")


# ------------------------------------------------------------- task-specific helpers
def polish_summary(llm, summary):
    """Ask the model for a nicer name + one-sentence description of a recording.
    `summary` is the output of ai.describe.summarize (it already contains no typed text)."""
    safe = {"steps": summary["steps"][:25], "clicks": summary["stats"]["clicks"],
            "scrolls": summary["stats"]["scrolls"], "keys_pressed": summary["stats"]["keys_pressed"],
            "duration_seconds": round(summary["stats"]["duration_ms"] / 1000, 1)}
    system = ("You name and describe recorded computer routines for a desktop automation tool. "
              "Reply with JSON only: {\"name\": \"max 5 words\", \"description\": \"one plain sentence\"}. "
              "You only see action labels, never the content typed.")
    out = llm.complete_json(system, json.dumps(safe))
    name = str(out.get("name", "")).strip()[:60]
    desc = str(out.get("description", "")).strip()[:300]
    if not name or not desc:
        raise LLMError("The model's answer was incomplete.")
    return {"name": name, "description": desc}


def schedule_from_text(llm, text):
    """Fallback when the rule-based parser cannot read a phrase. Returns a validated cron rule."""
    from ai import nlschedule
    system = ("Convert the user's scheduling phrase into a 5-field cron expression "
              "(minute hour day-of-month month day-of-week). Use weekday names mon..sun, e.g. "
              "'30 9 * * mon-fri'. Reply with JSON only: {\"rule\": \"...\"}. If the phrase is not a "
              "repeating schedule reply {\"rule\": null}.")
    out = llm.complete_json(system, text[:300], max_tokens=100)
    rule = out.get("rule")
    if not isinstance(rule, str):
        raise nlschedule.ScheduleError("I could not turn that into a repeating schedule.")
    rule = nlschedule.validate_rule(rule)          # never trust the model's output
    return {"rule": rule, "human": nlschedule.humanize_rule(rule),
            "next_runs": [d.isoformat(timespec="minutes") for d in nlschedule.next_runs(rule)],
            "notes": ["Interpreted by the cloud model; check the next run times below."], "source": "cloud"}
