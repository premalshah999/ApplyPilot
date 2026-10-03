import hashlib
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class ATS:
    id: str
    name: str
    domains: tuple[str, ...]
    guidance: str
    tier: str = "fixture-tested adapter; live validation pending"


REGISTRY = [
    ATS(
        "greenhouse",
        "Greenhouse",
        ("greenhouse.io", "greenhouse.com"),
        "Application may be in an iframe. Upload resume, wait for parsing, then verify custom dropdowns and EEO.",
    ),
    ATS(
        "lever",
        "Lever",
        ("lever.co",),
        "Open the /apply form. Verify the resume remains attached after any later edits.",
    ),
    ATS(
        "ashby",
        "Ashby",
        ("ashbyhq.com",),
        "Open Apply. Reinspect conditional questions after selections; comboboxes require committed options.",
    ),
    ATS(
        "workday",
        "Workday",
        ("myworkdayjobs.com", "myworkdaysite.com"),
        "Deterministic adapter: Apply Manually (or Use My Last Application), shared-login sign-in or account "
        "creation with emailed verification, My Information, My Experience rows, questions, disclosures, "
        "self-identify, Review. Use authenticate for sign-in pages and verify_email for codes/links.",
    ),
    ATS(
        "oracle",
        "Oracle Recruiting",
        ("oraclecloud.com",),
        "Deterministic adapter: email + terms, emailed PIN for returning candidates, apply-flow sections with "
        "pills and searchable selects, resume, e-signature, Submit. Use verify_email for the PIN.",
    ),
    ATS(
        "icims",
        "iCIMS",
        ("icims.com",),
        "Deterministic adapter: loads the in_iframe content directly, email step, sign-in or profile "
        "creation with the shared login (reset by email when needed), resume, profile, screening, Submit.",
    ),
    ATS(
        "smartrecruiters",
        "SmartRecruiters",
        ("smartrecruiters.com",),
        "Use the hosted application page, not a JSON API URL. Upload CV and inspect screening questions.",
    ),
    ATS(
        "taleo",
        "Taleo",
        ("taleo.net",),
        "Deterministic adapter: Login with the shared email as user name or New User registration, privacy "
        "agreement, Save and Continue pages, Review and Submit.",
    ),
    ATS("workable", "Workable", ("workable.com",), "Inspect application and screening fields after upload."),
    ATS("bamboohr", "BambooHR", ("bamboohr.com",), "Inspect application fields and document upload status."),
    ATS(
        "eightfold",
        "Eightfold",
        ("eightfold.ai",),
        "Apply opens a resume-first form. Upload, wait for parsing, then verify prefilled fields. "
        "Some tenants email a one-time code.",
    ),
    ATS(
        "successfactors",
        "SAP SuccessFactors",
        ("successfactors.com", "successfactors.eu", "sapsf.com", "jobs2web.com"),
        "Sign in or create a candidate account, accept the data privacy statement, then one long form "
        "whose final button is Apply.",
    ),
    ATS("jobvite", "Jobvite", ("jobvite.com",), "Single or multi-step form; upload resume first."),
    ATS("avature", "Avature", ("avature.net",), "Multi-step portal, often with registration."),
    ATS("phenom", "Phenom", ("phenompeople.com",), "Career site front end; Apply hands off to the real ATS."),
    ATS("adp", "ADP Workforce Now", ("adp.com",), "Multi-step with optional account creation."),
    ATS(
        "ukg",
        "UKG / UltiPro",
        ("ultipro.com", "ukg.com", "ukg.net"),
        "Multi-step; sign in or continue as guest.",
    ),
    ATS("paylocity", "Paylocity", ("paylocity.com",), "Single long form with screening questions."),
    ATS("dayforce", "Dayforce", ("dayforcehcm.com",), "Candidate account and multi-step form."),
    ATS("jazzhr", "JazzHR", ("applytojob.com",), "Single-page form."),
    ATS("breezy", "Breezy HR", ("breezy.hr",), "Single-page form."),
    ATS("recruitee", "Recruitee", ("recruitee.com",), "Single-page form."),
    ATS("teamtailor", "Teamtailor", ("teamtailor.com",), "Single-page form, sometimes with email code."),
    ATS("personio", "Personio", ("personio.de", "personio.com"), "Single-page form."),
    ATS("rippling", "Rippling", ("rippling.com", "rippling-ats.com"), "Single-page form."),
]

# Company career sites often embed or link to the real ATS; the page reveals it.
EMBED_JS = r"""() => {
  const urls = [];
  for (const f of document.querySelectorAll('iframe[src]')) urls.push(['frame', f.src]);
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim();
    if (/^apply|apply now|apply for/i.test(t)) urls.push(['apply', a.href]);
  }
  for (const s of document.querySelectorAll('script[src]')) urls.push(['script', s.src]);
  return urls.slice(0, 400);
}"""


def detect_embedded(entries):
    """Return (ats, url) for an embedded application frame or apply link to a known ATS."""
    for kind in ("frame", "apply", "script"):
        for k, url in entries:
            if k != kind:
                continue
            ats = detect(url)
            if ats.id == "custom":
                continue
            if kind == "script":
                return ats.id, None
            if kind == "frame" and not re.search(r"embed|job_app|in_iframe|apply|jobs/\d|careers", url, re.I):
                continue
            return ats.id, url
    return None, None


def detect(url: str) -> ATS:
    host = (urlsplit(url).hostname or "").lower()
    for ats in REGISTRY:
        if any(host == x or host.endswith("." + x) for x in ats.domains):
            return ats
    return ATS("custom", "Custom career site", (), "Inspect the application flow using observed controls.")


def canonicalize(url: str):
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Provide an http(s) application URL without embedded credentials")
    if detect(url).id == "smartrecruiters" and p.hostname == "api.smartrecruiters.com":
        raise ValueError("Use the applicant-facing SmartRecruiters job URL, not the API URL")
    # Preserve functional query parameters and SPA fragments. Drop tracking only.
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref", "referrer", "sourceid"}
    ]
    path = p.path.rstrip("/")
    ats = detect(url).id
    if ats == "lever":
        path = re.sub(r"/apply$", "", path)
    clean = urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(query)), p.fragment))
    params = dict(query)
    identity = clean
    if ats == "greenhouse":
        match = re.search(r"^/([^/]+)/jobs/(\d+)", path)
        board = match[1] if match else params.get("for")
        job_id = match[2] if match else params.get("token") or params.get("gh_jid")
        if board and job_id:
            identity = f"greenhouse:{board}:{job_id}"
    elif ats == "workday":
        match = re.search(r"/([^/]+)/job/.+_([^/]+)(?:/apply)?$", path)
        if match:
            identity = f"workday:{p.hostname}:{match[1]}:{match[2]}"
    elif ats == "icims":
        match = re.search(r"/jobs/(\d+)(?:/|$)", path)
        if match:
            identity = f"icims:{p.hostname}:{match[1]}"
    if identity == clean and params.get("gh_jid"):
        identity = f"{p.hostname}:greenhouse:{params['gh_jid']}"
    return clean, hashlib.sha256(identity.encode()).hexdigest()


def catalog():
    return [asdict(x) for x in REGISTRY]
