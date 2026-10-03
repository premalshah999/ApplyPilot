"""Server-rendered portals: Taleo careersection and a SuccessFactors-style single long form."""

import re
import secrets
from urllib.parse import parse_qsl, urlsplit

from . import MockSite, policy_error

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>
<div id="container">{body}</div></body></html>"""


class FormSite(MockSite):
    title = "Careers"

    def page(self, body, cookie=None):
        return {"body": PAGE.format(title=self.title, body=body), "cookie": cookie}

    @staticmethod
    def field(name, label, kind="text", required=True, value="", fid=None):
        star = '<span class="mandatory">*</span>' if required else ""
        return (
            f'<div class="row"><label for="{fid or name}">{label}{star}</label>'
            f'<input type="{kind}" id="{fid or name}" name="{name}" value="{value}"{" required" if required else ""}></div>'
        )

    @staticmethod
    def select(name, label, options, required=True):
        opts = '<option value="">Select...</option>' + "".join(
            f'<option value="{o}">{o}</option>' for o in options
        )
        star = '<span class="mandatory">*</span>' if required else ""
        return (
            f'<div class="row"><label for="{name}">{label}{star}</label>'
            f'<select id="{name}" name="{name}"{" required" if required else ""}>{opts}</select></div>'
        )

    async def handle(self, route):
        request = route.request
        p = urlsplit(request.url)
        if p.hostname != self.host:
            return await route.fallback()
        self.requests.append((request.method, p.path))
        if request.resource_type != "document":
            return await route.fulfill(status=404, body="")
        headers = await request.all_headers()
        sid = re.search(r"sid=([^;]+)", headers.get("cookie", ""))
        user = self.sessions.get(sid[1]) if sid else None
        form = {}
        if request.method == "POST":
            data = request.post_data_buffer or b""
            if "multipart" in headers.get("content-type", ""):
                m = re.search(rb'filename="([^"]+)"', data)
                form["resume"] = m[1].decode() if m else ""
                for name, value in re.findall(rb'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', data):
                    form[name.decode()] = value.decode()
            else:
                form = dict(parse_qsl(data.decode()))
        result = self.route(p.path, dict(parse_qsl(p.query)), form, user)
        extra = {"set-cookie": f"sid={result['cookie']}; Path=/; Secure"} if result.get("cookie") else {}
        if result.get("redirect"):
            body = f'<script>location.replace("{result["redirect"]}")</script>'
            return await route.fulfill(body=body, content_type="text/html", headers=extra)
        return await route.fulfill(body=result["body"], content_type="text/html", headers=extra)


class MockTaleo(FormSite):
    """careersection: Apply Online -> Login (User Name) / New User -> Privacy Agreement -> pages with
    Save and Continue -> Review and Submit -> Thank you."""

    host = "acme.taleo.net"
    title = "Job Description - Software Engineer (12345)"
    cs = "/careersection/2"
    job_url = f"https://{host}{cs}/jobdetail.ftl?job=12345&lang=en"

    def route(self, path, query, form, user):
        cs = self.cs
        if path == f"{cs}/jobdetail.ftl":
            return self.page(
                '<h1 class="titlepage">Software Engineer</h1><div class="editablesection"><p>Join Acme.</p></div>'
                f'<a id="requisitionDescriptionInterface.UP_APPLY_ON_REQ.row1" class="button" href="{cs}/jobapply.ftl?job=12345">Apply Online</a>'
            )
        if path == f"{cs}/jobapply.ftl":
            if not user:
                return {"redirect": f"{cs}/login.jsf?redirectionURI=apply"}
            step = int(query.get("step", "0"))
            return self.flow(step, form, user)
        if path == f"{cs}/login.jsf":
            if form:
                account = self.accounts.get(form.get("username", "").lower())
                if not account or account["password"] != form.get("password"):
                    return self.page(
                        '<div class="error" role="alert">The user name or password is incorrect.</div>'
                        + self.login_form()
                    )
                return {
                    "redirect": f"{cs}/jobapply.ftl?job=12345&step=0",
                    "cookie": self.login(form["username"]),
                }
            return self.page(self.login_form())
        if path == f"{cs}/profile.jsf":
            if form:
                if form.get("password") != form.get("confirm"):
                    return self.page(
                        '<div class="error" role="alert">The passwords do not match.</div>'
                        + self.register_form()
                    )
                if error := policy_error(form.get("password")):
                    return self.page(f'<div class="error" role="alert">{error}</div>' + self.register_form())
                if form.get("username", "").lower() in self.accounts:
                    return self.page(
                        '<div class="error" role="alert">This user name is already taken.</div>'
                        + self.register_form()
                    )
                self.add_account(form["username"], form["password"])
                return {
                    "redirect": f"{cs}/jobapply.ftl?job=12345&step=0",
                    "cookie": self.login(form["username"]),
                }
            return self.page(self.register_form())
        return self.page("<h1>Not found</h1>")

    def login_form(self):
        return (
            '<h1>Login</h1><form method="post" action="/careersection/2/login.jsf">'
            + self.field("username", "User Name", fid="dialogTemplate-dialogForm-login-name1")
            + self.field("password", "Password", "password", fid="dialogTemplate-dialogForm-login-password")
            + '<button type="submit" id="dialogTemplate-dialogForm-login-defaultCmd">Login</button></form>'
            + '<a id="dialogTemplate-dialogForm-login-register" href="/careersection/2/profile.jsf">New User</a>'
            + '<a href="/careersection/2/forgot.jsf">Forgot your password?</a>'
        )

    def register_form(self):
        return (
            '<h1>New User Registration</h1><form method="post" action="/careersection/2/profile.jsf">'
            + self.field("username", "User Name")
            + self.field("password", "Password", "password")
            + self.field("confirm", "Re-enter Password", "password")
            + self.field("email", "Email Address", "email")
            + '<div class="row"><input type="checkbox" id="agree" name="agree" required>'
            + '<label for="agree">I have read and accept the Terms of Use</label></div>'
            + '<button type="submit">Register</button></form>'
        )

    def flow(self, step, form, user):
        action = f"{self.cs}/jobapply.ftl?job=12345&step={step + 1}"
        if form and step > 0:
            self.saves.append({"step": step - 1, **form})
        nav = '<button type="submit" id="et-ef-content-ftf-saveContinueCmdBottom">Save and Continue</button>'
        if step == 0:
            body = (
                "<h1>Privacy Agreement</h1><p>Please read the privacy agreement and accept to continue.</p>"
                f'<form method="post" action="{action}"><button type="submit" id="et-ef-content-ftf-acceptCmd">Accept</button>'
                '<button type="button">Decline</button></form>'
            )
            return self.page(body)
        if step == 1:
            inner = (
                '<h2>Resume Upload</h2><div class="row"><label for="resume">Attach a resume</label>'
                '<input type="file" id="resume" name="resume"></div>'
            )
            return self.page(
                f'<h1>Software Engineer</h1><form method="post" enctype="multipart/form-data" action="{action}">{inner}{nav}</form>'
            )
        if step == 2:
            inner = (
                "<h2>Personal Information</h2>"
                + self.field("first", "First Name")
                + self.field("last", "Last Name")
                + self.field("email", "Email Address", "email")
                + self.field("phone", "Primary Number", "tel")
                + self.select("country", "Country of Residence", ["Canada", "United States"])
                + self.field("city", "City")
            )
            return self.page(
                f'<h1>Software Engineer</h1><form method="post" action="{action}">{inner}{nav}</form>'
            )
        if step == 3:
            inner = (
                "<h2>Questions</h2>"
                + self.select(
                    "authorized", "Are you legally authorized to work in the United States?", ["Yes", "No"]
                )
                + self.select("sponsorship", "Will you require sponsorship?", ["Yes", "No"])
                + self.select("k8s", "Do you have production experience with Kubernetes?", ["Yes", "No"])
            )
            return self.page(
                f'<h1>Software Engineer</h1><form method="post" action="{action}">{inner}{nav}</form>'
            )
        if step == 4:
            body = (
                "<h1>Review and Submit</h1><p>Review your application before submitting.</p>"
                f'<form method="post" action="{action}"><button type="submit" id="et-ef-content-ftf-submitCmdBottom">Submit</button></form>'
            )
            return self.page(body)
        self.submissions.append({"user": user, "saves": list(self.saves)})
        return self.page(
            "<h1>Thank You</h1><p>Your application has been submitted. Application Number 12345.</p>"
        )


class MockSuccessFactors(FormSite):
    """RMK style: posting with "Apply now" -> create account (with country + privacy) -> one long
    application form whose final button reads "Apply"."""

    host = "career4.successfactors.com"
    title = "Software Engineer - Acme"
    job_url = f"https://{host}/career?company=acme&career_job_req_id=777&career_ns=job_listing"

    def route(self, path, query, form, user):
        if query.get("career_ns") == "job_listing" or path == "/job":
            return self.page(
                "<h1>Software Engineer</h1><p>Acme is hiring.</p>"
                '<a class="btn" href="/career?company=acme&career_ns=apply&career_job_req_id=777">Apply now</a>'
            )
        if query.get("career_ns") == "apply":
            if not user:
                return {"redirect": "/career?company=acme&career_ns=account"}
            if form:
                self.submissions.append({"user": user, **form})
                return self.page("<h1>Thank you for applying</h1><p>Your application has been submitted.</p>")
            inner = (
                self.field("first", "First Name")
                + self.field("last", "Last Name")
                + self.field("phone", "Phone Number", "tel")
                + self.field("city", "City")
                + self.select(
                    "source", "How did you hear about us?", ["Company Website", "LinkedIn", "Referral"]
                )
                + self.select(
                    "authorized", "Are you legally authorized to work in the United States?", ["Yes", "No"]
                )
                + '<div class="row"><label for="resume">Resume</label><input type="file" id="resume" name="resume" required></div>'
                + '<div class="row"><input type="checkbox" id="certify" name="certify" required>'
                + '<label for="certify">I certify that the information provided is true and complete.</label></div>'
            )
            return self.page(
                "<h1>Application: Software Engineer</h1>"
                f'<form method="post" enctype="multipart/form-data" action="/career?company=acme&career_ns=apply">{inner}'
                '<button type="submit" class="applyButton">Apply</button></form>'
            )
        if query.get("career_ns") == "account":
            if form:
                email = form.get("email", "").lower()
                if email in self.accounts:
                    return self.page(
                        '<div class="error" role="alert">An account with this email already exists.</div>'
                    )
                if form.get("password") != form.get("confirm") or policy_error(form.get("password")):
                    return self.page(
                        '<div class="error" role="alert">Password must meet the requirements.</div>'
                    )
                self.add_account(email, form["password"])
                token = secrets.token_hex(6)
                self.sessions[token] = email
                return {
                    "redirect": "/career?company=acme&career_ns=apply&career_job_req_id=777",
                    "cookie": token,
                }
            inner = (
                self.field("email", "Email", "email")
                + self.field("password", "Password", "password")
                + self.field("confirm", "Retype Password", "password")
                + self.field("first", "First Name")
                + self.field("last", "Last Name")
                + self.select("country", "Country/Region", ["Canada", "United States"])
                + '<div class="row"><input type="checkbox" id="dpcs" name="dpcs" required>'
                + '<label for="dpcs">I have read and agree to the Data Privacy Statement</label></div>'
            )
            return self.page(
                '<h1>Create an Account</h1><form method="post" action="/career?company=acme&career_ns=account">'
                f'{inner}<button type="submit">Create Account</button></form>'
            )
        return self.page("<h1>Not found</h1>")


class MockEmbeddedAccount(FormSite):
    """A company career site whose single form also creates an account (password + confirm)."""

    host = "careers.acme-industries.com"
    title = "Apply - Software Engineer"
    job_url = f"https://{host}/jobs/42/apply"

    def route(self, path, query, form, user):
        if form:
            self.submissions.append(form)
            return self.page("<h1>Thank you for applying</h1>")
        inner = (
            self.field("first", "First Name")
            + self.field("last", "Last Name")
            + self.field("email", "Email", "email")
            + self.field("phone", "Phone Number", "tel")
            + self.field("city", "City")
            + self.field("password", "Create a Password", "password")
            + self.field("confirm", "Confirm Password", "password")
            + '<div class="row"><label for="resume">Resume</label><input type="file" id="resume" name="resume" required></div>'
        )
        return self.page(
            f'<h1>Software Engineer</h1><form method="post" enctype="multipart/form-data" action="/jobs/42/apply">'
            f'{inner}<button type="submit">Submit</button></form>'
        )
