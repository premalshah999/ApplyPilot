import time
from datetime import datetime, UTC

from jobpilot.receipts import acknowledgement
from test_mail import message


def test_acknowledgement_requires_employer_role_authentication_and_recipient():
    job = {"company": "Example", "title": "AI Engineer"}
    run = {
        "packet": {"profile": {"email": "alex@example.test"}},
        "created_at": datetime.fromtimestamp(time.time() - 30, UTC).isoformat(),
    }
    msg = message(
        "Thank you for applying to Example for the AI Engineer position. We received your application."
    )
    msg["payload"]["headers"].append({"name": "Subject", "value": "Application received"})
    assert acknowledgement(msg, job, run)
    assert acknowledgement(msg, {**job, "company": "Another"}, run) is None
    assert acknowledgement(msg, {**job, "title": "Data Scientist"}, run) is None
    msg["payload"]["headers"][2]["value"] = "mx.google.com; dmarc=fail header.from=notify.example.test"
    assert acknowledgement(msg, job, run) is None


def test_security_code_is_not_a_receipt():
    run = {
        "packet": {"profile": {"email": "alex@example.test"}},
        "created_at": datetime.fromtimestamp(time.time() - 30, UTC).isoformat(),
    }
    msg = message(
        "Thank you for applying to Example as AI Engineer. Enter the security code to submit your application: 12345678"
    )
    assert acknowledgement(msg, {"company": "Example", "title": "AI Engineer"}, run) is None


def test_generic_employer_acknowledgements_with_unique_company():
    run = {
        "packet": {"profile": {"email": "alex@example.test"}},
        "created_at": datetime.fromtimestamp(time.time() - 30, UTC).isoformat(),
    }
    cases = [
        (
            "My Funded Futures",
            "Thanks for applying to MyFunded Futures. Our team will review your background shortly.",
        ),
        ("Zip Co", "Thank you for your application to Zip. Your application landed successfully."),
    ]
    for company, body in cases:
        msg = message(body)
        if company == "Zip Co":
            msg["payload"]["headers"].append(
                {"name": "Subject", "value": "Thank you for your application to Zip"}
            )
        assert acknowledgement(msg, {"company": company, "title": "AI Engineer"}, run, True)
        assert acknowledgement(msg, {"company": company, "title": "AI Engineer"}, run) is None
