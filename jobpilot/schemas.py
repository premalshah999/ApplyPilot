from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=8000)


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


class Profile(BaseModel):
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
    # "How did you hear about us?" default.
    referral_source: str = "LinkedIn"
    salary_expectation: str = ""
    availability: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    minimum_fit: int = Field(default=70, ge=0, le=100)
    decline_demographics: bool = True
    # Accept standard privacy/terms/data-processing/accuracy-certification checkboxes automatically.
    auto_accept_consents: bool = True
    approved_consents: list[str] = Field(default_factory=list)
    # Exact question + options answers. No fuzzy reuse of legally meaningful questions.
    approved_answers: dict[str, str] = Field(default_factory=dict)

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


class KnowledgeInput(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)
    options: list[str] = Field(default_factory=list, max_length=250)
    scope: str = Field(default="global", max_length=300)


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
