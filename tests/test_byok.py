"""Bring-your-own keys, sponsored allowances, prices, and metering.

The envelope, the resolver, the price table and the local-mode routes are
exercised keyless. Storing a key, granting an allowance and charging it need
the account tables, so those run on the Postgres lane only.
"""

from __future__ import annotations

import base64
import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from research_tree import credentials
from research_tree.billing.keywrap import (
    CompositeKeyWrapper,
    LocalAesKeyWrapper,
    forget_key_wrapper,
    key_wrapper_from_env,
)
from research_tree.billing.pricing import cost_usd, price_for
from research_tree.billing.usage import TokenUsage, record_llm_usage, usage_from_response
from research_tree.billing.user_keys import open_secret, seal_secret
from research_tree.principal import LOCAL_PRINCIPAL, Principal, bind_principal, current_binding
from research_tree.services.errors import AllowanceExhaustedError, NoLlmCredentialsError
from test_auth import _postgres_only, _register_and_sign_in, accounts_client  # noqa: F401 - fixture

ACCOUNT = Principal(
    user_id="11111111-1111-1111-1111-111111111111", email="friend@example.com", is_verified=True
)
SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
PLATFORM_KEY = "sk-platform-key-0000000000"


def _kek() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ---- the envelope -----------------------------------------------------------


def test_envelope_round_trip_is_bound_to_the_owner_and_the_row() -> None:
    wrapper = LocalAesKeyWrapper(os.urandom(32))
    sealed = seal_secret(wrapper, user_id="u1", key_id="k1", provider="openai", secret=SECRET)

    assert sealed.kek_id.startswith("local:")
    assert SECRET.encode() not in sealed.ciphertext
    assert open_secret(wrapper, sealed, user_id="u1", key_id="k1", provider="openai") == SECRET
    with pytest.raises(Exception):
        open_secret(wrapper, sealed, user_id="u2", key_id="k1", provider="openai")
    with pytest.raises(Exception):
        open_secret(wrapper, sealed, user_id="u1", key_id="k2", provider="openai")
    stranger = LocalAesKeyWrapper(os.urandom(32))
    with pytest.raises(Exception):
        open_secret(stranger, sealed, user_id="u1", key_id="k1", provider="openai")


def test_a_rotation_wraps_with_the_new_key_and_still_opens_the_old() -> None:
    old = LocalAesKeyWrapper(os.urandom(32))
    new = LocalAesKeyWrapper(os.urandom(32))
    sealed_before = seal_secret(old, user_id="u1", key_id="k1", provider="openai", secret=SECRET)

    both = CompositeKeyWrapper(new, old)
    assert open_secret(both, sealed_before, user_id="u1", key_id="k1", provider="openai") == SECRET
    sealed_after = seal_secret(both, user_id="u1", key_id="k2", provider="openai", secret=SECRET)
    assert sealed_after.kek_id == new.kek_id


def test_the_key_wrapper_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert key_wrapper_from_env() is None
    forget_key_wrapper()
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", _kek())
    assert isinstance(key_wrapper_from_env(), LocalAesKeyWrapper)
    forget_key_wrapper()
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", "not base64!")
    with pytest.raises(RuntimeError):
        key_wrapper_from_env()


# ---- whose key a call spends -------------------------------------------------


def _fake_stores(
    monkeypatch: pytest.MonkeyPatch, *, user_key: str | None = None, status: str = "none"
) -> None:
    monkeypatch.setattr(credentials, "_load_user_key", lambda user_id: user_key)
    monkeypatch.setattr(
        credentials, "_allowance_status", lambda user_id, email, *, verified: status
    )
    monkeypatch.setenv(
        "RESEARCH_TREE_DATABASE_URL", "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"
    )
    credentials.forget_user_key()


def test_the_local_profile_spends_the_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    with bind_principal(LOCAL_PRINCIPAL):
        assert credentials.openai_api_key() == PLATFORM_KEY
        assert current_binding().credential_source is None
    assert credentials.openai_api_key() == PLATFORM_KEY
    monkeypatch.delenv("OPENAI_API_KEY")
    assert credentials.openai_api_key() is None


def test_an_account_key_wins_over_the_platform_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stores(monkeypatch, user_key=SECRET, status="ok")
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    with bind_principal(ACCOUNT):
        assert credentials.openai_api_key() == SECRET
        assert current_binding().credential_source == "byok"


