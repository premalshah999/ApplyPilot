"""iCIMS career portals (careers-<company>.icims.com).

The public job page wraps the real application in #icims_content_iframe. Loading the frame URL
directly (in_iframe=1) removes a cross-document hop. Flow: Apply -> email -> (sign in | create
profile with password) -> resume upload/parse -> profile -> screening -> EEO -> Submit."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .base import Adapter

APPLY_ICIMS = re.compile(r"apply for this job online|^apply(?: now)?$|apply online", re.I)


class ICIMS(Adapter):
    id = "icims"
    name = "iCIMS"
    apply_ids = ()

    @staticmethod
    def prepare_url(url):
        p = urlsplit(url)
        if not (p.hostname or "").endswith("icims.com") or "/jobs/" not in p.path:
            return url
        query = dict(parse_qsl(p.query))
        query.setdefault("in_iframe", "1")
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), p.fragment))

    def find_apply(self, obs):
        for b in obs["controls"]:
            if APPLY_ICIMS.search(b["label"]) and "linkedin" not in b["label"].lower():
                return b
        return super().find_apply(obs)
