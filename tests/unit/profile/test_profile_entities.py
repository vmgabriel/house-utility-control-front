"""Unit tests for the profile domain entity."""

from src.profile.domain.entities import UserProfile


def make_profile(**overrides) -> UserProfile:
    base = dict(
        id="user-123",
        first_name="Test",
        last_name="User",
        timezone="UTC",
        language="es",
        currency="USD",
        date_format="YYYY-MM-DD",
        avatar_url="https://example.com/a.png",
        bio="hello",
    )
    base.update(overrides)
    return UserProfile(**base)


class TestUserProfile:
    def test_clear_avatar_returns_new_instance_with_empty_string(self):
        profile = make_profile()
        cleared = profile.clear_avatar()
        assert cleared.avatar_url == ""
        assert profile.avatar_url == "https://example.com/a.png"

    def test_clear_bio_returns_new_instance_with_empty_string(self):
        profile = make_profile()
        cleared = profile.clear_bio()
        assert cleared.bio == ""
        assert profile.bio == "hello"
