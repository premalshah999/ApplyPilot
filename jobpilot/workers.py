from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from croniter import croniter
from dbos import DBOS, SetEnqueueOptions
from sqlalchemy import select

from .db import Job, Run, Source, now, record
from .discovery import discover, rank

SERVICE = None


@DBOS.workflow(max_recovery_attempts=1)
async def apply_workflow(run_id: str):
    await SERVICE.execute(run_id)


@DBOS.workflow(max_recovery_attempts=1)
async def source_workflow(source_id: str):
    service = SERVICE
    with service.db.session() as s:
        row = s.get(Source, source_id)
        if not row:
            return
        source = record(row)
    try:
        ids = await discover(source, service.db, service.config)
        if source["auto_queue"]:
            for job_id in ids[:100]:
                if service.db.get_setting("control", {}).get("paused"):
                    break
                try:
                    result = await rank(service.db, service.config, job_id)
                    if result["eligible"] and (
                        not result["uncertainties"] or service.db.get_setting("profile", {}).get("autonomous")
                    ):
                        await service.queue(job_id, "submit", result["resume_id"])
                except (ValueError, RuntimeError):
                    continue
        with service.db.session() as s:
            row = s.get(Source, source_id)
            row.last_run, row.error, row.found = now(), "", len(ids)
    except Exception as exc:
        with service.db.session() as s:
            row = s.get(Source, source_id)
            if row:
                row.error, row.last_run = f"Source failed: {type(exc).__name__}: {str(exc)[:180]}", now()


@DBOS.workflow(max_recovery_attempts=1)
async def scheduled_tick(scheduled_time: datetime, context: dict):
    if SERVICE.db.get_setting("control", {}).get("paused"):
        return
    current = datetime.now(ZoneInfo(SERVICE.config.timezone))
    due = []
    with SERVICE.db.exclusive() as s:
        for row in s.scalars(select(Source).where(Source.enabled.is_(True))):
            if datetime.fromisoformat(row.next_run) <= current:
                due.append(row.id)
                row.next_run = croniter(row.cron, current).get_next(datetime).isoformat()
    for source_id in due:
        with SetEnqueueOptions(deduplication_id=source_id):
            await DBOS.enqueue_workflow_async("discovery", source_workflow, source_id)


def recover_interrupted(service):
    # This app runs in one server process. An interrupted commit is never replayed.
    with service.db.exclusive() as s:
        for row in s.scalars(select(Run).where(Run.state.in_(["running", "submitting"]))):
            row.state = "submission_unknown" if row.state == "submitting" else "needs_review"
            row.reason, row.finished_at = "Worker interrupted; review before retrying", now()
            s.get(Job, row.job_id).status = row.state


async def start(service):
    global SERVICE
    SERVICE = service
    recover_interrupted(service)
    DBOS(
        config={
            "name": "applypilot-studio",
            "system_database_url": service.config.dbos_database_url,
            "application_version": "studio-1",
        }
    )
    DBOS.launch()
    await DBOS.register_queue_async(
        "applications",
        worker_concurrency=service.config.workers,
        global_concurrency=service.config.workers,
        partition_concurrency=1,
    )
    await DBOS.register_queue_async("discovery", worker_concurrency=1, global_concurrency=1)

    async def dispatch(run_id):
        with service.db.session() as s:
            run = s.get(Run, run_id)
            job = s.get(Job, run.job_id)
            parsed = urlsplit(job.url)
            host = parsed.hostname
            if job.ats in {"greenhouse", "lever", "ashby"}:
                host += "/" + parsed.path.strip("/").split("/")[0]
        # Partitioned DBOS queues do not support dedup IDs. The application row's
        # atomic queued -> running transition provides delivery idempotency.
        with SetEnqueueOptions(queue_partition_key=host):
            await DBOS.enqueue_workflow_async("applications", apply_workflow, run_id)

    service.dispatch = dispatch
    if not await DBOS.get_schedule_async("sources"):
        await DBOS.create_schedule_async(
            schedule_name="sources",
            workflow_fn=scheduled_tick,
            schedule="* * * * *",
            context={},
            cron_timezone=service.config.timezone,
        )
    # Business outbox recovery: enqueue anything committed before a process crash.
    with service.db.session() as s:
        pending = list(s.scalars(select(Run.id).where(Run.state == "queued")))
    for run_id in pending:
        await dispatch(run_id)


async def enqueue_source(source_id):
    with SetEnqueueOptions(deduplication_id=source_id):
        await DBOS.enqueue_workflow_async("discovery", source_workflow, source_id)


async def resume(service):
    with service.db.session() as s:
        ids = list(s.scalars(select(Run.id).where(Run.state == "queued")))
    for run_id in ids:
        # A paused workflow may have completed without starting; use a new delivery ID,
        # while the application state's CAS prevents a second active browser.
        await service.dispatch(run_id)


def stop():
    DBOS.destroy()
