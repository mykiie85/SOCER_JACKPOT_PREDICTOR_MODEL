from jackpot_predictor.resolver.fuzzy_matcher import fuzzy_match_team, match_team
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


def test_matcher_strips_club_affixes():
    """SportPesa's corporate spellings must reach football-data's short names."""
    teams = {"Parma", "Cagliari", "Lecce", "Utrecht", "Gent", "Lyon"}
    for raw, want in [("Parma Calcio", "Parma"), ("Cagliari Calcio", "Cagliari"),
                      ("US Lecce", "Lecce"), ("FC Utrecht", "Utrecht"),
                      ("KAA Gent", "Gent"), ("Olympique Lyon", "Lyon")]:
        hit, _method, _score = match_team(raw, teams)
        assert hit == want, f"{raw} -> {hit}, expected {want}"


def test_matcher_prefers_leading_tokens_over_trailing_city():
    """The club is named first, the city second — a token-set scorer gets
    these backwards and would price Sampdoria's match off Genoa's form."""
    assert match_team("Sampdoria Genoa", {"Sampdoria", "Genoa"})[0] == "Sampdoria"
    assert match_team("Wisla Krakow", {"Wisla", "Wisla Plock"})[0] == "Wisla"


def test_matcher_prefers_a_misspelled_leading_club_over_a_trailing_one():
    """SportPesa writes "Espanyol Barcelona"; football-data writes that club
    "Espanol". The leading run therefore misses an exact match and the
    whole-string scorer took the trailing token — Barcelona, a different club
    in the same league — pricing Espanyol's match off Barcelona's form at 0.80
    home. A near-miss on the leading run has to beat an exact trailing one.
    Live on Supa17 #233 (2026-09-05) before it was caught."""
    roster = {"Espanol", "Barcelona", "Sevilla", "Vallecano", "Santander"}
    assert match_team("Espanyol Barcelona", roster)[0] == "Espanol"
    # ...but a leading token that names no club must still yield to the tail,
    # or Rayo Vallecano and Racing Santander stop resolving at all.
    assert match_team("Rayo Vallecano", roster)[0] == "Vallecano"
    assert match_team("Racing Santander", roster)[0] == "Santander"


def test_matcher_refuses_ambiguous_match():
    """A near-tie must stay unresolved so the fixture falls back to odds."""
    hit, method, _ = match_team("Sporting", {"Sporting Lisbon", "Sporting Gijon"})
    assert hit is None
    assert method.startswith("ambiguous")


def test_matcher_collapses_duplicate_spellings_by_frequency():
    """EdgeBot's history has trailing-whitespace twins; pick the common one."""
    hit, _m, _s = match_team("FC Utrecht", {"Utrecht", "Utrecht "},
                             counts={"Utrecht": 672, "Utrecht ": 1})
    assert hit == "Utrecht"


def test_detect_romania_and_russia_top_flights():
    # Added with EdgeBot's RO1/RU1 tier-2 history (free extra feed, 14 seasons).
    assert detect_league("Superliga", "Romania") == ("RO1", 2)
    assert detect_league("Liga 1", "Romania") == ("RO1", 2)
    assert detect_league("Premier League", "Russia") == ("RU1", 2)


def test_romania_russia_second_tiers_stay_uncovered():
    # The extra feed carries top divisions only — never promote a second tier.
    assert detect_league("Liga 2", "Romania") == (None, None)
    assert detect_league("1. Liga", "Russia") == (None, None)
    assert detect_league("FNL", "Russia") == (None, None)


def test_argentina_primera_lpf_alias():
    # SportPesa's label for the Argentine top flight; AR1 is covered, so this
    # must not fall through to the odds path.
    assert detect_league("Primera LPF", "Argentina") == ("AR1", 2)
    assert detect_league("Primera Nacional", "Argentina") == (None, None)


def test_matcher_keeps_the_distinguishing_tail_token():
    """Leading-token preference must not collapse two clubs into one.

    Argentina's Primera has both Independiente (Avellaneda) and Independiente
    Rivadavia (Mendoza); football-data abbreviates the second "Ind. Rivadavia".
    Taking the leading run alone silently priced one off the other's form.
    """
    hit, method, _ = match_team("Independiente Rivadavia",
                                {"Independiente", "Ind. Rivadavia", "Racing Club"})
    assert hit == "Ind. Rivadavia", method
    # ...while a trailing *city* still loses to the leading club name.
    assert match_team("Sampdoria Genoa", {"Sampdoria", "Genoa"})[0] == "Sampdoria"
