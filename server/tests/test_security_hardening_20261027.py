"""Security review 2026-10-27: write routes that could touch the paper bankroll require a signed-in user."""

TICKET = {"sportsbook": "draftkings", "stake": 1.0, "funded": False,
          "legs": [{"game_id": "00000000-0000-0000-0000-000000000001", "market": "MONEYLINE", "selection": "NYY ML"},
                   {"game_id": "00000000-0000-0000-0000-000000000002", "market": "MONEYLINE", "selection": "BOS ML"}]}


def _member(client):
    r = client.post("/api/v1/auth/register", json={"email": "member@example.com", "password": "MemberPass123!", "display_name": "Member"})  # secret-guard: test-fixture
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_anonymous_parlay_ticket_is_refused(client):
    assert client.post("/api/v1/mlb/parlays/tickets", json=TICKET).status_code == 401


def test_member_cannot_fund_a_ticket_from_the_aisp_bankroll(client):
    r = client.post("/api/v1/mlb/parlays/tickets", json={**TICKET, "funded": True}, headers=_member(client))
    assert r.status_code == 403
