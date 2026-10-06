"""Unit tests for `GetAdminStatsUseCase`.

Pure application-layer logic: ports mocked with `AsyncMock`, no framework and
no HTTP in the loop.
"""

from unittest.mock import AsyncMock

import pytest

from src.users.application.use_cases import GetAdminStatsUseCase
from src.users.domain.entities import SystemUser, UserPlan


def _user(plan: UserPlan = UserPlan.FREE, *, is_active: bool = True) -> SystemUser:
    return SystemUser(
        id="u1",
        email="a@b.com",
        full_name="Ana Ruiz",
        plan=plan,
        is_active=is_active,
        is_staff=False,
    )


def _use_case(*users: SystemUser, count: int | None = None) -> GetAdminStatsUseCase:
    repository = AsyncMock()
    repository.list_users.return_value = (
        len(users) if count is None else count,
        list(users),
    )
    return GetAdminStatsUseCase(repository=repository)


class TestCounts:
    async def test_empty_user_base_reports_zeroes(self):
        stats = await _use_case().execute("token")

        assert stats == {
            "total_users": 0,
            "active_users": 0,
            "banned_users": 0,
            "by_plan": {"free": 0, "pro": 0, "premium": 0},
        }

    async def test_active_and_banned_split_on_is_active(self):
        stats = await _use_case(
            _user(is_active=True),
            _user(is_active=True),
            _user(is_active=False),
        ).execute("token")

        assert stats["total_users"] == 3
        assert stats["active_users"] == 2
        assert stats["banned_users"] == 1

    async def test_active_and_banned_always_sum_to_total(self):
        # The two are complements of one set, so a disagreement would mean one of
        # them silently dropped a row.
        users = [_user(is_active=index % 3 != 0) for index in range(9)]
        stats = await _use_case(*users).execute("token")

        assert stats["active_users"] + stats["banned_users"] == stats["total_users"]

    async def test_by_plan_counts_each_member(self):
        stats = await _use_case(
            _user(UserPlan.FREE),
            _user(UserPlan.FREE),
            _user(UserPlan.PRO),
            _user(UserPlan.PREMIUM),
        ).execute("token")

        assert stats["by_plan"] == {"free": 2, "pro": 1, "premium": 1}

    async def test_by_plan_always_has_every_plan_key(self):
        # A missing key would render as a dash on the dashboard rather than a
        # zero, so every member is present whether or not anyone holds it.
        stats = await _use_case(_user(UserPlan.PRO)).execute("token")

        assert set(stats["by_plan"]) == {"free", "pro", "premium"}

    async def test_by_plan_ignores_a_banned_users_plan(self):
        # Bans are counted separately; a banned premium user is still a premium
        # user, and the two cards must not contradict each other.
        stats = await _use_case(
            _user(UserPlan.PREMIUM, is_active=False),
            _user(UserPlan.PREMIUM, is_active=True),
        ).execute("token")

        assert stats["banned_users"] == 1
        assert stats["by_plan"]["premium"] == 2


class TestPagination:
    async def test_asks_for_one_large_page(self):
        # A single request: fetching every page would be N round trips for a
        # count, and the admin list's own 20 would under-report.
        use_case = _use_case()

        await use_case.execute("token")

        _, kwargs = use_case.repository.list_users.call_args
        assert kwargs["page"] == 1
        assert kwargs["page_size"] == GetAdminStatsUseCase.PAGE_SIZE

    async def test_ignores_the_servers_total(self):
        # `count` is the paginator's total across all pages, while `users` is
        # this page only. Using the wrong one would report every user in the
        # deployment while counting only the rows actually fetched.
        use_case = _use_case(_user(), _user(), count=500)

        stats = await use_case.execute("token")

        assert stats["total_users"] == 2

    async def test_propagates_a_repository_failure(self):
        # The dashboard view decides how to degrade; swallowing the error here
        # would make "could not count" indistinguishable from "no users".
        repository = AsyncMock()
        repository.list_users.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await GetAdminStatsUseCase(repository=repository).execute("token")
