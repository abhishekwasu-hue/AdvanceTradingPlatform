"""MO2A-2: Cloudflare Workers AI as a user-key AI provider in "Connect AI in one paste" (behind `ui_v2` + `ai_one_key`).

Cloudflare needs an API token AND the Account ID (the address carries the id). Both fit the existing one-paste flow - the
token and the id side by side in one paste, or the panel's second box (`account_id`) - and the existing storage: one
sealed secret "<account id>:<token>" in `user_ai_keys.encrypted_key` (Text), no migration.

Every key here is an obviously fake string and every answer comes from tests/fixtures/cloudflare_workers_ai.json through
a mocked transport: no test reaches Cloudflare.
"""
import json
from pathlib import Path

import httpx
import pytest

from app.ai import byok, key_formats, pricing
from app.ai import providers as prov
from app.ai.providers import ProviderError
from app.db.models import LlmCallRecord, UserAiKeyRecord
from tests.test_auth_api import client
from tests.test_settings_a_connect_ai import _chart_ask, _connect, _flags, _rows, _run, _user

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "cloudflare_workers_ai.json").read_text())
SHARED = json.loads((FIXTURES / "key_formats.json").read_text())
ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cfut_FAKEFAKEtestonlycloudflare0000000000000000000000"
ACCOUNT_TOKEN = "cfat_FAKEFAKEtestonlycloudflare0000000000000000000000"
OLD_TOKEN = "FAKEFAKEtestonlyCloudflareOldToken000000"
GLOBAL_KEY = "cfk_FAKEFAKEtestonlyglobalapikey000000000000000000000"
SECRET = prov.cloudflare_secret(ACCOUNT, TOKEN)
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions"
SCOUT = "@cf/meta/llama-4-scout-17b-16e-instruct"


@pytest.fixture
def one_key():
    _flags(ui_v2=True, ai_one_key=True)
    yield
    _flags(ui_v2=False, ai_one_key=False, vision_byok=False)


class Recorded:
    """The mocked Cloudflare endpoint: records every request and answers with a recorded body."""

    def __init__(self, answer=None):
        self.answer = answer or RECORDED["success"]
        self.requests = []

    def client(self):
        def handler(request):
            self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization", ""),
                                  "body": json.loads(request.content or b"{}")})
            body = json.loads(json.dumps(self.answer["body"]).replace("{token}", TOKEN).replace("{account}", ACCOUNT))
            return httpx.Response(self.answer["status"], json=body)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def install(self, monkeypatch):
        original = prov.build_byok_provider

        def build(name, api_key, model, client=None, **kw):
            return original(name, api_key, model, client=self.client(), **kw)
        monkeypatch.setattr(prov, "build_byok_provider", build)
        return self


# --- the one table, the shared fixture and the paste -------------------------------------------------------------------------

def test_cloudflare_is_in_the_one_table_with_its_tags_and_the_shared_fixture_agrees():
    assert SHARED["table"] == key_formats.table()
    assert SHARED["provider_facts"] == key_formats.facts()
    assert SHARED["connect_order"] == list(key_formats.CONNECT_ORDER) == list(prov.CONNECT_PROVIDERS)
    assert SHARED["picker_order"] == list(key_formats.PICKER_ORDER)
    facts = key_formats.PROVIDER_FACTS["cloudflare"]
    assert facts == {"name": "Cloudflare Workers AI", "free_tier": True, "vision": True, "may_train": False,
                     "privacy": facts["privacy"]}
    assert "does not train" in facts["privacy"] and len(facts["privacy"]) < 160
    assert "cloudflare" in prov.VISION_PROVIDERS and key_formats.MAY_TRAIN_ON_FREE_TIER["cloudflare"] is False
    assert key_formats.CONNECT_ORDER[0] == "gemini"                         # free Google Gemini stays first
    assert "cloudflare" in key_formats.PICKER_ORDER                         # older tokens have no prefix


def test_the_token_formats_and_the_account_id_next_to_it_in_one_paste():
    assert key_formats.detect_provider(TOKEN).provider == "cloudflare"
    assert key_formats.detect_provider(ACCOUNT_TOKEN).provider == "cloudflare"
    assert key_formats.detect_provider(GLOBAL_KEY).kind == "admin"
    assert key_formats.detect_provider(OLD_TOKEN).kind == "unknown"          # the picker asks
    assert key_formats.detect_provider(f"{ACCOUNT} {OLD_TOKEN}").provider == "cloudflare"
    for case in SHARED["account_id_cases"]:
        assert list(key_formats.split_account_id(case["paste"])) == [case["account_id"], case["rest"]], case["paste"]
    assert any(c["provider"] == "cloudflare" and " " in c["key"] for c in SHARED["cases"])


