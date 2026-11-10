"""Module 7/8: local REST API + dashboard (Phase 8), with a token-protected remote section for Hermes.

Run (localhost only):
    python cli.py serve            -> dashboard at http://127.0.0.1:8765  (API docs at /docs)

NOTE: this file could not be executed where it was written (FastAPI was not installable
there). Run it, open /docs and try each endpoint, and run `pytest`.
For access from another machine, put it behind HTTPS (reverse proxy or uvicorn
--ssl-keyfile/--ssl-certfile). Never send the token over plain HTTP.
"""
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ai import nlschedule
from ai import service as ai_service
from ai.assistant import Assistant, validate_proposal
from ai.llm import LLM, LLMError, schedule_from_text
from capture.recorder import Recorder
from db import dal
from intelligence.miner import run_miner, save_routine_as_recording
from playback import runner
from reports.report import run_report
from scheduler.scheduler import MemoryScheduler

DASHBOARD_FILE = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
DASHBOARD_ORIGIN = "http://localhost:5173"      # only needed if you later add a React dev server
VALID_SCOPES = {"read", "run", "edit"}
state = {"recorder": None, "name": None, "scheduler": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    dal.init_db()
    if dal.load_setting("hermes_token") is None:
        dal.set_setting("hermes_token", secrets.token_urlsafe(32))
    if dal.load_setting("remote_scope") is None:
        dal.set_setting("remote_scope", "read,run")       # edit rights are OFF by default
    scheduler = MemoryScheduler()
    scheduler.start()
    scheduler.load_from_db()
    state["scheduler"] = scheduler
    yield
    scheduler.shutdown()


app = FastAPI(title="MemoryBox", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[DASHBOARD_ORIGIN],
    allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-MemoryBox-Local"],
)


# ------------------------------------------------------------------ guards
def local_only(x_memorybox_local: str = Header(None)):
    """State-changing local routes need a custom header. A random web page cannot send it
    without a CORS preflight, which the CORS rule above rejects."""
    if x_memorybox_local != "1":
        raise HTTPException(403, "Missing X-MemoryBox-Local header")


def remote_auth(scope: str):
    """Every remote call needs a valid token AND the scope enabled in settings.
    Every attempt, granted or refused, is written to remote_access_log."""
    def dependency(request: Request, x_token: str = Header(None)):
        client = request.client.host if request.client else "unknown"
        token = dal.load_setting("hermes_token") or ""
        token_ok = x_token is not None and secrets.compare_digest(x_token.encode(), token.encode())
        scope_ok = scope in (dal.load_setting("remote_scope") or "").split(",")
        dal.log_remote(client, f"{request.method} {request.url.path}", token_ok and scope_ok)
        if not token_ok:
            raise HTTPException(401, "Unauthorized")
        if not scope_ok:
            raise HTTPException(403, f"Scope '{scope}' is not enabled in settings")
    return dependency


# --------------------------------------------------------------- models
class RecordStart(BaseModel):
    name: str


class ScheduleIn(BaseModel):
    recording_id: int
    rule: str                 # 5-field cron, e.g. "30 9 * * mon"
    retry: int = 0


class SettingsIn(BaseModel):
    remote_scope: Optional[List[str]] = None     # subset of read, run, edit
    regenerate_token: bool = False


class AISettingsIn(BaseModel):
    cloud_enabled: Optional[bool] = None         # explicit consent switch, off by default
    api_key: Optional[str] = None                # "" removes the stored key
    model: Optional[str] = None


class ScheduleText(BaseModel):
    text: str


class AskIn(BaseModel):
    message: str


class ProposalIn(BaseModel):
    action: str                                  # play | schedule | mine
    recording_id: Optional[int] = None
    speed: float = 1.0
    loops: int = 1
    rule: Optional[str] = None
    retry: int = 0


# ----------------------------------------------------------------- dashboard
@app.get("/", include_in_schema=False)
def dashboard():
    if not DASHBOARD_FILE.exists():
        raise HTTPException(404, "dashboard/index.html is missing")
    return FileResponse(DASHBOARD_FILE)


# --------------------------------------------------------------- local routes
@app.get("/api/recordings")
def list_recordings():
    return dal.list_recordings()


@app.get("/api/recordings/{rid}")
def get_recording(rid: int):
    rec = dal.get_recording(rid)
    if rec is None:
        raise HTTPException(404, "Recording not found")
    return rec


def _start_playback(rid: int, speed: float, loops: int, delay: int):
    def job():
        time.sleep(max(0, delay))
        runner.run_recording(rid, speed=speed, loops=loops)

    threading.Thread(target=job, daemon=True).start()


@app.post("/api/recordings/{rid}/play", dependencies=[Depends(local_only)])
def play_recording(rid: int, speed: float = 1.0, loops: int = 1, delay: int = 3):
    """Starts playback after `delay` seconds so you can switch to the target window.
    Emergency stop: move the mouse to the top-left corner, or call /api/play/stop."""
    if dal.get_recording(rid) is None:
        raise HTTPException(404, "Recording not found")
    if speed <= 0 or loops < 1:
        raise HTTPException(422, "speed must be > 0 and loops >= 1")
    _start_playback(rid, speed, loops, delay)
    return {"status": "starting", "recording_id": rid, "delay_seconds": delay}


@app.post("/api/play/stop", dependencies=[Depends(local_only)])
def stop_playback():
    return {"stopped": runner.request_stop()}


@app.get("/api/record/status")
def record_status():
    rec = state["recorder"]
    return {"state": rec.state if rec else "idle", "name": state["name"]}


@app.post("/api/record/start", dependencies=[Depends(local_only)])
def start_recording(body: RecordStart):
    if state["recorder"] is not None:
        raise HTTPException(409, "A recording is already running")
    state["recorder"], state["name"] = Recorder(), body.name
    state["recorder"].start()
    return {"status": "recording", "name": body.name}


@app.post("/api/record/stop", dependencies=[Depends(local_only)])
def stop_recording():
    rec = state["recorder"]
    if rec is None:
        raise HTTPException(409, "No recording is running")
    events = rec.stop()
    # The last click was almost certainly the click on this page's Stop button: drop it.
    for i in range(len(events) - 1, max(len(events) - 4, -1), -1):
        if events[i]["type"] == "click":
            del events[i]
            break
    rid = dal.create_recording(state["name"], events)
    state["recorder"] = state["name"] = None
    return {"recording_id": rid, "events": len(events)}


@app.get("/api/schedules")
def list_schedules():
    return dal.list_schedules()


@app.post("/api/schedules", dependencies=[Depends(local_only)])
def add_schedule(body: ScheduleIn):
    if dal.get_recording(body.recording_id) is None:
        raise HTTPException(404, "Recording not found")
    try:
        rule = nlschedule.validate_rule(body.rule)        # also blocks runaway schedules (e.g. every minute)
        sid = state["scheduler"].add(body.recording_id, rule, body.retry)
    except ValueError as exc:                             # ScheduleError is a ValueError
        raise HTTPException(422, str(exc))
    return {"schedule_id": sid}


@app.post("/api/schedules/{sid}/remove", dependencies=[Depends(local_only)])
def remove_schedule(sid: int):
    state["scheduler"].remove(sid)
    return {"removed": sid}


@app.get("/api/runs")
def list_runs():
    return dal.list_runs()


@app.get("/api/report")
def report():
    return run_report()


@app.get("/api/remote-log")
def remote_log():
    return dal.list_remote_log()


@app.get("/api/routines")
def list_routines():
    return dal.list_routines()


@app.post("/api/routines/mine", dependencies=[Depends(local_only)])
def mine_now(min_support: int = 3):
    return {"found": [{"support": s, "pattern": p} for s, p in run_miner(min_support=min_support)]}


@app.post("/api/routines/{routine_id}/save", dependencies=[Depends(local_only)])
def save_routine(routine_id: int):
    try:
        rid = save_routine_as_recording(routine_id)
    except KeyError:
        raise HTTPException(404, "Routine not found")
    if rid is None:
        raise HTTPException(404, "No contiguous example of this routine was found in your recordings")
    return {"recording_id": rid}


@app.get("/api/settings")
def get_settings():
    token = dal.load_setting("hermes_token") or ""
    return {"remote_scope": (dal.load_setting("remote_scope") or "").split(","),
            "token_hint": f"...{token[-4:]}" if token else ""}


@app.post("/api/settings", dependencies=[Depends(local_only)])
def update_settings(body: SettingsIn):
    """Returns the full token (it is your own machine) so you can copy it into a Hermes client."""
    if body.remote_scope is not None:
        bad = set(body.remote_scope) - VALID_SCOPES
        if bad:
            raise HTTPException(422, f"Unknown scope(s): {sorted(bad)}")
        dal.set_setting("remote_scope", ",".join(body.remote_scope))
    if body.regenerate_token:
        dal.set_setting("hermes_token", secrets.token_urlsafe(32))
    return {"remote_scope": (dal.load_setting("remote_scope") or "").split(","),
            "token": dal.load_setting("hermes_token")}


# ------------------------------------------------------------------ AI features
# Local analysis needs no key and sends nothing anywhere. The optional cloud model only runs
# when the user has switched it on, and only ever sees step labels and counts (see ai/llm.py).
def _llm() -> LLM:
    return LLM.from_settings()


@app.get("/api/ai/status")
def ai_status():
    return {**_llm().status(), "local_features": ["schedule parsing", "recording summaries", "habit detection",
                                                  "next-action prediction", "duplicate detection", "copilot"]}


@app.post("/api/ai/settings", dependencies=[Depends(local_only)])
def ai_update_settings(body: AISettingsIn):
    if body.model is not None:
        model = body.model.strip()
        if model and not re.fullmatch(r"[A-Za-z0-9._:\-]{1,64}", model):
            raise HTTPException(422, "Model name has unexpected characters")
        dal.set_setting("ai_model", model)
    if body.api_key is not None:
        key = body.api_key.strip()
        if len(key) > 300 or any(c.isspace() for c in key):
            raise HTTPException(422, "That does not look like an API key")
        dal.set_setting("ai_api_key", key)
    if body.cloud_enabled is not None:
        dal.set_setting("ai_cloud_enabled", "1" if body.cloud_enabled else "0")
    return _llm().status()                                  # never returns the key itself


@app.get("/api/ai/overview")
def ai_overview():
    return ai_service.overview()


@app.get("/api/ai/recordings/{rid}")
def ai_recording_insights(rid: int):
    try:
        return ai_service.recording_insights(rid)
    except KeyError:
        raise HTTPException(404, "Recording not found")


@app.post("/api/ai/recordings/{rid}/polish", dependencies=[Depends(local_only)])
def ai_polish_recording(rid: int):
    """Same insights, with the name and description rewritten by the cloud model (if enabled)."""
    try:
        return ai_service.recording_insights(rid, llm=_llm(), use_llm=True)
    except KeyError:
        raise HTTPException(404, "Recording not found")


@app.get("/api/recordings/{rid}/timeline")
def recording_timeline(rid: int):
    try:
        return ai_service.timeline(rid)
    except KeyError:
        raise HTTPException(404, "Recording not found")


@app.post("/api/ai/parse-schedule", dependencies=[Depends(local_only)])
def ai_parse_schedule(body: ScheduleText):
    try:
        return nlschedule.parse_schedule(body.text)
    except nlschedule.ScheduleError as exc:
        llm = _llm()
        if llm.available and body.text.strip():
            try:
                return schedule_from_text(llm, body.text)
            except (LLMError, nlschedule.ScheduleError):
                pass
        raise HTTPException(422, str(exc))


@app.post("/api/ai/ask", dependencies=[Depends(local_only)])
def ai_ask(body: AskIn):
    """Copilot. Returns a reply and, for actions, a *proposal* the user must confirm."""
    try:
        return Assistant(llm=_llm()).handle(body.message)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/ai/execute", dependencies=[Depends(local_only)])
def ai_execute(body: ProposalIn):
    """Runs a confirmed proposal. It is validated again here, whatever produced it."""
    try:
        p = validate_proposal(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if p["action"] == "play":
        _start_playback(p["recording_id"], p["speed"], p["loops"], 3)
        return {"ok": True, "message": "Starting in 3 seconds. Switch to the target window. Mouse to the top-left corner = stop."}
    if p["action"] == "schedule":
        try:
            sid = state["scheduler"].add(p["recording_id"], p["rule"], p["retry"])
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True, "message": f"Scheduled: {p['human']}.", "schedule_id": sid}
    found = run_miner(min_support=3)
    return {"ok": True, "message": f"Found {len(found)} routine(s)." if found else "No routines yet. Record the same task a few times.",
            "found": len(found)}


# -------------------------------------------------- remote routes (Hermes clients)
@app.get("/api/remote/recordings", dependencies=[Depends(remote_auth("read"))])
def remote_list():
    return dal.list_recordings()


@app.post("/api/remote/run/{rid}", dependencies=[Depends(remote_auth("run"))])
def remote_run(rid: int):
    if dal.get_recording(rid) is None:
        raise HTTPException(404, "Recording not found")
    threading.Thread(target=runner.run_recording, args=(rid,), daemon=True).start()
    return {"status": "started", "recording_id": rid}


@app.post("/api/remote/stop", dependencies=[Depends(remote_auth("run"))])
def remote_stop():
    return {"stopped": runner.request_stop()}
