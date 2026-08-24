from app.mock_exam.content import EXPECTED_LAYOUT, list_approved_sets, validate_exam_set


def test_bootstrap_set_is_complete_and_deployable():
    exams = list_approved_sets()
    assert exams
    exam = exams[0]
    assert [question.number for question in exam.questions] == list(range(1, 12))
    assert validate_exam_set(exam).valid


def test_every_question_matches_the_locked_timing_layout():
    exam = list_approved_sets()[0]
    for question in exam.questions:
        expected_type, preparation, response = EXPECTED_LAYOUT[question.number]
        assert question.question_type == expected_type
        assert question.preparation_seconds == preparation
        assert question.response_seconds == response


def test_all_picture_assets_have_public_license_manifests():
    exam = list_approved_sets()[0]
    assets = {asset.asset_id: asset for asset in exam.assets}
    for question in exam.questions:
        if question.asset_id:
            asset = assets[question.asset_id]
            assert asset.source_url
            assert asset.license_url
            assert asset.author
