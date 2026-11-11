from uuid import uuid4

from packages.domain.errors import ErrorBody, ErrorCode


def test_FR_001_25_error_body_related_case_id_is_optional_and_null_by_default() -> None:
    body = ErrorBody(code=ErrorCode.conflict, message="m", fields=[], reference_id="r")
    assert body.related_case_id is None
    assert body.model_dump(mode="json")["related_case_id"] is None
    cid = uuid4()
    with_id = ErrorBody(
        code=ErrorCode.conflict, message="m", fields=[], reference_id="r", related_case_id=cid
    )
    assert with_id.model_dump(mode="json")["related_case_id"] == str(cid)
