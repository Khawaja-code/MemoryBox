"""Module 9: run report and log views (Phase 9), built from the run and log tables."""
from collections import Counter

from db import dal


def run_report(db_path=None):
    runs = dal.list_runs(100000, db_path)
    names = {r["id"]: r["name"] for r in dal.list_recordings(db_path=db_path)}
    by_status = Counter(r["status"] for r in runs)
    finished = len(runs) - by_status.get("running", 0)
    success = by_status.get("success", 0)
    remote = dal.list_remote_log(100000, db_path)
    return {
        "recordings": len(names),
        "active_schedules": len(dal.list_schedules(active_only=True, db_path=db_path)),
        "total_runs": len(runs),
        "by_status": dict(by_status),
        "success_rate_percent": round(100 * success / finished, 1) if finished else None,
        "recent_failures": [
            {"run_id": r["id"], "recording": names.get(r["recording_id"], "(deleted)"),
             "at": r["started_at"], "message": r["message"]}
            for r in runs if r["status"] == "failed"
        ][:5],
        "remote_calls": len(remote),
        "remote_refused": sum(1 for r in remote if not r["granted"]),
    }


def format_report(report):
    lines = [
        "MemoryBox run report",
        "--------------------",
        f"Recordings:        {report['recordings']}",
        f"Active schedules:  {report['active_schedules']}",
        f"Total runs:        {report['total_runs']}  {report['by_status']}",
        f"Success rate:      {report['success_rate_percent']}%" if report["success_rate_percent"] is not None
        else "Success rate:      n/a (no finished runs yet)",
        f"Remote calls:      {report['remote_calls']} ({report['remote_refused']} refused)",
    ]
    if report["recent_failures"]:
        lines.append("Recent failures:")
        lines += [f"  run {f['run_id']} '{f['recording']}' at {f['at']}: {f['message']}"
                  for f in report["recent_failures"]]
    return "\n".join(lines)


def log_view(kind="runs", limit=50, db_path=None):
    """kind: 'runs' or 'remote'. Returns newest first."""
    if kind == "runs":
        return dal.list_runs(limit, db_path)
    if kind == "remote":
        return dal.list_remote_log(limit, db_path)
    raise ValueError("kind must be 'runs' or 'remote'")
