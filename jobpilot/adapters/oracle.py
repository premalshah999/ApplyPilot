"""Oracle Recruiting Cloud candidate experience (*.oraclecloud.com/hcmUI/CandidateExperience).

Flow: job page "Apply Now" -> email + terms -> optional emailed PIN for returning candidates ->
one or more apply-flow sections (cx-select-pills, oj-select comboboxes, e-signature) -> Submit."""

import re

from .base import CODE_TEXT, CONFIRM_TEXT, Adapter, Step

TERMS = re.compile(r"terms|privacy|agree|consent", re.I)


class Oracle(Adapter):
    id = "oracle"
    name = "Oracle Recruiting"
    next_ids = ("apply-flow-next", "next-button")

    def classify(self, obs):
        text = obs["text"]
        if CONFIRM_TEXT.search(text) and (self.committed or "Apply Now" not in text):
            if len([f for f in obs["fields"] if f["type"] != "checkbox"]) <= 1:
                return Step.CONFIRMATION
        pins = [f for f in obs["fields"] if re.search(r"pin|code|digit", f["label"] + f["name"], re.I)]
        if pins and CODE_TEXT.search(text):
            return Step.EMAIL_CODE
        fields = [f for f in self.app_fields(obs) if f["type"] != "checkbox"]
        emails = [f for f in fields if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I)]
        if emails and len(fields) == len(emails) and any(TERMS.search(f["label"]) for f in obs["fields"]):
            return Step.EMAIL_ENTRY
        return super().classify(obs)

    def code_fields(self, obs):
        found = super().code_fields(obs)
        if found:
            return found
        pins = [
            f for f in obs["fields"] if re.search(r"pin.?code|^pin|digit", f["label"] + " " + f["name"], re.I)
        ]
        return pins if CODE_TEXT.search(obs["text"]) else []
