"""An owned, synthetic application to exercise the browser without provider keys."""

import hashlib
from io import BytesIO
from uuid import uuid4

from pypdf import PdfWriter
from sqlalchemy import select

from .db import Job, Resume
from .discovery import add_job
from .schemas import JobInput


async def create_demo(service, mode):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Alex Example — synthetic test resume"})
    buf = BytesIO()
    writer.write(buf)
    content = buf.getvalue()
    sha = hashlib.sha256(content).hexdigest()
    with service.db.exclusive() as s:
        resume = s.scalar(select(Resume).where(Resume.demo.is_(True)))
        if not resume:
            name = "demo-resume.pdf"
            (service.config.data_dir / "resumes" / name).write_bytes(content)
            resume = Resume(
                name="Synthetic demo resume",
                filename=name,
                sha256=sha,
                text="Synthetic fixture. Never use for real applications.",
                demo=True,
            )
            s.add(resume)
            s.flush()
        rid = resume.id
    nonce = uuid4().hex
    job, _ = add_job(
        service.db,
        JobInput(
            url=f"{service.config.base_url}/demo/form/{nonce}",
            title="Demo · Software Engineer",
            company="Example Company",
            location="Local test",
            description="Synthetic application hosted on this server.",
        ),
        demo=True,
    )
    return await service.queue(job["id"], mode, rid)


FORM = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Example Company · Application</title>
<style>body{font:17px system-ui;max-width:680px;margin:60px auto;padding:20px;color:#142f2e}
label{display:block;margin:20px 0}input,select{display:block;padding:10px;width:95%;margin-top:6px}
button{padding:14px 26px;background:#174f47;color:white;border:0;border-radius:8px}small{color:#667}</style>
<small>APPLYPILOT / SYNTHETIC TEST</small><h1>Software Engineer</h1><p>Example Company · Local browser test</p>
<form method="post" enctype="multipart/form-data">
<label>First name <input name="first_name" required autocomplete="given-name"></label>
<label>Last name <input name="last_name" required autocomplete="family-name"></label>
<label>Email <input name="email" type="email" required></label>
<label>Phone <input name="phone" type="tel" required></label>
<label>Location <input name="city" required></label>
<label>Resume <input name="resume" type="file" required accept=".pdf"></label>
<fieldset><legend>Voluntary self-identification</legend><label>Gender
<select name="gender" required><option value="">Select an option</option><option>Woman</option><option>Man</option>
<option>Prefer not to identify</option></select></label></fieldset>
<button type="submit">Submit application</button></form></html>"""


def valid_demo(db, path):
    with db.session() as s:
        return bool(s.scalar(select(Job.id).where(Job.demo.is_(True), Job.url.endswith(path))))
