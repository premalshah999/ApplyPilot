import os
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from jobpilot.api import create_app
from jobpilot.config import Settings


@pytest.fixture
def config(tmp_path):
    return Settings(
        data_dir=tmp_path,
        app_token="test-access-token",
        enable_workers=False,
        allow_private_urls=True,
        chromium_path=os.getenv("CHROMIUM_PATH", ""),
    ).prepare()


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def service(app):
    return app.state.service


@pytest.fixture
def client(app, config):
    with TestClient(app, headers={"Authorization": "Bearer " + config.app_token}) as c:
        yield c


@pytest.fixture
def resume_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 40 700 Td (Alex Example - Python engineer with API and database experience) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def prepared(client, service, resume_pdf):
    r = client.post(
        "/api/resumes",
        data={"name": "Backend", "roles": "Python, Backend"},
        files={"file": ("resume.pdf", resume_pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    resume = r.json()
    client.put(
        "/api/profile",
        json={
            "name": "Alex Example",
            "email": "alex@example.test",
            "facts": {"first_name": "Alex", "last_name": "Example"},
        },
    )
    service.config.mimo_api_key = "unit-test-only"
    return resume
