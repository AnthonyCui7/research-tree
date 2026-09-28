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
from research_tree.llm import call_responses_api
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


# ---- what leaves for OpenAI, and what comes back refused ----------------------


def _openai_refusal(status: int, code: str, message: str = "no"):
    import json
    from io import BytesIO
    from urllib.error import HTTPError

    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    return HTTPError("https://api.openai.com/v1/responses", status, "refused", None, BytesIO(body))


@pytest.mark.parametrize(
    ("status", "code", "message", "said"),
    [
        (401, "invalid_api_key", "Incorrect API key provided: sk-proj-****wxyz", "refused your API key"),
        (429, "insufficient_quota", "You exceeded your current quota", "out of credit"),
        (429, "rate_limit_exceeded", "Request too large for the model on tokens per min", "rate limit"),
        (403, "model_not_found", "Project does not have access to model", "cannot use the model"),
    ],
)
def test_a_refusal_only_the_key_owner_can_fix_says_so_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str, message: str, said: str
) -> None:
    from research_tree import llm
    from research_tree.services.errors import ProviderRefusedError

    attempts: list[int] = []

    def refuse(*_args: object, **_kwargs: object) -> None:
        attempts.append(1)
        raise _openai_refusal(status, code, message)

    monkeypatch.setattr(llm, "_post", refuse)
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    with pytest.raises(ProviderRefusedError) as raised:
        call_responses_api({"model": "gpt-5.6-luna"}, api_key=SECRET, timeout_seconds=5, label="test")
    assert said in raised.value.message
    assert "sk-" not in raised.value.message
    assert attempts == [1]


def test_a_refused_platform_key_is_the_operators_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_tree import llm
    from research_tree.principal import set_credential_source
    from research_tree.services.errors import ServiceUnavailableError

    monkeypatch.setattr(
        llm, "_post", lambda *_a, **_k: (_ for _ in ()).throw(_openai_refusal(401, "invalid_api_key"))
    )
    with bind_principal(ACCOUNT):
        set_credential_source("sponsored")
        with pytest.raises(ServiceUnavailableError) as raised:
            call_responses_api(
                {"model": "gpt-5.6-luna"}, api_key=PLATFORM_KEY, timeout_seconds=5, label="test"
            )
    assert "key" not in raised.value.message.casefold()


def test_a_dropped_connection_is_tried_again_and_a_timeout_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client

    from research_tree import llm

    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    outcomes = iter(
        [ConnectionResetError("reset"), http.client.IncompleteRead(b"{"), {"output": []}]
    )

    def flaky(*_args: object, **_kwargs: object) -> object:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm, "_post", flaky)
    assert call_responses_api(
        {"model": "gpt-5.6-luna"}, api_key=SECRET, timeout_seconds=5, label="test"
    ) == {"output": []}

    attempts: list[int] = []

    def slow(*_args: object, **_kwargs: object) -> None:
        attempts.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(llm, "_post", slow)
    with pytest.raises(llm.LlmRequestError):
        call_responses_api({"model": "gpt-5.6-luna"}, api_key=SECRET, timeout_seconds=5, label="test")
    assert attempts == [1]


def test_a_request_carrying_a_key_does_not_follow_a_redirect() -> None:
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.error import HTTPError

    from research_tree.llm import open_openai_request

    seen_elsewhere: list[str | None] = []

    class Elsewhere(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen_elsewhere.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    elsewhere = HTTPServer(("127.0.0.1", 0), Elsewhere)

    class Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{elsewhere.server_port}/")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    redirecting = HTTPServer(("127.0.0.1", 0), Redirecting)
    for server in (elsewhere, redirecting):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{redirecting.server_port}/",
            headers={"Authorization": f"Bearer {SECRET}"},
        )
        with pytest.raises(HTTPError) as raised:
            open_openai_request(request, timeout_seconds=5)
        assert raised.value.code == 302
        assert seen_elsewhere == []
    finally:
        for server in (elsewhere, redirecting):
            server.shutdown()
            server.server_close()


