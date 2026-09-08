"""Who is acting, carried through a request without being passed around.

The API binds a `Principal` for the duration of each request (see
`research_tree.api.auth`); services and the repository read it back through
`current_principal()` and `acting_user_id()` when they need to record who did
something or whose data to look at. Background work binds the run's owner the
same way. With `RESEARCH_TREE_AUTH_MODE=none` the single implicit local user
is bound instead, which is exactly the pre-accounts behaviour.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Iterator

AUTH_MODE_ENV = "RESEARCH_TREE_AUTH_MODE"
AUTH_MODES = ("accounts", "none")
LOCAL_USER_ID = "local_user"


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False
    is_verified: bool = False
    is_local: bool = False


LOCAL_PRINCIPAL = Principal(
    user_id=LOCAL_USER_ID,
    email="",
    name="Local profile",
    avatar_url=None,
    is_admin=True,
    is_verified=True,
    is_local=True,
)


@dataclass(frozen=True)
class Binding:
    principal: Principal
    feature: str | None = None
    request_id: str | None = None
    # Set by the credential resolver once a model call has chosen whose key
    # it is spending ("byok" or "sponsored"); read back by usage metering.
    credential_source: str | None = None


_binding: ContextVar[Binding | None] = ContextVar("research_tree_principal", default=None)


def auth_mode() -> str:
    value = (os.environ.get(AUTH_MODE_ENV) or "none").strip().lower()
    if value not in AUTH_MODES:
        raise RuntimeError(
            f"{AUTH_MODE_ENV} must be one of {', '.join(AUTH_MODES)}; got {value!r}."
        )
    return value


def current_binding() -> Binding | None:
    return _binding.get()


def current_principal() -> Principal | None:
    binding = _binding.get()
    return binding.principal if binding is not None else None


def acting_user_id() -> str:
    """The id recorded as the actor of a change; the local user when unbound.

    This is also the id a repository is bound to: what the acting principal
    creates is theirs, under this id, whether it is an account or the one
    implicit local user.
    """

    principal = current_principal()
    return principal.user_id if principal is not None else LOCAL_USER_ID


@contextmanager
def bind_principal(
    principal: Principal,
    *,
    feature: str | None = None,
    request_id: str | None = None,
) -> Iterator[Binding]:
    binding = Binding(principal=principal, feature=feature, request_id=request_id)
    token = _binding.set(binding)
    try:
        yield binding
    finally:
        try:
            _binding.reset(token)
        except ValueError:
            # The cleanup of a request-scoped dependency can run in another
            # context than the one that set the value; there is nothing to
            # restore there.
            pass


def set_credential_source(source: str | None) -> None:
    binding = _binding.get()
    if binding is not None:
        _binding.set(replace(binding, credential_source=source))
