"""Taleo, SuccessFactors, Eightfold, and other multi-page portals that differ mostly in labels."""

import re

from ..forms import FINAL
from .base import Adapter, Step


class Taleo(Adapter):
    """Oracle Taleo careersection: login/New User, privacy agreement, Save and Continue pages, Submit."""

    id = "taleo"
    name = "Taleo"
    max_steps = 50

    def classify_page(self, obs):
        text = obs["text"]
        buttons = [b["label"].lower() for b in obs["controls"]]
        if re.search(r"privacy (?:agreement|statement)|legal (?:disclaimer|agreement)", text, re.I) and any(
            re.fullmatch(r"(?:i )?accept|agree|i agree", b) for b in buttons
        ):
            fields = [f for f in self.app_fields(obs) if f["type"] != "checkbox"]
            if not fields:
                return Step.CONSENT
        return super().classify_page(obs)

    def is_login_field(self, f):
        # Taleo asks for a "User Name"; the shared email is used as the username.
        return super().is_login_field(f) or bool(re.search(r"user ?name|login name", f["label"], re.I))


class SuccessFactors(Adapter):
    """SAP SuccessFactors / RMK: account page, then one long form whose final button is "Apply"."""

    id = "successfactors"
    name = "SuccessFactors"
    final = re.compile(FINAL.pattern + r"|^apply$|^submit application$", re.I)

    def find_final(self, obs):
        fields = [f for f in self.app_fields(obs) if f["type"] != "checkbox"]
        if len(fields) < 3:
            # On the posting page "Apply" opens the application, it does not submit it.
            hits = [b for b in obs["controls"] if FINAL.search(b["label"])]
            return hits[-1] if len({b["label"].lower() for b in hits}) == 1 else None
        return super().find_final(obs)


class Eightfold(Adapter):
    """Eightfold Talent Intelligence career sites: Apply opens a resume-first form, sometimes an email code."""

    id = "eightfold"
    name = "Eightfold"


class Generic(Adapter):
    id = "custom"
    name = "Career site"
