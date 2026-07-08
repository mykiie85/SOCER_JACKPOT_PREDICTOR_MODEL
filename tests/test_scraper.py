"""Parser tests against a canned jackpot-offer-api payload (no network)."""
from jackpot_predictor.scraper.sportpesa import parse_jackpot

PAYLOAD = {
    "id": "abc-123",
    "humanId": 225,
    "settings": {"numberOfEvents": 2, "jackpotTypes": ["2/2"]},
    "bettingStatus": "Open",
    "events": [
        {
            "id": "sr:match:2", "order": 2, "utcKickOffTime": "2026-07-12T14:00:00Z",
            "competitors": [
                {"competitorName": "FK Atyrau", "isHome": True},
                {"competitorName": "FC Yelimai", "isHome": False},
            ],
            "tournamentName": "Premier League", "countryName": "Kazakhstan",
            "countryIsoCode": "KAZ", "home": 2.1, "draw": 3.1, "away": 3.4,
        },
        {
            "id": "sr:match:1", "order": 1, "utcKickOffTime": "2026-07-12T11:00:00Z",
            "competitors": [
                {"competitorName": "Arsenal FC", "isHome": True},
                {"competitorName": "Chelsea FC", "isHome": False},
            ],
            "tournamentName": "Premier League", "countryName": "England",
            "countryIsoCode": "ENG", "home": 1.8, "draw": 3.5, "away": 4.2,
        },
    ],
}


def test_parse_orders_and_normalizes():
    jp = parse_jackpot(PAYLOAD)
    assert jp["human_id"] == 225
    assert jp["number_of_events"] == 2
    assert jp["jackpot_type"] == "jackpot2"
    fixtures = jp["fixtures"]
    assert [f["match_number"] for f in fixtures] == [1, 2]
    assert fixtures[0]["home_team_raw"] == "Arsenal FC"
    assert fixtures[0]["away_team_raw"] == "Chelsea FC"
    assert fixtures[0]["odds_home"] == 1.8
    assert jp["first_kickoff_utc"] == "2026-07-12T11:00:00Z"
    assert jp["last_kickoff_utc"] == "2026-07-12T14:00:00Z"


def test_jackpot_type_names():
    p = dict(PAYLOAD, settings={"numberOfEvents": 17})
    assert parse_jackpot(p)["jackpot_type"] == "supa17"
    p = dict(PAYLOAD, settings={"numberOfEvents": 13})
    assert parse_jackpot(p)["jackpot_type"] == "midweek13"