def test_a_key_inside_an_exception_does_not_reach_the_log() -> None:
    import io
    import logging

    from research_tree.log_scrub import SecretScrubFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretScrubFilter())
    logger = logging.getLogger("test.scrub.traceback")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise ValueError(f"Invalid header value b'Bearer {SECRET}'")
        except ValueError:
            logger.exception("call failed")
    finally:
        logger.removeHandler(handler)
    assert "Traceback" in stream.getvalue()
    assert SECRET not in stream.getvalue()


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
        # The rows stay as history; what could be opened does not.
        retired = conn.execute(
            text("SELECT last4, length(ciphertext), length(nonce), length(wrapped_dek) FROM user_api_keys")
        ).all()
    assert sorted(row[0] for row in retired) == ["6789", "9999"]
    assert all(tuple(row[1:]) == (0, 0, 0) for row in retired)


def test_a_saved_key_that_cannot_be_opened_is_an_error_not_an_absence(
    accounts_client, repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account chose its own key; that choice holds when the key cannot be read."""

    from research_tree.billing.allowances import grant_allowance
    from research_tree.services.errors import StoredKeyUnreadableError

    _postgres_only(repository)
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", _kek())
    forget_key_wrapper()
    user_id = _signed_in_user_id(accounts_client, "vault@example.com")
    monkeypatch.setattr("research_tree.billing.user_keys.validate_openai_key", lambda key: None)
    assert accounts_client.put("/account/api-keys", json={"api_key": SECRET}).status_code == 200
    grant_allowance(email="vault@example.com", limit_usd=Decimal("5"))
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)

    # The key-encryption key changes underneath the row, as a vault outage or
    # a lost KEK would look from here.
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", _kek())
    forget_key_wrapper()
    with bind_principal(Principal(user_id=user_id, email="vault@example.com", is_verified=True)):
        with pytest.raises(StoredKeyUnreadableError) as refused:
            credentials.openai_api_key()
        assert current_binding().credential_source is None
    assert SECRET not in refused.value.message
    review = accounts_client.post("/workspaces/topic-review", json={"topic": "prompting"})
    assert review.status_code == 503
    assert review.json()["error_code"] == "stored_key_unreadable"


def test_the_vault_is_asked_once_for_each_data_key() -> None:
    from types import SimpleNamespace

    from research_tree.billing.keywrap import KeyVaultKeyWrapper

    asked: list[bytes] = []

    class Vault:
        def unwrap_key(self, _algorithm: object, wrapped: bytes) -> SimpleNamespace:
            asked.append(wrapped)
            return SimpleNamespace(key=b"k" * 32)

    wrapper = KeyVaultKeyWrapper("https://vault.example.net", "byok-kek", credential=object())
    kek_id = "https://vault.example.net/keys/byok-kek/version1"
    wrapper._clients[kek_id] = Vault()

    for _ in range(50):
        assert wrapper.unwrap(b"wrapped-one", kek_id) == b"k" * 32
    wrapper.unwrap(b"wrapped-two", kek_id)
    assert asked == [b"wrapped-one", b"wrapped-two"]


def test_saving_a_key_needs_a_key_encryption_key(accounts_client, repository) -> None:
    _postgres_only(repository)
    _signed_in_user_id(accounts_client, "nokek@example.com")
    refused = accounts_client.put("/account/api-keys", json={"api_key": SECRET})
    assert refused.status_code == 503
    assert refused.json()["error_code"] == "byok_unavailable"
    assert SECRET not in refused.text


def test_key_checks_are_bounded_per_account(
    accounts_client, repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The save route sends the key to OpenAI, so a script trying a list is stopped."""

    from research_tree.auth import throttle

    _postgres_only(repository)
    monkeypatch.setenv("RESEARCH_TREE_KEY_ENCRYPTION_KEY", _kek())
    forget_key_wrapper()
    _signed_in_user_id(accounts_client, "prolific@example.com")
    checked: list[str] = []
    monkeypatch.setattr(
        "research_tree.billing.user_keys.validate_openai_key", lambda key: checked.append(key)
    )

    limit, _window = throttle.KEY_CHECKS_PER_ACCOUNT
    for _ in range(limit):
        assert accounts_client.put("/account/api-keys", json={"api_key": SECRET}).status_code == 200
    refused = accounts_client.put("/account/api-keys", json={"api_key": SECRET})

    assert refused.status_code == 429
    assert refused.json()["error_code"] == "rate_limited"
    assert SECRET not in refused.text
    assert len(checked) == limit
    # A key that does not look like one is refused before it counts as a check.
    assert accounts_client.put("/account/api-keys", json={"api_key": "sk-short"}).status_code == 400


def test_a_grant_after_an_allowance_expired_starts_a_new_one(accounts_client, repository) -> None:
    _postgres_only(repository)
    from datetime import UTC, datetime, timedelta

    from research_tree.auth.accounts import set_user_flags
    from research_tree.billing.allowances import allowance_status, grant_allowance

    user_id = _signed_in_user_id(accounts_client, "lapsed@example.com")
    set_user_flags(user_id, is_verified=True)
    grant_allowance(
        email="lapsed@example.com",
        limit_usd=Decimal("5"),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    assert allowance_status(user_id, "lapsed@example.com", verified=True) == "none"

    grant_allowance(email="lapsed@example.com", limit_usd=Decimal("10"))
    assert allowance_status(user_id, "lapsed@example.com", verified=True) == "ok"


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

    # There has to be an allowance to take credit from.
    with pytest.raises(ValueError):
        grant_allowance(email="nobody@example.com", limit_usd=Decimal("-1.00"))


def test_a_top_up_keeps_the_period_and_the_expiry_it_was_not_told_to_change(
    accounts_client, repository
) -> None:
    _postgres_only(repository)
    from datetime import UTC, datetime

    from research_tree.auth.accounts import set_user_flags
    from research_tree.billing.allowances import allowance_summary, grant_allowance

    user_id = _signed_in_user_id(accounts_client, "monthly@example.com")
    set_user_flags(user_id, is_verified=True)
    ends = datetime(2030, 12, 31, 23, 59, 59, tzinfo=UTC)
    grant_allowance(
        email="monthly@example.com", limit_usd=Decimal("10"), period="monthly", expires_at=ends
    )
    grant_allowance(email="monthly@example.com", limit_usd=Decimal("5"))

    topped_up = allowance_summary(user_id, "monthly@example.com", verified=True)
    assert topped_up["limit_usd"] == 15.0
    assert topped_up["period"] == "monthly"
    assert topped_up["expires_at"].startswith("2030-12-31")


def test_sponsored_work_stops_when_its_allowance_is_taken_away(
    accounts_client, repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job resolves its key once, so the allowance is checked again as it is charged."""

    _postgres_only(repository)
    from research_tree.auth.accounts import set_user_flags
    from research_tree.billing.allowances import grant_allowance, revoke_allowance

    user_id = _signed_in_user_id(accounts_client, "revoked@example.com")
    set_user_flags(user_id, is_verified=True)
    allowance_id = grant_allowance(email="revoked@example.com", limit_usd=Decimal("5"))
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)
    response = {"usage": {"input_tokens": 1000, "output_tokens": 1000}}

    with bind_principal(Principal(user_id=user_id, email="revoked@example.com", is_verified=True)):
        assert credentials.openai_api_key() == PLATFORM_KEY
        record_llm_usage(model="gpt-5.6-luna", raw_response=response, label="test")
        assert current_binding().spend.exhausted is False
        assert revoke_allowance(allowance_id) is True
        record_llm_usage(model="gpt-5.6-luna", raw_response=response, label="test")
        assert current_binding().spend.exhausted is True
        monkeypatch.setattr("research_tree.llm._post", lambda *_a, **_k: response)
        with pytest.raises(AllowanceExhaustedError):
            call_responses_api(
                {"model": "gpt-5.6-luna"}, api_key=PLATFORM_KEY, timeout_seconds=5, label="test"
            )


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
    principal = Principal(user_id=user_id, email="friend@example.com", is_verified=True)
    with bind_principal(principal, feature="test", request_id="r1"):
        assert credentials.openai_api_key() == PLATFORM_KEY
        # 100k input at $5/M plus 20k output at $30/M on Sol: $1.10, past the $1 limit.
        record_llm_usage(
            model="gpt-5.6-sol",
            raw_response={"usage": {"input_tokens": 100_000, "output_tokens": 20_000}},
            label="test call",
        )
        # The charge that emptied the allowance stops the very next call of
        # the same piece of work, before it reaches the provider.
        assert current_binding().spend.exhausted is True
        with pytest.raises(AllowanceExhaustedError):
            call_responses_api(
                {"model": "gpt-5.6-luna", "input": "x"},
                api_key=PLATFORM_KEY,
                timeout_seconds=1.0,
                label="next call",
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
