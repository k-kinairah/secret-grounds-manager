"""Fix 7: misheard member names -> ranked did_you_mean (spelling + Soundex)."""

import asyncio
import json
from zoneinfo import ZoneInfo

import httpx
import pytest

from sg_assistant import tools
from sg_assistant.backend import BackendClient

TOKEN = "test-token"
TZ = ZoneInfo("Asia/Manila")
MEMBERS = [
    {"member_id": "m1", "member_code": "SG-944F97F3", "username": "rogene", "full_name": "rogene",
     "display_name": None, "phone_number": "09171234567", "pin_hash": "secret", "status": "active",
     "time_balance_seconds": 79200, "vip_balance_seconds": 36000},
    {"member_id": "m2", "member_code": "SG-CC409E3C", "username": "kinairah", "full_name": "Kin Airah Santos",
     "display_name": None, "phone_number": "09170000000", "status": "active", "time_balance_seconds": 57600},
    {"member_id": "m3", "member_code": "SG-BB29228E", "username": "lock", "full_name": "Lock Test",
     "display_name": None, "status": "active", "time_balance_seconds": 100},
    {"member_id": "m4", "member_code": "SG-12345678", "username": "marites", "full_name": "Maria Teresa Reyes",
     "display_name": "Marites", "status": "inactive", "time_balance_seconds": 0},
]


def make_client(seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.path != "/members":
            return httpx.Response(404, json={"detail": "Not Found"})
        q = request.url.params.get("q")
        if q is None:
            return httpx.Response(200, json=MEMBERS)
        # Backend behaviour: LIKE match on code / username / full name only.
        ql = q.lower()
        hits = [m for m in MEMBERS if ql in m["member_code"].lower() or ql in (m["username"] or "")
                or ql in m["full_name"].lower()]
        return httpx.Response(200, json=hits)

    return BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "word, code",
    [("Rogene", "R250"), ("rojin", "R250"), ("raw jin", "R250"), ("Robert", "R163"), ("Rupert", "R163"),
     ("Tymczak", "T522"), ("Pfister", "P236"), ("Ashcraft", "A261"), ("", "")],
)
def test_soundex(word, code):
    assert tools.soundex(word) == code


@pytest.mark.parametrize("misheard", ["rojin", "raw jin", "Rojean", "rogine", "roh jean", "ro gene"])
def test_misheard_rogene_is_suggested_first(misheard):
    result = run(tools.find_member(make_client(), TZ, misheard))
    assert result["members"] == []  # never treated as a match
    assert result["did_you_mean"][0]["name"] == "rogene"
    assert result["did_you_mean"][0]["member_code"] == "SG-944F97F3"
    assert "confirm the name" in result["note"]


@pytest.mark.parametrize(
    "misheard, expected",
    [("kin ira", "kinairah"), ("kinaira", "kinairah"), ("santo", "kinairah"), ("lok", "lock"),
     ("mari tess", "marites"), ("SG 944F97F3", "rogene"), ("944f97f", "rogene")],
)
def test_other_misheard_names_and_codes(misheard, expected):
    result = run(tools.find_member(make_client(), TZ, misheard))
    usernames = [s["username"] for s in result.get("did_you_mean", [])] or [m["username"] for m in result["members"]]
    assert usernames[0] == expected


def test_suggestions_carry_no_balances_or_secrets():
    result = run(tools.find_member(make_client(), TZ, "rojin"))
    text = json.dumps(result["did_you_mean"])
    for forbidden in ("balance", "phone", "pin", "0917", "secret", "79200"):
        assert forbidden not in text.lower()
    assert set(result["did_you_mean"][0]) == {"name", "username", "member_code", "match"}


def test_exact_match_has_no_guesses_and_skips_full_scan():
    seen: list[str] = []
    result = run(tools.find_member(make_client(seen), TZ, "rogene"))
    assert result["members"][0]["username"] == "rogene"
    assert "did_you_mean" not in result
    assert len(seen) == 1  # only the normal search; no full member list fetched


def test_nothing_close_gives_no_guesses():
    result = run(tools.find_member(make_client(), TZ, "zzqqxx"))
    assert result["members"] == [] and "did_you_mean" not in result


def test_at_most_three_ranked():
    names = ["rogene", "rogen", "rojene", "rojeen", "rogena", "rogenne"]
    many = [dict(MEMBERS[0], member_code=f"SG-{i}", username=n, full_name=n) for i, n in enumerate(names)]
    suggestions = tools.closest_members("rojin", many)
    assert len(suggestions) == 3
    assert [s["match"] for s in suggestions] == sorted((s["match"] for s in suggestions), reverse=True)
