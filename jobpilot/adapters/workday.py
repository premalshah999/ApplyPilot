"""Workday candidate experience (myworkdayjobs.com / myworkdaysite.com).

Contracts used: data-automation-id attributes (adventureButton, applyManually, useMyLastApplication,
autofillWithResume, email/password/verifyPassword, createAccountSubmitButton, signInSubmitButton,
click_filter overlays, bottom-navigation-next-button, progressBar, multiselectInputContainer,
dateInputWrapper, file-upload-input-ref), plus visible headings as a fallback."""

import re

from .base import CHECK_EMAIL, CONFIRM_TEXT, Adapter, Step

STEP_PAGES = {
    "applyFlowMyInfoPage": "my information",
    "applyFlowMyExpPage": "my experience",
    "applyFlowPrimaryQuestionsPage": "application questions",
    "applyFlowSecondaryQuestionsPage": "application questions",
    "applyFlowVoluntaryDisclosuresPage": "voluntary disclosures",
    "applyFlowSelfIdentifyPage": "self identify",
    "applyFlowReviewPage": "review",
}
ADD = {
    "work": re.compile(r"^add(?: another)? work experience$|^add(?: another)? experience$", re.I),
    "education": re.compile(r"^add(?: another)? education$", re.I),
}


class Workday(Adapter):
    id = "workday"
    name = "Workday"
    max_steps = 50
    next_ids = ("bottom-navigation-next-button", "pageFooterNextButton", "wd-CommandButton_next")
    apply_ids = ("adventureButton", "applyButton", "jobPostingApplyButton")

    @staticmethod
    def prepare_url(url):
        # /apply and /apply/applyManually resume the flow; the posting itself is the stable entry.
        return re.sub(r"/apply(?:/[^/?#]*)?(?=$|[?#])", "", url)

    def page_name(self, obs):
        auto = set(obs["automation"])
        for key, name in STEP_PAGES.items():
            if key in auto:
                return name
        heads = " ".join(obs["headings"][:6]).lower()
        for name in (
            "my information",
            "my experience",
            "application questions",
            "voluntary disclosures",
            "self identify",
            "review",
        ):
            if name in heads:
                return name
        return ""

    def classify(self, obs):
        auto = set(obs["automation"])
        text = obs["text"]
        if self.committed and CONFIRM_TEXT.search(text):
            return Step.CONFIRMATION
        if {"applyManually", "autofillWithResume", "useMyLastApplication"} & auto:
            return Step.METHOD
        if "verifyPassword" in auto or "createAccountSubmitButton" in auto:
            return Step.CREATE_ACCOUNT
        if "signInSubmitButton" in auto or ("password" in auto and "email" in auto):
            return Step.SIGN_IN
        code = self.code_fields(obs)
        if code:
            return Step.EMAIL_CODE
        in_flow = bool({"progressBar", "bottom-navigation-next-button", "pageFooterNextButton"} & auto)
        if in_flow:
            if self.page_name(obs) == "review":
                return Step.REVIEW
            return Step.FORM
        if CHECK_EMAIL.search(text) and not obs["fields"]:
            return Step.VERIFY_LINK
        if set(self.apply_ids) & auto and not self.committed:
            return Step.JOB
        return super().classify(obs)

    async def on_apply_method(self, obs):
        by_id = {b["automation_id"]: b for b in obs["controls"] if b["automation_id"]}
        profile = self.e.profile
        account = self.accounts.get(self.realm)
        if (
            "useMyLastApplication" in by_id
            and account
            and account["state"] in {"active", "verified", "reset"}
        ):
            choice = by_id["useMyLastApplication"]
        elif "applyManually" in by_id and (profile.work or "autofillWithResume" not in by_id):
            choice = by_id["applyManually"]
        elif "autofillWithResume" in by_id:
            # No structured history in the profile: let Workday parse the selected resume.
            choice = by_id["autofillWithResume"]
        else:
            choice = by_id.get("applyManually") or next(iter(by_id.values()))
        self.emit("apply_method", f"Workday: {choice['label'] or choice['automation_id']}")
        await self.click(choice)
        self.apply_url = self.page.url

    async def on_form(self, obs):
        if self.page_name(obs) == "my experience":
            await self.add_rows(obs)
        return await super().on_form(obs)

    async def add_rows(self, obs):
        """Open one Work Experience / Education panel per profile entry, keeping existing rows."""
        profile = self.e.profile
        for kind, entries in (("work", profile.work), ("education", profile.education)):
            if not entries:
                continue
            label = "work experience" if kind == "work" else "education"
            for _ in range(len(entries)):
                obs = await self.form.scan()
                groups = {
                    f["group"].lower()
                    for f in obs["fields"]
                    if re.match(label + r" \d+$", (f["group"] or "").lower())
                }
                if len(groups) >= len(entries):
                    break
                add = [b for b in obs["controls"] if ADD[kind].search(b["aria_label"] or b["label"])]
                if not add:
                    add = [
                        b
                        for b in obs["controls"]
                        if re.fullmatch(r"add(?: another)?", b["label"], re.I)
                        and label in (b["aria_label"] or "").lower()
                    ]
                if not add:
                    break
                await self.form.click(add[0]["id"], step_control=True)
