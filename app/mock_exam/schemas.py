from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class QuestionType(str, Enum):
    READ_ALOUD = "read_aloud"
    DESCRIBE_PICTURE = "describe_picture"
    RESPOND_QUESTION = "respond_question"
    PROVIDED_INFORMATION = "provided_information"
    EXPRESS_OPINION = "express_opinion"


class AssetLicense(BaseModel):
    asset_id: str
    kind: Literal["image", "audio", "html"]
    source_url: HttpUrl | None = None
    local_path: str | None = None
    author: str
    license_name: str
    license_url: HttpUrl | None = None
    attribution_required: bool = True
    sha256: str | None = None

    @model_validator(mode="after")
    def source_or_local(self) -> "AssetLicense":
        if not self.source_url and not self.local_path:
            raise ValueError("asset needs source_url or local_path")
        return self


class InformationPanel(BaseModel):
    title: str
    subtitle: str | None = None
    headers: list[str]
    rows: list[list[str]]
    note: str | None = None

    @model_validator(mode="after")
    def rectangular_rows(self) -> "InformationPanel":
        if not self.headers or any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("information rows must match header width")
        return self


class MockQuestion(BaseModel):
    number: int = Field(ge=1, le=11)
    question_type: QuestionType
    direction: str
    prompt: str
    preparation_seconds: int = Field(ge=0, le=60)
    response_seconds: int = Field(ge=10, le=60)
    group_read_seconds: int = Field(default=0, ge=0, le=60)
    prompt_repeat_count: int = Field(default=1, ge=1, le=2)
    asset_id: str | None = None
    information_panel: InformationPanel | None = None
    rubric_anchors: list[str] = Field(min_length=1)
    expected_facts: list[str] = Field(default_factory=list)
    narrator_audio_path: str | None = None


class AuditRecord(BaseModel):
    generator_model: str
    reviewer_model: str
    prompt_version: str
    attempts: int = Field(ge=0, le=2)
    deterministic_checks: list[str]
    reviewer_findings: list[str]
    adjudication: Literal["approved", "rejected"]
    approved_by: str
    approved_at: str


class MockExamSet(BaseModel):
    id: str
    version: str
    title: str
    target: str
    status: Literal["candidate", "approved", "rejected"]
    questions: list[MockQuestion]
    assets: list[AssetLicense]
    audit: AuditRecord

    @model_validator(mode="after")
    def complete_exam(self) -> "MockExamSet":
        numbers = [q.number for q in self.questions]
        if numbers != list(range(1, 12)):
            raise ValueError("approved exam set must contain ordered questions 1..11")
        asset_ids = {a.asset_id for a in self.assets}
        missing = {q.asset_id for q in self.questions if q.asset_id and q.asset_id not in asset_ids}
        if missing:
            raise ValueError(f"missing asset manifests: {sorted(missing)}")
        return self


class ValidationFinding(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    question_number: int | None = None
    message: str


class ValidationReport(BaseModel):
    valid: bool
    findings: list[ValidationFinding]
