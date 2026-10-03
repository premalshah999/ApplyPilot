from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Evidence(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=8000)


class ReviewedAnswer(BaseModel):
    id: str
    question: str
    answer: str
    options: list[str] = Field(default_factory=list)
    employer: str = ""
    scope: Literal["personal", "employer"] = "employer"
    layer: Literal["fact", "narrative"] = "fact"


class Address(BaseModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    county: str = ""


class WorkEntry(BaseModel):
    company: str = ""
    title: str = ""
    location: str = ""
    # "YYYY-MM" (or "YYYY"). Precision is preserved; missing months are never invented.
    start: str = ""
    end: str = ""
    current: bool = False
    description: str = ""


class EducationEntry(BaseModel):
    school: str = ""
    degree: str = ""
    field_of_study: str = ""
    start: str = ""
    end: str = ""
    gpa: str = ""


# Address facts saved before the structured address existed (fact key -> Address field).
ADDRESS_FACTS = {
    "street_address": "line1",
    "address_line_1": "line1",
    "address_line_2": "line2",
    "city": "city",
    "state": "state",
    "postal_code": "postal_code",
    "zip_code": "postal_code",
    "county": "county",
    "country_of_residence": "country",
    "country": "country",
}


class Profile(BaseModel):
    # Keep keys this version does not know: re-saving a profile must never drop saved data.
    model_config = ConfigDict(extra="allow")

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""
    github: str = ""
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    preferred_name: str = ""
    # E.g. "+1" or "United States of America (+1)" as the employer lists it; matched to offered options.
    phone_country_code: str = ""
    phone_type: str = "Mobile"
    address: Address = Field(default_factory=Address)
    work: list[WorkEntry] = Field(default_factory=list, max_length=30)
    education: list[EducationEntry] = Field(default_factory=list, max_length=15)
    skills: list[str] = Field(default_factory=list, max_length=200)
    languages: list[str] = Field(default_factory=list, max_length=20)
    salary_expectation: str = ""
    availability: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    minimum_fit: int = Field(default=70, ge=0, le=100)
    decline_demographics: bool = True
    approved_consents: list[str] = Field(default_factory=list)
    # Exact question + options answers. No fuzzy reuse of legally meaningful questions.
    approved_answers: dict[str, str] = Field(default_factory=dict)
    reviewed_answers: list[ReviewedAnswer] = Field(default_factory=list, max_length=2000)
    # "How did you hear about us?" policy (e.g. LinkedIn).
    application_source: str = ""
    allow_account_creation: bool = False
    allow_application_consents: bool = False
    accept_all_application_terms: bool = False
    autonomous: bool = False

    @model_validator(mode="before")
    @classmethod
    def migrate_address_facts(cls, data):
        """Confirmed address facts fill the structured address; existing structured values win."""
        if not isinstance(data, dict):
            return data
        facts = data.get("facts") or {}
        address = dict(data.get("address") or {})
        changed = False
        for key, attr in ADDRESS_FACTS.items():
            value = facts.get(key)
            if value not in (None, "") and not address.get(attr):
                address[attr] = str(value)
                changed = True
        if changed:
            data = {**data, "address": address}
        return data

    def given_names(self):
        """First/last names from explicit fields, verified facts, or an unambiguous two-word name."""
        first = self.first_name or str(self.facts.get("first_name") or "")
        last = self.last_name or str(self.facts.get("last_name") or "")
        parts = self.name.split()
        if not first and not last and len(parts) == 2:
            first, last = parts
        return first, last


class JobInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(default="", max_length=500)
    company: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=50000)


class QueueInput(BaseModel):
    mode: Literal["dry_run", "submit"] = "dry_run"
    resume_id: str | None = None


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "career_page"]
    url: str = Field(min_length=8, max_length=2048)
    cron: str = "0 */3 * * *"
    enabled: bool = True
    auto_queue: bool = False

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value):
        from croniter import croniter

        if len(value.split()) != 5 or not croniter.is_valid(value):
            raise ValueError("Use a five-field cron expression")
        return value


class Answer(BaseModel):
    field_id: str
    value: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    disposition: Literal["answer", "skip", "review"]
    reason: str = ""


class AnswerBatch(BaseModel):
    answers: list[Answer]


class FitResult(BaseModel):
    score: int = Field(ge=0, le=100)
    eligible: bool
    resume_id: str | None
    reason: str
    uncertainties: list[str] = Field(default_factory=list)


class KnowledgeNote(BaseModel):
    category: Literal["education", "employment", "skill", "achievement", "project"]
    evidence_id: str
    quote: str = Field(min_length=1, max_length=2500)


class KnowledgeNotes(BaseModel):
    notes: list[KnowledgeNote] = Field(max_length=40)


class KnowledgeInput(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)
    options: list[str] = Field(default_factory=list, max_length=250)
    # "personal" answers apply to every employer; "employer" only to the named one.
    scope: Literal["personal", "employer"] = "personal"
    employer: str = Field(default="", max_length=300)


class ProfileExtract(BaseModel):
    """Structured details the model extracts from an existing resume (never invented)."""

    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    work: list[WorkEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
