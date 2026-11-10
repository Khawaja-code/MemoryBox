"""Module 5: Task Scheduler (Phase 6) built on APScheduler.

Every schedule is stored as a 5-field cron expression ("minute hour day month weekday"),
so daily, weekly, monthly, yearly and custom plans all use the same trigger.
Weekdays are written as names (mon..sun) because APScheduler numbers weekdays from
Monday = 0, unlike classic cron.
"""
from db import dal
from playback.runner import run_recording

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ---- pure helpers: turn the user's choice into a cron expression --------------------
def cron_daily(hour, minute):
    return f"{minute} {hour} * * *"


def cron_weekly(day, hour, minute):
    day = day.lower()[:3]
    if day not in DAYS:
        raise ValueError(f"day must be one of {DAYS}")
    return f"{minute} {hour} * * {day}"


def cron_monthly(day_of_month, hour, minute):
    return f"{minute} {hour} {day_of_month} * *"


def cron_yearly(month, day_of_month, hour, minute):
    return f"{minute} {hour} {day_of_month} {month} *"


def validate_cron(expr):
    if len(expr.split()) != 5:
        raise ValueError("cron expression needs 5 fields: minute hour day month weekday")
    return expr


class MemoryScheduler:
    def __init__(self, runner=run_recording, db_path=None):
        from apscheduler.schedulers.background import BackgroundScheduler
        self._sched = BackgroundScheduler()
        self._runner = runner
        self._db = db_path

    def start(self):
        self._sched.start()

    def shutdown(self):
        self._sched.shutdown(wait=False)

    def _job_id(self, schedule_id):
        return f"sched-{schedule_id}"

    def _fire(self, schedule_id, rid, retry):
        self._runner(rid, retries=retry, db_path=self._db)
        self._refresh_next_run(schedule_id)

    def _refresh_next_run(self, schedule_id):
        job = self._sched.get_job(self._job_id(schedule_id))
        nxt = job.next_run_time.isoformat() if job and job.next_run_time else None
        dal.update_next_run(schedule_id, nxt, self._db)

    def _register(self, schedule_id, rid, rule, retry):
        from apscheduler.triggers.cron import CronTrigger
        self._sched.add_job(
            self._fire,
            CronTrigger.from_crontab(validate_cron(rule)),
            args=[schedule_id, rid, retry],
            id=self._job_id(schedule_id),
            replace_existing=True,
            max_instances=1,          # never overlap two runs of the same schedule
            coalesce=True,            # if several were missed, run once
            misfire_grace_time=60,
        )
        self._refresh_next_run(schedule_id)

    def add(self, rid, rule, retry=0):
        """Store and register a schedule. Returns the schedule id."""
        validate_cron(rule)
        schedule_id = dal.add_schedule(rid, rule, retry, self._db)
        self._register(schedule_id, rid, rule, retry)
        return schedule_id

    def remove(self, schedule_id):
        dal.set_schedule_active(schedule_id, False, self._db)
        if self._sched.get_job(self._job_id(schedule_id)):
            self._sched.remove_job(self._job_id(schedule_id))

    def load_from_db(self):
        """Re-register every active schedule after an app restart."""
        for s in dal.list_schedules(active_only=True, db_path=self._db):
            self._register(s["id"], s["recording_id"], s["rule"], s["retry"])
