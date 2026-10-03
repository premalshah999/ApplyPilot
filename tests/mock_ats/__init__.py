"""High-fidelity mock ATS sites served on their real hostnames through Playwright routing.

They reproduce each vendor's DOM contracts and server behavior (accounts, verification email,
validation, submission) so adapters can be exercised end to end without contacting employers."""

import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from jobpilot.inbox import Message

HERE = Path(__file__).parent
SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:15px Arial;margin:0}}main{{max-width:900px;margin:0 auto;padding:20px}}
[data-automation-id^="formField"]{{margin:12px 0}}label{{font-weight:bold}}
.popup{{position:absolute;background:#fff;border:1px solid #888;z-index:50;min-width:280px}}
.popup li{{list-style:none;padding:4px 8px;cursor:pointer}}.popup ul{{margin:0;padding:0}}
#onetrust-banner-sdk{{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:1000}}
.cf{{position:relative;display:inline-block}}.cf [data-automation-id=click_filter]{{position:absolute;inset:0}}
.modal{{position:fixed;top:80px;left:30%;background:#fff;border:1px solid #333;padding:20px;z-index:60}}
.err{{color:#b00}}</style></head><body>{banner}<main id="app"></main><script src="/mock/{script}"></script></body></html>"""
COOKIE_BANNER = (
    '<div id="onetrust-banner-sdk"><div style="background:#fff;padding:20px;margin-top:300px">'
    'We use cookies to improve your experience. <button id="onetrust-accept-btn-handler">Accept All Cookies</button>'
    "</div></div>"
)
POLICY = [
    (r".{8,}", "Password must be at least 8 characters."),
    (r"[A-Z]", "Password must contain an uppercase letter."),
    (r"[a-z]", "Password must contain a lowercase letter."),
    (r"\d", "Password must contain a numeric character."),
    (r"[^A-Za-z0-9]", "Password must contain a special character."),
]


def policy_error(password):
    for pattern, message in POLICY:
        if not re.search(pattern, password or ""):
            return message
    return None


class MockSite:
    host = ""
    title = "Careers"
    script = ""
    banner = ""

    def __init__(self, inbox):
        self.inbox = inbox
        self.accounts = {}
        self.tokens = {}
        self.sessions = {}
        self.uploads, self.saves, self.submissions = [], [], []
        self.applied = set()
        self.requests = []

    def mail(self, to, sender, subject, text, link=None, link_text="Verify"):
        self.inbox.append(
            Message(
                key="mock-" + secrets.token_hex(6),
                received=time.time(),
                sender=sender,
                recipients=[to],
                subject=subject,
                text=text,
                links=[(link, link_text)] if link else [],
                authenticated=True,
            )
        )

    def add_account(self, email, password, verified=True):
        self.accounts[email.lower()] = {"password": password, "verified": verified}

    def shell(self):
        return SHELL.format(title=self.title, banner=self.banner, script=self.script)

    async def handle(self, route):
        request = route.request
        p = urlsplit(request.url)
        if p.hostname != self.host:
            return await route.fallback()
        self.requests.append((request.method, p.path))
        if p.path == "/mock/" + self.script:
            return await route.fulfill(
                body=(HERE / self.script).read_text(), content_type="application/javascript"
            )
        if p.path.startswith("/mock/api/"):
            body = json.loads(request.post_data or "{}")
            result = self.api(p.path.rsplit("/", 1)[1], body, dict(parse_qsl(p.query)))
            return await route.fulfill(body=json.dumps(result), content_type="application/json")
        if request.resource_type == "document":
            return await route.fulfill(body=self.shell(), content_type="text/html")
        return await route.fulfill(status=404, body="")

    def user(self, body):
        return self.sessions.get(body.get("session", ""))

    def login(self, email):
        token = secrets.token_hex(8)
        self.sessions[token] = email.lower()
        return token


class MockWorkday(MockSite):
    host = "acme.wd5.myworkdayjobs.com"
    title = "Careers at Acme"
    script = "workday.js"
    banner = COOKIE_BANNER
    site = "/en-US/External"
    job_url = f"https://{host}{site}/job/New-York/Software-Engineer_R123"

    def api(self, name, body, query):
        email = (body.get("email") or "").strip().lower()
        if name == "session":
            user = self.user(body)
            return {"signedIn": bool(user), "applied": user in self.applied}
        if name == "create":
            if email in self.accounts:
                return {"error": "An account already exists with this email address. Sign in instead."}
            if error := policy_error(body.get("password")):
                return {"error": error}
            self.add_account(email, body["password"], verified=False)
            token = secrets.token_urlsafe(12)
            self.tokens[token] = ("activate", email)
            self.mail(
                email,
                "acme@myworkday.com",
                "Acme - Verify your candidate account",
                "Thanks for creating an account with Acme. Please verify your email to activate your account.",
                f"https://{self.host}{self.site}/activate/{token}",
                "Verify Account",
            )
            return {"ok": True}
        if name == "activate":
            kind, owner = self.tokens.pop(body.get("token", ""), (None, None))
            if kind != "activate":
                return {"ok": False}
            self.accounts[owner]["verified"] = True
            return {"ok": True}
        if name == "signin":
            account = self.accounts.get(email)
            if not account or account["password"] != body.get("password"):
                return {"error": "Wrong email address or password. Try again, or select Forgot Password."}
            if not account["verified"]:
                return {"error": "Your account is not verified. Check your email for the verification link."}
            return {"ok": True, "session": self.login(email)}
        if name == "forgot":
            if email in self.accounts:
                token = secrets.token_urlsafe(12)
                self.tokens[token] = ("reset", email)
                self.mail(
                    email,
                    "acme@myworkday.com",
                    "Acme - Reset your password",
                    "We received a request to reset your password.",
                    f"https://{self.host}{self.site}/reset/{token}",
                    "Reset Password",
                )
            return {"ok": True}
        if name == "reset":
            kind, owner = self.tokens.pop(body.get("token", ""), (None, None))
            if kind != "reset":
                return {"error": "This link has expired."}
            if error := policy_error(body.get("password")):
                return {"error": error}
            self.accounts[owner].update(password=body["password"], verified=True)
            return {"ok": True}
        user = self.user(body)
        if not user:
            return {"error": "Your session has expired. Sign in again."}
        if name == "upload":
            self.uploads.append(body)
            return {"ok": True}
        if name == "save":
            self.saves.append(body)
            return {"ok": True}
        if name == "submit":
            if user in self.applied:
                return {"error": "You have already applied for this job."}
            self.submissions.append({"user": user, **body})
            self.applied.add(user)
            return {"ok": True}
        return {"error": "Unknown request"}


class MockOracle(MockSite):
    host = "ecsa.fa.us2.oraclecloud.com"
    title = "Acme Careers"
    script = "oracle.js"
    base = "/hcmUI/CandidateExperience/en/sites/CX_1"
    job_url = f"https://{host}{base}/job/12345"

    def __init__(self, inbox):
        super().__init__(inbox)
        self.pins = {}

    def api(self, name, body, query):
        email = (body.get("email") or "").strip().lower()
        if name == "email":
            if email in self.accounts:
                pin = f"{secrets.randbelow(900000) + 100000}"
                self.pins[email] = pin
                self.mail(
                    email,
                    "no-reply@us2.fa.oraclecloud.com",
                    "Acme: Confirm your identity",
                    f"Use this verification code to continue your application with Acme: {pin}. "
                    "The code expires in 10 minutes.",
                )
                return {"pin": True}
            return {"pin": False}
        if name == "pin":
            if self.pins.get(email) != body.get("pin"):
                return {"error": "The code you entered is incorrect."}
            self.pins.pop(email)
            return {"ok": True, "profile": {"firstName": "Alex", "lastName": "Example"}}
        if name == "upload":
            self.uploads.append(body)
            return {"ok": True}
        if name == "submit":
            self.submissions.append(body)
            return {"ok": True}
        return {"error": "Unknown request"}


ICIMS_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Software Engineer at Acme</title></head>
<body><div class="iCIMS_MainWrapper">{body}</div>
<script>for (const s of document.querySelectorAll('select.chosen')) {{
  const box = s.nextElementSibling;
  const show = () => box.querySelector('span').textContent = s.selectedOptions[0]?.text || 'Select';
  s.addEventListener('change', show); show();
}}</script></body></html>"""


class MockICIMS(MockSite):
    host = "careers-acme.icims.com"
    job_path = "/jobs/5678/software-engineer"
    job_url = f"https://{host}{job_path}/job"

    def page(self, body, cookie=None):
        return {"body": ICIMS_PAGE.format(body=body), "cookie": cookie}

    def form(self, step, inner, button="Next", files=False):
        enc = ' enctype="multipart/form-data"' if files else ""
        sep = "&" if "?" in step else "?"
        return (
            f'<form method="post" action="{self.job_path}/{step}{sep}in_iframe=1"{enc}>{inner}'
            f'<button type="submit" class="iCIMS_Button">{button}</button></form>'
        )

    @staticmethod
    def field(name, label, kind="text", required=True, value=""):
        star = ' <span class="iCIMS_Required">*</span>' if required else ""
        req = " required" if required else ""
        return (
            f'<div class="iCIMS_FieldRow"><label for="{name}">{label}{star}</label>'
            f'<input type="{kind}" id="{name}" name="{name}" value="{value}"{req}></div>'
        )

    @staticmethod
    def select(name, label, options, chosen=False, required=True):
        opts = '<option value="">Select</option>' + "".join(
            f'<option value="{o}">{o}</option>' for o in options
        )
        star = ' <span class="iCIMS_Required">*</span>' if required else ""
        if chosen:
            return (
                f'<div class="iCIMS_FieldRow"><label for="{name}">{label}{star}</label>'
                f'<select id="{name}" name="{name}" class="chosen" style="display:none"{" required" if required else ""}>{opts}</select>'
                f'<a class="chosen-container chosen-single" href="#"><span>Select</span></a></div>'
            )
        return (
            f'<div class="iCIMS_FieldRow"><label for="{name}">{label}{star}</label>'
            f'<select id="{name}" name="{name}"{" required" if required else ""}>{opts}</select></div>'
        )

    @staticmethod
    def radios(name, label, options):
        inner = "".join(
            f'<label><input type="radio" name="{name}" value="{o}" required> {o}</label>' for o in options
        )
        return f'<fieldset class="iCIMS_FieldRow"><legend>{label} *</legend>{inner}</fieldset>'

    async def handle(self, route):
        request = route.request
        p = urlsplit(request.url)
        if p.hostname != self.host:
            return await route.fallback()
        self.requests.append((request.method, p.path))
        if request.resource_type != "document":
            return await route.fulfill(status=404, body="")
        headers = await request.all_headers()
        sid = re.search(r"icims_sid=([^;]+)", headers.get("cookie", ""))
        user = self.sessions.get(sid[1]) if sid else None
        query = dict(parse_qsl(p.query))
        form = {}
        if request.method == "POST":
            data = request.post_data_buffer or b""
            if "multipart" in headers.get("content-type", ""):
                m = re.search(rb'filename="([^"]+)"', data)
                form["resume"] = m[1].decode() if m else ""
                form["size"] = len(data)
            else:
                form = dict(parse_qsl(data.decode()))
        result = self.route(p.path, query, form, user)
        extra = {}
        if result.get("cookie"):
            extra["set-cookie"] = f"icims_sid={result['cookie']}; Path=/; Secure"
        if result.get("redirect"):
            # Playwright does not route requests that follow an HTTP redirect; redirect in the page.
            body = f"<script>location.replace({json.dumps(result['redirect'])})</script>"
            return await route.fulfill(body=body, content_type="text/html", headers=extra)
        return await route.fulfill(body=result["body"], content_type="text/html", headers=extra)

    def route(self, path, query, form, user):
        jp = self.job_path
        if path == "/reset":
            if form:
                kind, owner = self.tokens.pop(query.get("token", ""), (None, None))
                if kind != "reset" or form.get("password") != form.get("confirm"):
                    return self.page('<p class="iCIMS_Error">Passwords do not match or the link expired.</p>')
                if error := policy_error(form.get("password")):
                    return self.page(f'<p class="iCIMS_Error">{error}</p>')
                self.accounts[owner]["password"] = form["password"]
                return self.page(
                    f'<h1>Password Reset</h1><p>Your password has been reset.</p><a href="{jp}/login?in_iframe=1">Log In</a>'
                )
            inner = self.field("password", "New Password", "password") + self.field(
                "confirm", "Confirm New Password", "password"
            )
            action = f"/reset?token={query.get('token', '')}"
            return self.page(
                f'<h1>Reset Your Password</h1><form method="post" action="{action}">{inner}<button type="submit">Reset Password</button></form>'
            )
        if path == f"{jp}/job":
            if query.get("in_iframe") != "1":
                return self.page(
                    f'<iframe id="icims_content_iframe" src="{jp}/job?in_iframe=1" width="100%" height="900"></iframe>'
                )
            return self.page(
                '<h1 class="iCIMS_Header">Software Engineer</h1><div class="iCIMS_JobContent"><p>Build our platform. '
                f'Thank you for your interest in Acme.</p></div><a class="iCIMS_ApplyOnlineButton" href="{jp}/login?in_iframe=1">'
                "Apply for this job online</a>"
            )
        if path == f"{jp}/login":
            if not form:
                inner = self.field("email", "Email Address", "email") + (
                    '<div class="iCIMS_FieldRow"><input type="checkbox" id="privacy" name="privacy" required>'
                    '<label for="privacy">I have read and accept the Privacy Policy</label></div>'
                )
                return self.page("<h1>Sign In / Register</h1>" + self.form("login", inner))
            email = form.get("email", "").lower()
            if "password" in form:
                account = self.accounts.get(email)
                if not account or account["password"] != form["password"]:
                    return self.page(
                        '<p class="iCIMS_Error" role="alert">Invalid email or password.</p>'
                        + self.password_form(email)
                    )
                return {"redirect": f"{jp}/candidate?step=resume&in_iframe=1", "cookie": self.login(email)}
            if email in self.accounts:
                return self.page("<h1>Welcome back</h1>" + self.password_form(email))
            inner = (
                self.field("first", "First Name")
                + self.field("last", "Last Name")
                + self.field("email", "Email Address", "email", value=email)
                + self.field("password", "Password", "password")
                + self.field("confirm", "Confirm Password", "password")
            )
            return self.page(
                "<h1>Create a Candidate Account</h1>" + self.form("create", inner, "Create Account")
            )
        if path == f"{jp}/create":
            email = form.get("email", "").lower()
            if form.get("password") != form.get("confirm"):
                return self.page('<p class="iCIMS_Error">Passwords do not match.</p>')
            if error := policy_error(form.get("password")):
                return self.page(f'<p class="iCIMS_Error" role="alert">{error}</p>')
            self.add_account(email, form["password"])
            self.accounts[email]["names"] = (form.get("first"), form.get("last"))
            return {"redirect": f"{jp}/candidate?step=resume&in_iframe=1", "cookie": self.login(email)}
        if path == f"{jp}/forgot":
            if form:
                email = form.get("email", "").lower()
                if email in self.accounts:
                    token = secrets.token_urlsafe(10)
                    self.tokens[token] = ("reset", email)
                    self.mail(
                        email,
                        "donotreply@icims.com",
                        "Acme Careers password reset",
                        "Click the link below to reset your password.",
                        f"https://{self.host}/reset?token={token}",
                        "Reset your password",
                    )
                return self.page(
                    "<h1>Check your email</h1><p>If an account exists, we sent a password reset link.</p>"
                )
            return self.page(
                "<h1>Forgot Password</h1>"
                + self.form("forgot", self.field("email", "Email Address", "email"), "Send Reset Link")
            )
        if path == f"{jp}/candidate":
            if not user:
                return {"redirect": f"{jp}/login?in_iframe=1"}
            step = query.get("step")
            if step == "resume":
                if form:
                    self.uploads.append(form)
                    return {"redirect": f"{jp}/candidate?step=profile&in_iframe=1"}
                inner = '<div class="iCIMS_FieldRow"><label for="resume">Upload Your Resume</label><input type="file" id="resume" name="resume"></div>'
                return self.page(
                    "<h1>Attach a Resume</h1>" + self.form("candidate?step=resume", inner, files=True)
                )
            if step == "profile":
                if form:
                    self.saves.append(form)
                    return {"redirect": f"{jp}/candidate?step=questions&in_iframe=1"}
                first, last = self.accounts[user].get("names", ("", ""))
                inner = (
                    self.field("first", "First Name", value=first or "")
                    + self.field("last", "Last Name", value=last or "")
                    + self.field("phone", "Phone", "tel")
                    + self.field("address", "Address")
                    + self.field("city", "City")
                    + self.select("state", "State", ["California", "New York", "Texas"], chosen=True)
                    + self.field("zip", "Zip Code")
                    + self.select(
                        "education",
                        "Highest Level of Education",
                        ["High School", "Associate's", "Bachelor's", "Master's", "Doctorate"],
                    )
                )
                return self.page("<h1>Candidate Profile</h1>" + self.form("candidate?step=profile", inner))
            if step == "questions":
                if form:
                    self.submissions.append({"user": user, **form})
                    return self.page(
                        "<h1>Thank you for applying!</h1><p>Your application has been submitted.</p>"
                    )
                inner = (
                    self.radios(
                        "authorized",
                        "Are you legally authorized to work in the United States?",
                        ["Yes", "No"],
                    )
                    + self.select(
                        "sponsorship", "Will you require sponsorship now or in the future?", ["Yes", "No"]
                    )
                    + self.select(
                        "source",
                        "How did you hear about this position?",
                        ["Company Website", "Indeed", "LinkedIn", "Other"],
                    )
                    + self.select("k8s", "Do you have production experience with Kubernetes?", ["Yes", "No"])
                )
                return self.page(
                    "<h1>Screening Questions</h1>"
                    + self.form("candidate?step=questions", inner, "Submit Application")
                )
        return self.page("<h1>Page not found</h1>")

    def password_form(self, email):
        inner = self.field("email", "Email Address", "email", value=email) + self.field(
            "password", "Password", "password"
        )
        forgot = f'<a href="{self.job_path}/forgot?in_iframe=1">Forgot Password?</a>'
        return self.form("login", inner, "Log In") + forgot


class Router:
    """One Playwright route handler for several mock hosts."""

    def __init__(self, *sites):
        self.sites = {s.host: s for s in sites}

    async def __call__(self, route):
        host = urlsplit(route.request.url).hostname
        site = self.sites.get(host)
        if site:
            return await site.handle(route)
        return await route.fulfill(status=404, body="unknown host")
