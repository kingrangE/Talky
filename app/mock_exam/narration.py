"""Piper narration cache used by approved mock-exam sets."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.audio.tts_engine import synthesize
from app.mock_exam.schemas import MockExamSet, MockQuestion, QuestionType
from app.settings import get_settings


def narration_text(question: MockQuestion) -> str:
    if question.question_type in {QuestionType.READ_ALOUD, QuestionType.DESCRIBE_PICTURE}:
        return question.direction
    return question.prompt


def narration_for(exam: MockExamSet, question: MockQuestion) -> bytes | None:
    """Read a pre-generated WAV, generating the deterministic cache on first use if needed."""
    text = narration_text(question)
    digest = hashlib.sha256(
        f"{exam.id}:{exam.version}:{question.number}:{text}".encode()
    ).hexdigest()[:16]
    root = get_settings().mock_exam_data_path / "narration" / exam.id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"q{question.number:02d}-{digest}.wav"
    if path.exists():
        return path.read_bytes()
    audio = synthesize(text, "en")
    if audio:
        path.write_bytes(audio)
    return audio


def pre_generate_narration(exam: MockExamSet) -> list[Path]:
    generated: list[Path] = []
    for question in exam.questions:
        if narration_for(exam, question):
            generated.extend(
                sorted((get_settings().mock_exam_data_path / "narration" / exam.id).glob(f"q{question.number:02d}-*.wav"))
            )
    return generated


if __name__ == "__main__":
    from app.mock_exam.content import list_approved_sets

    for approved_exam in list_approved_sets():
        paths = pre_generate_narration(approved_exam)
        print(f"{approved_exam.id}: {len(paths)} narration files ready")
