from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.mock_exam.schemas import MockExamSet, QuestionType, ValidationFinding, ValidationReport
from app.settings import get_settings


EXPECTED_LAYOUT = {
    1: (QuestionType.READ_ALOUD, 45, 45),
    2: (QuestionType.READ_ALOUD, 45, 45),
    3: (QuestionType.DESCRIBE_PICTURE, 45, 30),
    4: (QuestionType.DESCRIBE_PICTURE, 45, 30),
    5: (QuestionType.RESPOND_QUESTION, 3, 15),
    6: (QuestionType.RESPOND_QUESTION, 3, 15),
    7: (QuestionType.RESPOND_QUESTION, 3, 30),
    8: (QuestionType.PROVIDED_INFORMATION, 3, 15),
    9: (QuestionType.PROVIDED_INFORMATION, 3, 15),
    10: (QuestionType.PROVIDED_INFORMATION, 3, 30),
    11: (QuestionType.EXPRESS_OPINION, 45, 60),
}


def validate_exam_set(exam: MockExamSet) -> ValidationReport:
    findings: list[ValidationFinding] = []
    for q in exam.questions:
        expected_type, prep, response = EXPECTED_LAYOUT[q.number]
        if q.question_type != expected_type:
            findings.append(ValidationFinding(
                code="wrong_type", severity="error", question_number=q.number,
                message=f"expected {expected_type.value}, got {q.question_type.value}",
            ))
        if q.preparation_seconds != prep or q.response_seconds != response:
            findings.append(ValidationFinding(
                code="wrong_timing", severity="error", question_number=q.number,
                message=f"expected {prep}s/{response}s",
            ))
        if q.question_type == QuestionType.DESCRIBE_PICTURE and not q.asset_id:
            findings.append(ValidationFinding(
                code="missing_picture", severity="error", question_number=q.number,
                message="picture-description question requires an image asset",
            ))
        if q.question_type == QuestionType.PROVIDED_INFORMATION and not q.information_panel:
            findings.append(ValidationFinding(
                code="missing_information", severity="error", question_number=q.number,
                message="provided-information question requires a deterministic panel",
            ))
    if exam.questions[7].group_read_seconds != 45:
        findings.append(ValidationFinding(
            code="missing_group_read_time", severity="error", question_number=8,
            message="question 8 must begin with the 45-second information review",
        ))
    if exam.questions[9].prompt_repeat_count != 2:
        findings.append(ValidationFinding(
            code="missing_repeat", severity="error", question_number=10,
            message="question 10 prompt must be played twice",
        ))
    for asset in exam.assets:
        if asset.kind == "image" and not asset.license_name:
            findings.append(ValidationFinding(
                code="missing_license", severity="error",
                message=f"asset {asset.asset_id} has no license",
            ))
    return ValidationReport(valid=not any(f.severity == "error" for f in findings), findings=findings)


def _set_dir() -> Path:
    return Path(get_settings().MOCK_EXAM_SET_DIR)


@lru_cache(maxsize=16)
def load_exam_set(set_id: str = "workplace-basics-001") -> MockExamSet:
    candidates = sorted(_set_dir().glob("*.json"))
    for path in candidates:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("id") != set_id:
            continue
        exam = MockExamSet.model_validate(raw)
        report = validate_exam_set(exam)
        if exam.status != "approved" or not report.valid:
            details = "; ".join(f.message for f in report.findings)
            raise ValueError(f"exam set {set_id} is not deployable: {details}")
        return exam
    raise FileNotFoundError(f"mock exam set not found: {set_id}")


def list_approved_sets() -> list[MockExamSet]:
    exams: list[MockExamSet] = []
    for path in sorted(_set_dir().glob("*.json")):
        exam = MockExamSet.model_validate_json(path.read_text(encoding="utf-8"))
        if exam.status == "approved" and validate_exam_set(exam).valid:
            exams.append(exam)
    return exams
