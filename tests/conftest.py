import pytest

from annotatiathon.models import Person


@pytest.fixture
def verify_participants():
    """Seed the identity fields normally populated by Supervisely membership sync."""
    def verify(session_factory, *person_ids):
        with session_factory.begin() as db:
            for native_id, person_id in enumerate(person_ids, start=1):
                person = db.get(Person, person_id)
                person.membership_status = "joined"
                person.supervisely_user_id = native_id
                person.supervisely_role = "annotator"
                person.native_verified_at = 1

    return verify
