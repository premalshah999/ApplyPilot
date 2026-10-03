from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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


class Profile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""
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
    application_source: str = ""
    allow_account_creation: bool = False
    allow_application_consents: bool = False
    accept_all_application_terms: bool = False
    autonomous: bool = False


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