def test_an_allowance_unlocks_the_platform_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stores(monkeypatch, status="ok")
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    with bind_principal(ACCOUNT):
        assert credentials.openai_api_key() == PLATFORM_KEY
        assert current_binding().credential_source == "sponsored"


def test_nothing_to_spend_is_a_402(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    _fake_stores(monkeypatch, status="none")
    with bind_principal(ACCOUNT), pytest.raises(NoLlmCredentialsError) as refused:
        credentials.openai_api_key()
    assert refused.value.status_code == 402

    _fake_stores(monkeypatch, status="exhausted")
    with bind_principal(ACCOUNT), pytest.raises(AllowanceExhaustedError) as used_up:
        credentials.openai_api_key()
    assert used_up.value.status_code == 402
    assert "allowance" in used_up.value.message

    # An allowance is nothing to spend when the server holds no platform key.
    _fake_stores(monkeypatch, status="ok")
    monkeypatch.delenv("OPENAI_API_KEY")
    with bind_principal(ACCOUNT), pytest.raises(NoLlmCredentialsError):
        credentials.openai_api_key()


def test_account_keys_are_cached_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []

    def load(user_id: str) -> str:
        loads.append(user_id)
        return SECRET

    _fake_stores(monkeypatch)
    monkeypatch.setattr(credentials, "_load_user_key", load)
    with bind_principal(ACCOUNT):
        credentials.openai_api_key()
        credentials.openai_api_key()
        assert loads == [ACCOUNT.user_id]
        credentials.forget_user_key(ACCOUNT.user_id)
        credentials.openai_api_key()
        assert len(loads) == 2


# ---- prices and metering ----------------------------------------------------


def test_prices_bill_cached_input_at_the_cached_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cost_usd("gpt-5.6-luna", input_tokens=1_000_000, output_tokens=1_000_000) == Decimal(
        "1.400000"
    )
    assert cost_usd("gpt-5.6-luna", input_tokens=1_000_000, cached_input_tokens=1_000_000) == Decimal(
        "0.020000"
    )
    assert cost_usd("text-embedding-3-large", input_tokens=1_000_000) == Decimal("0.130000")
    # Dated snapshots price like their base model; unknown models like the top tier.
    assert price_for("gpt-5.6-terra-2026-08-01") == price_for("gpt-5.6-terra")
    assert price_for("gpt-7-nova") == price_for("gpt-5.6-sol")

    monkeypatch.setenv("RESEARCH_TREE_MODEL_PRICES", '{"gpt-7-nova": [1, 0.1, 2]}')
    assert cost_usd("gpt-7-nova", input_tokens=1_000_000, output_tokens=1_000_000) == Decimal(
        "3.000000"
    )


def test_usage_is_read_from_both_response_shapes() -> None:
    responses = {
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 20},
        }
    }
    assert usage_from_response(responses) == TokenUsage(120, 100, 30, 20)
    assert usage_from_response({"usage": {"prompt_tokens": 55, "total_tokens": 55}}) == TokenUsage(
        input_tokens=55
    )
    assert usage_from_response({}) == TokenUsage()


def test_metering_ignores_the_local_profile_and_unbound_calls() -> None:
    # No database is configured here: a metering attempt would fail loudly.
    response = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    record_llm_usage(model="gpt-5.6-luna", raw_response=response, label="unbound")
    with bind_principal(LOCAL_PRINCIPAL):
        record_llm_usage(model="gpt-5.6-luna", raw_response=response, label="local")
    with bind_principal(ACCOUNT):
        record_llm_usage(model="gpt-5.6-luna", raw_response=response, label="no database")


# ---- the routes, local profile ----------------------------------------------


