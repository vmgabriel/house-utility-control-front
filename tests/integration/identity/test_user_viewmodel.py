"""Tests for the identity context's ViewModels."""

from src.identity.domain.entities import User
from src.identity.domain.value_objects import UserId
from src.identity.interfaces.viewmodels import UserViewModel


class TestUserViewModel:
    def test_from_domain_maps_plan_display(self):
        user = User(id=UserId("u1"), email="test@example.com", name="Test", plan="pro")
        vm = UserViewModel.from_domain(user)
        assert vm.plan_display == "Pro Plan"
        assert vm.email == "test@example.com"

    def test_unknown_plan_falls_back_to_title_case(self):
        user = User(id=UserId("u1"), email="a@b.com", name="T", plan="lifetime")
        assert UserViewModel.from_domain(user).plan_display == "Lifetime"