def test_the_key_shape_checks_and_the_global_api_key_is_refused():
    assert byok.validate_key_shape("cloudflare", TOKEN, one_key=True) == TOKEN
    assert byok.validate_key_shape("cloudflare", OLD_TOKEN, one_key=True) == OLD_TOKEN
    with pytest.raises(byok.ByokError, match="^This looks like a Groq key, not a Cloudflare Workers AI key$"):
        byok.validate_key_shape("cloudflare", "gsk_FAKEFAKE-test-only-ffffffffffffffffffffffff-6666", one_key=True)
    with pytest.raises(byok.ByokError) as info:
        byok.validate_key_shape("cloudflare", GLOBAL_KEY, one_key=True)
    assert "Global API Key" in str(info.value) and "Workers AI API token" in str(info.value) and GLOBAL_KEY not in str(info.value)
    with pytest.raises(byok.ByokError, match="This looks like a Cloudflare Workers AI key. AiAlgoDesk cannot use .* yet"):
        byok.validate_key_shape("openai", TOKEN)                          # the Vision card (no ai_one_key): as before
    with pytest.raises(byok.ByokError, match="Unknown provider"):
        byok.validate_key_shape("cloudflare", TOKEN)
    assert byok.clean_account_id(f" {ACCOUNT.upper()} ") == ACCOUNT
    with pytest.raises(byok.ByokError, match="32 letters and numbers"):
        byok.clean_account_id("not-an-account-id")


# --- the request shape (recorded answers, mocked HTTP) -------------------------------------------------------------------------

def test_each_tier_goes_to_the_account_address_with_the_token_only_and_max_tokens():
    mock = Recorded()
    for task in ("strategy_generation", "copilot", "support"):
        llm = prov.build_byok_provider("cloudflare", SECRET, "", client=mock.client(), task=task)
        out = _run(llm.complete_full("system text", "user text", max_tokens=32))
        assert out.text == "OK" and out.provider == "cloudflare" and out.input_tokens == 42
    assert [r["url"] for r in mock.requests] == [URL] * 3
    assert {r["auth"] for r in mock.requests} == {f"Bearer {TOKEN}"}          # the Account ID is in the address, never the header
    tiers = prov.default_models()["cloudflare"]
    assert set(tiers) == {"strong", "fast", "cheap"} and set(tiers.values()) == {SCOUT}
    for r in mock.requests:
        assert r["body"]["model"] == SCOUT and r["body"]["max_tokens"] >= 32 and "max_completion_tokens" not in r["body"]
        assert r["body"]["messages"][0] == {"role": "system", "content": "system text"}


def test_a_chart_goes_as_a_data_url_image_part_to_a_model_that_reads_images():
    mock = Recorded()
    llm = prov.build_byok_provider("cloudflare", SECRET, "@cf/meta/llama-3.3-70b-instruct-fp8-fast", client=mock.client(), vision=True)
    _run(llm.complete_full("s", "u", max_tokens=8, image_png=byok.TEST_IMAGE_PNG))
    body = mock.requests[0]["body"]
    part = body["messages"][1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["model"] == SCOUT                                            # a text-only model is moved to the vision model


def test_a_stored_secret_without_a_valid_account_id_is_never_sent():
    for bad in (TOKEN, f"short:{TOKEN}", f"{ACCOUNT}:", f"{ACCOUNT.upper()}:{TOKEN}"):
        with pytest.raises(ProviderError) as info:
            prov.build_byok_provider("cloudflare", bad, "", task="copilot")
        assert info.value.kind == "auth" and TOKEN not in str(info.value)


@pytest.mark.parametrize("case", RECORDED["errors"], ids=[c["name"] for c in RECORDED["errors"]])
def test_every_recorded_cloudflare_error_maps_to_one_plain_sentence(case):
    mock = Recorded(answer=case)
    llm = prov.build_byok_provider("cloudflare", SECRET, "", client=mock.client(), task="copilot")
    with pytest.raises(ProviderError) as info:
        _run(llm.complete_full("s", "u", max_tokens=8))
    assert info.value.kind == case["kind"]
    assert TOKEN not in str(info.value)
    assert byok.plain_error(case["kind"]) in byok.PLAIN_ERRORS.values()


def test_a_cloudflare_token_in_any_error_text_is_masked():
    assert TOKEN not in byok.sanitize_error(f"failed with {TOKEN} and {GLOBAL_KEY}")
    assert GLOBAL_KEY not in byok.sanitize_error(f"failed with {GLOBAL_KEY}")


def test_prices_come_from_the_cloudflare_list_and_an_unknown_model_is_never_under_counted():
    cost = pricing.cost_usd("cloudflare", SCOUT, input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.estimated is False and round(cost.amount, 4) == round(0.27 + 0.85, 4)
    price, estimated = pricing.price_for("cloudflare", "@cf/some-vendor/new-model")
    assert estimated is True and price is not None
    known = [p for name, p in pricing._prices.items() if name.startswith("@cf/")]
    assert price[0] + price[1] == max(p[0] + p[1] for p in known)


# --- the connect panel -------------------------------------------------------------------------------------------------------------

def test_one_paste_with_the_token_and_the_account_id_connects_and_tests_with_the_tiny_image(one_key, monkeypatch):
    mock = Recorded().install(monkeypatch)
    headers, me = _user("mo2a-cf-one-paste@example.com")
    done = _connect(headers, f"{ACCOUNT}\n{TOKEN}", consent=True)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["connected"] is True and body["ok"] is True and body["provider"] == "cloudflare"
    assert body["label"] == "Cloudflare Workers AI" and body["vision"] is True and body["may_train"] is False
    assert body["paid_plan"] is True and body["free_tier_warning"] is None
    assert body["key_end"] == TOKEN[-4:] and TOKEN not in done.text and ACCOUNT not in done.text
    assert mock.requests[-1]["url"] == URL and mock.requests[-1]["auth"] == f"Bearer {TOKEN}"
    assert "image_url" in json.dumps(mock.requests[-1]["body"])
    row = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))[0]
    assert row.provider == "cloudflare" and TOKEN not in row.encrypted_key and ACCOUNT not in row.encrypted_key
    assert row.key_hint == byok.key_hint(TOKEN)
    logged = _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"]))
    assert {c.provider for c in logged} == {"cloudflare"} and all(c.billed_to_user_key for c in logged)