def test_local_profile_key_routes_report_the_environment(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-abcdef9f2a")
    keys = client.get("/account/api-keys").json()
    assert keys["openai"]["source"] == "environment"
    assert keys["allowance"] is None
    assert keys["saving_enabled"] is False
    assert keys["platform_key"] is True
    assert client.delete("/account/api-keys").json()["removed"] is False
    usage = client.get("/account/usage").json()
    assert usage["calls"] == 0 and usage["total_usd"] == 0


# ---- the routes and the stores, Postgres lane --------------------------------


def _signed_in_user_id(accounts_client, email: str) -> str:
    _register_and_sign_in(accounts_client, email)
    return accounts_client.get("/account/me").json()["user"]["id"]


def test_saving_a_key_checks_it_stores_it_sealed_and_never_echoes_it(
    accounts_client, repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres_only(repository)
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", _kek())
    forget_key_wrapper()
    user_id = _signed_in_user_id(accounts_client, "keys@example.com")

    malformed = accounts_client.put("/account/api-keys", json={"api_key": "sk-short"})
    assert malformed.status_code == 400
    assert "sk-short" not in malformed.text

    checked: list[str] = []
    monkeypatch.setattr(
        "research_tree.billing.user_keys.validate_openai_key", lambda key: checked.append(key)
    )
    saved = accounts_client.put("/account/api-keys", json={"api_key": SECRET})
    assert saved.status_code == 200, saved.text
    assert saved.json()["stored"] is True
    assert saved.json()["openai"]["masked"] == "sk-…6789"
    assert SECRET not in saved.text
    assert checked == [SECRET]

    keys = accounts_client.get("/account/api-keys").json()
    assert keys["openai"]["configured"] is True
    assert keys["openai"]["masked"] == "sk-…6789"
    assert keys["openai"]["source"] == "account"
    assert keys["saving_enabled"] is True
    with repository._engine.begin() as conn:
        stored = conn.execute(text("SELECT ciphertext, last4 FROM user_api_keys")).all()
    assert len(stored) == 1 and stored[0][1] == "6789"
    assert SECRET.encode() not in bytes(stored[0][0])

    # The resolver spends the account's own key, not the platform one.
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    credentials.forget_user_key()
    with bind_principal(Principal(user_id=user_id, email="keys@example.com", is_verified=True)):
        assert credentials.openai_api_key() == SECRET
        assert current_binding().credential_source == "byok"

    # Replacing retires the old row; removing leaves none.
    replaced = accounts_client.put("/account/api-keys", json={"api_key": SECRET[:-4] + "9999"})
    assert replaced.json()["openai"]["masked"] == "sk-…9999"
    assert accounts_client.delete("/account/api-keys").json()["removed"] is True
    assert accounts_client.get("/account/api-keys").json()["openai"]["configured"] is False
    with repository._engine.begin() as conn:
        active = conn.execute(text("SELECT count(*) FROM user_api_keys WHERE revoked_at IS NULL"))
        assert active.scalar() == 0


def test_saving_a_key_needs_a_key_encryption_key(accounts_client, repository) -> None:
    _postgres_only(repository)
    _signed_in_user_id(accounts_client, "nokek@example.com")
    refused = accounts_client.put("/account/api-keys", json={"api_key": SECRET})
    assert refused.status_code == 503
    assert refused.json()["error_code"] == "byok_unavailable"
    assert SECRET not in refused.text


def test_an_allowance_is_one_number_that_grants_add_to(accounts_client, repository) -> None:
    """Every grant used to make a row and only the oldest was read, so top-ups did nothing."""

    _postgres_only(repository)
    from research_tree.auth.accounts import set_user_flags
    from research_tree.billing.allowances import (
        allowance_status,
        allowance_summary,
        charge_allowance,
        grant_allowance,
    )
    from research_tree.db import get_engine

    user_id = _signed_in_user_id(accounts_client, "topup@example.com")
    set_user_flags(user_id, is_verified=True)
    grant_allowance(email="topup@example.com", limit_usd=Decimal("5.00"), granted_by="test")
    assert allowance_status(user_id, "topup@example.com", verified=True) == "ok"

    with get_engine().begin() as conn:
        charge_allowance(conn, user_id, Decimal("5.00"))
    assert allowance_status(user_id, "topup@example.com", verified=True) == "exhausted"

    grant_allowance(email="topup@example.com", limit_usd=Decimal("10.00"), granted_by="test")
    assert allowance_status(user_id, "topup@example.com", verified=True) == "ok"
    topped_up = allowance_summary(user_id, "topup@example.com", verified=True)
    assert (topped_up["limit_usd"], topped_up["spent_usd"]) == (15.0, 5.0)
    assert topped_up["remaining_usd"] == 10.0

    # And a negative grant takes credit away, without ever undoing what was spent.
    grant_allowance(email="topup@example.com", limit_usd=Decimal("-100.00"), granted_by="test")
    clawed_back = allowance_summary(user_id, "topup@example.com", verified=True)
    assert (clawed_back["limit_usd"], clawed_back["remaining_usd"]) == (5.0, 0.0)
    assert allowance_status(user_id, "topup@example.com", verified=True) == "exhausted"


def test_allowances_attach_to_verified_accounts_and_spend_down(
    accounts_client, repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres_only(repository)
    from research_tree.auth.accounts import set_user_flags
    from research_tree.billing.allowances import (
        allowance_status,
        allowance_summary,
        grant_allowance,
        list_allowances,
        revoke_allowance,
    )

    user_id = _signed_in_user_id(accounts_client, "friend@example.com")
    allowance_id = grant_allowance(
        email="Friend@Example.com", limit_usd=Decimal("1.00"), granted_by="test", note="trial"
    )
    # A password account is unverified until the operator says so: nothing attaches yet.
    assert allowance_status(user_id, "friend@example.com", verified=False) == "none"
    assert accounts_client.get("/account/api-keys").json()["allowance"] is None
    set_user_flags(user_id, is_verified=True)
    assert allowance_status(user_id, "friend@example.com", verified=True) == "ok"
    assert accounts_client.get("/account/api-keys").json()["allowance"]["remaining_usd"] == 1.0

    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    credentials.forget_user_key()
    principal = Principal(user_id=user_id, email="friend@example.com", is_verified=True)
    with bind_principal(principal, feature="test", request_id="r1"):
        assert credentials.openai_api_key() == PLATFORM_KEY
        # 100k input at $5/M plus 20k output at $30/M on Sol: $1.10, past the $1 limit.
        record_llm_usage(
            model="gpt-5.6-sol",
            raw_response={"usage": {"input_tokens": 100_000, "output_tokens": 20_000}},
            label="test call",
        )
    summary = allowance_summary(user_id, "friend@example.com", verified=True)
    assert summary["spent_usd"] == pytest.approx(1.10)
    assert summary["exhausted"] is True
    with bind_principal(principal), pytest.raises(AllowanceExhaustedError):
        credentials.openai_api_key()

    usage = accounts_client.get("/account/usage").json()
    assert usage["calls"] == 1
    assert usage["sponsored_usd"] == pytest.approx(1.10)
    assert usage["recent"][0]["label"] == "test call"
    assert usage["recent"][0]["feature"] == "test"
    assert accounts_client.get("/account/api-keys").json()["allowance"]["exhausted"] is True

    # A monthly allowance starts over a month after its period began.
    with repository._engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE allowances SET period = 'monthly', "
                "period_start = now() - interval '35 days' WHERE id = CAST(:id AS uuid)"
            ),
            {"id": allowance_id},
        )
    assert allowance_status(user_id, "friend@example.com", verified=True) == "ok"
    assert allowance_summary(user_id, "friend@example.com", verified=True)["spent_usd"] == 0

    listed = list_allowances()
    assert [item["id"] for item in listed] == [allowance_id]
    assert listed[0]["linked"] is True and listed[0]["note"] == "trial"
    assert revoke_allowance(allowance_id) is True
    assert revoke_allowance(allowance_id) is False
    assert allowance_status(user_id, "friend@example.com", verified=True) == "none"
    with bind_principal(principal), pytest.raises(NoLlmCredentialsError):
        credentials.openai_api_key()


def test_the_grant_cli_round_trips(accounts_client, repository, capsys) -> None:
    _postgres_only(repository)
    from research_tree.cli.grant_allowance import main

    _signed_in_user_id(accounts_client, "cli@example.com")
    assert main(["--email", "cli@example.com", "--usd", "5", "--monthly", "--note", "friend"]) == 0
    granted = capsys.readouterr().out
    assert "$5.00 monthly to cli@example.com" in granted
    allowance_id = granted.split()[1].rstrip(":")
    assert main(["--list"]) == 0
    listed = capsys.readouterr().out
    assert "cli@example.com" in listed and "waiting for a verified account" in listed
    assert main(["--revoke", allowance_id]) == 0
    assert main(["--revoke", allowance_id]) == 1
    assert main(["--email", "cli@example.com", "--usd", "0"]) == 1
