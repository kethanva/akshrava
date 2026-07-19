from akshrava_backend.session_admission import SessionAdmission


def test_session_admission_is_bounded_and_releases_capacity():
    admission = SessionAdmission(2)
    assert admission.try_open()
    assert admission.try_open()
    assert not admission.try_open()
    admission.close()
    assert admission.try_open()