def test_a_token_alone_asks_for_the_account_id_in_the_second_box(one_key, monkeypatch):
    mock = Recorded().install(monkeypatch)
    headers, _ = _user("mo2a-cf-second-box@example.com")
    asked = _connect(headers, TOKEN, consent=True)
    assert asked.status_code == 400 and asked.json()["detail"]["code"] == "account_id_needed"
    assert asked.json()["detail"]["message"] == byok.ACCOUNT_ID_NEEDED and not mock.requests
    bad = _connect(headers, TOKEN, consent=True, account_id="1234")
    assert bad.status_code == 400 and bad.json()["detail"]["message"] == byok.ACCOUNT_ID_INVALID and not mock.requests
    done = _connect(headers, TOKEN, consent=True, account_id=ACCOUNT.upper())
    assert done.status_code == 200 and done.json()["provider"] == "cloudflare" and done.json()["ok"] is True
    assert mock.requests[-1]["url"] == URL


def test_an_older_token_comes_from_the_picker_and_still_needs_the_account_id(one_key, monkeypatch):
    Recorded().install(monkeypatch)
    headers, _ = _user("mo2a-cf-old-token@example.com")
    assert _connect(headers, OLD_TOKEN, consent=True).json()["detail"]["code"] == "provider_needed"
    assert _connect(headers, OLD_TOKEN, consent=True, provider="cloudflare").json()["detail"]["code"] == "account_id_needed"
    done = _connect(headers, OLD_TOKEN, consent=True, provider="cloudflare", account_id=ACCOUNT)
    assert done.status_code == 200 and done.json()["provider"] == "cloudflare"


def test_the_global_api_key_is_refused_at_the_door(one_key, monkeypatch):
    mock = Recorded().install(monkeypatch)
    headers, _ = _user("mo2a-cf-global@example.com")
    refused = _connect(headers, f"{ACCOUNT} {GLOBAL_KEY}", consent=True)
    assert refused.status_code == 400 and "Global API Key" in refused.json()["detail"]["message"]
    assert GLOBAL_KEY not in refused.text and not mock.requests


def test_a_failed_test_keeps_the_key_with_the_plain_sentence(one_key, monkeypatch):
    used_up = next(c for c in RECORDED["errors"] if c["name"] == "daily free allocation used up")
    Recorded(answer=used_up).install(monkeypatch)
    headers, _ = _user("mo2a-cf-used-up@example.com")
    body = _connect(headers, f"{TOKEN} {ACCOUNT}", consent=True).json()
    assert body["connected"] is True and body["ok"] is False and body["error"] == byok.PLAIN_ERRORS["no_credit"]


def test_chart_analysis_runs_on_a_cloudflare_key_without_a_paid_plan_step(one_key, monkeypatch):
    mock = Recorded().install(monkeypatch)
    headers, me = _user("mo2a-cf-chart@example.com")
    _connect(headers, f"{ACCOUNT} {TOKEN}", consent=True)
    answer = _run(_chart_ask(me))
    assert answer.billed_to_user_key is True and answer.provider == "cloudflare"
    assert mock.requests[-1]["body"]["model"] == SCOUT and "image_url" in json.dumps(mock.requests[-1]["body"])


def test_no_migration_the_secret_fits_the_existing_columns():
    table = UserAiKeyRecord.__table__.c
    assert len("cloudflare") <= table.provider.type.length
    assert table.encrypted_key.type.__class__.__name__ == "Text"            # the two-part secret is one sealed string
    assert len(byok.key_hint(TOKEN)) <= table.key_hint.type.length
    from app.ai import compliance_terms as terms
    assert len(f"{terms.BYOK_CONSENT_VERSION}:cloudflare") <= 24             # the consent version is not cut


def test_the_picker_route_takes_cloudflare_and_the_account_id_field(one_key):
    headers, _ = _user("mo2a-cf-route@example.com")
    too_long = _connect(headers, TOKEN, consent=True, account_id="a" * 65)
    assert too_long.status_code == 422
    unknown = client.post("/api/ai/byok/connect", headers=headers, json={"api_key": TOKEN, "consent": True, "account": ACCOUNT})
    assert unknown.status_code == 422                                        # bodies refuse unknown fields
