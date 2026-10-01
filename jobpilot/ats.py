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
    tier: str = "browser adapter; live validation pending"


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
        "Choose Apply Manually or Use My Last Application if available. Reconcile resume parsing. "
        "Navigate My Information, My Experience, Application Questions, Disclosures, Review. "
        "If sign-in or OTP is needed, request session setup; do not invent credentials.",
    ),
    ATS(
        "oracle",
        "Oracle Recruiting",
        ("oraclecloud.com",),
        "Oracle HCM candidate experience often has contact/verification and multipage profile questions. "
        "Use the actual job details/application URL. Request review if email verification is required.",
    ),
    ATS(
        "icims",
        "iCIMS",
        ("icims.com",),
        "Inspect embedded frames. Profile login, separate EEO and screening sections may appear. "
        "Request session setup when authentication is required.",
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
        "Multipage candidate profiles often need login and explicit Save and Continue. Reinspect each page.",
    ),
    ATS("workable", "Workable", ("workable.com",), "Inspect application and screening fields after upload."),
    ATS("bamboohr", "BambooHR", ("bamboohr.com",), "Inspect application fields and document upload status."),
]


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
