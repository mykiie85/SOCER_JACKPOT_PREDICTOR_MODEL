from jackpot_predictor.resolver.fuzzy_matcher import fuzzy_match_team
from jackpot_predictor.resolver.league_detector import detect_league


def test_detect_tier1_leagues():
    assert detect_league("Premier League", "England") == ("E0", 1)
    assert detect_league("Chinese Super League", "China") == ("CH1", 1)
    assert detect_league("Brasileiro Serie A", "Brazil") == ("BR1", 1)
    assert detect_league("LaLiga", "Spain") == ("SP1", 1)
    assert detect_league("Bundesliga", "Germany") == ("D1", 1)


def test_detect_tier2_leagues():
    assert detect_league("Primera Division", "Argentina") == ("AR1", 2)
    assert detect_league("MLS", "USA") == ("US1", 2)
    assert detect_league("League One", "England") == ("E2", 2)


def test_uncovered_leagues_return_none():
    # Second/third tiers EdgeBot has no data for must NOT map to the top flight.
    assert detect_league("Brasileiro Serie B", "Brazil") == (None, None)
    assert detect_league("Brasileiro Serie C", "Brazil") == (None, None)
    assert detect_league("Primera Nacional", "Argentina") == (None, None)
    assert detect_league("Kakkonen", "Finland") == (None, None)
    assert detect_league("Vysshaya Liga", "Belarus") == (None, None)
    assert detect_league("Besta deild", "Iceland") == (None, None)


def test_specific_pattern_beats_generic():
    # "serie b" must win over "serie a"-in-"Brasileiro Serie A" style overlap
    assert detect_league("Serie B", "Italy") == ("I2", 1)
    assert detect_league("Serie A", "Italy") == ("I1", 1)
    assert detect_league("Ligue 2", "France") == ("F2", 1)


def test_fuzzy_matcher():
    teams = {"Man United", "Man City", "Nott'm Forest", "Sheffield United"}
    hit, score = fuzzy_match_team("Sheffield Utd", teams, threshold=85)
    assert hit == "Sheffield United"
    assert score >= 85
    hit, _ = fuzzy_match_team("Real Madrid", teams, threshold=85)
    assert hit is None
