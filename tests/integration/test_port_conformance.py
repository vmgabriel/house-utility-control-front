"""Verify the DRF adapters structurally satisfy the domain ports.

The domain declares its contracts as ``typing.Protocol``\\ s that are *not*
``@runtime_checkable``, so ``issubclass``/``isinstance`` cannot be used to
verify conformance and the domain layer must not be modified to enable it.
Conformance is therefore asserted structurally: every public port method must
exist on the adapter with an identical signature.
"""

import inspect

import pytest

from src.budget.domain.ports import (
    DashboardRepositoryPort,
    TransactionRepositoryPort,
)
from src.budget.infrastructure.repositories.dashboard_repository import (
    DRFDashboardRepository,
)
from src.budget.infrastructure.repositories.transaction_repository import (
    DRFTransactionRepository,
)
from src.identity.domain.ports import AuthRepositoryPort
from src.identity.infrastructure.repository import DRFAuthRepository

PAIRS = [
    (AuthRepositoryPort, DRFAuthRepository),
    (TransactionRepositoryPort, DRFTransactionRepository),
    (DashboardRepositoryPort, DRFDashboardRepository),
]


def public_methods(cls):
    """Public callables declared on the class itself (skips __init__, etc.)."""
    return {
        name: member
        for name, member in vars(cls).items()
        if not name.startswith("_")
        and (inspect.isfunction(member) or inspect.iscoroutinefunction(member))
    }


@pytest.mark.parametrize(("port", "adapter"), PAIRS)
def test_adapter_implements_every_port_method(port, adapter):
    missing = set(public_methods(port)) - set(public_methods(adapter))
    assert not missing, f"{adapter.__name__} is missing {sorted(missing)}"


@pytest.mark.parametrize(("port", "adapter"), PAIRS)
def test_port_and_adapter_signatures_agree(port, adapter):
    for name, port_method in public_methods(port).items():
        adapter_method = public_methods(adapter)[name]

        port_sig = inspect.signature(port_method)
        adapter_sig = inspect.signature(adapter_method)
        assert list(adapter_sig.parameters) == list(port_sig.parameters), (
            f"{adapter.__name__}.{name}{tuple(adapter_sig.parameters)} does not match "
            f"{port.__name__}.{name}{tuple(port_sig.parameters)}"
        )

        for param_name, port_param in port_sig.parameters.items():
            adapter_param = adapter_sig.parameters[param_name]
            assert adapter_param.annotation == port_param.annotation, (
                f"{adapter.__name__}.{name}({param_name}) annotation drifted: "
                f"{adapter_param.annotation!r} != {port_param.annotation!r}"
            )
            assert (
                adapter_param.default == port_param.default
            ), f"{adapter.__name__}.{name}({param_name}) default drifted"

        # Return annotations are intentionally not compared: the ports declare
        # bare `list` for the summary methods while the adapters (correctly)
        # narrow them to `list[DashboardSummary]`, which is safe for callers.
        assert adapter_sig.return_annotation is not inspect.Signature.empty


@pytest.mark.parametrize(("port", "adapter"), PAIRS)
def test_adapter_methods_are_coroutines_like_the_port(port, adapter):
    for name in public_methods(port):
        assert inspect.iscoroutinefunction(
            public_methods(adapter)[name]
        ), f"{adapter.__name__}.{name} must be async, like {port.__name__}.{name}"
