import pytest

from dorkscout.config import get_dork_db, validate_dork_database
from dorkscout.dorks import build_query, get_dorks, list_all_techs, load_dorks
from dorkscout.models import DorkPattern


def test_dorkpattern_model_and_string_repr():
    pattern = DorkPattern(
        signature="inurl:checkout intitle:store -amazon",
        engine="duckduckgo",
    )
    assert pattern.signature == "inurl:checkout intitle:store -amazon"
    assert pattern.engine == "duckduckgo"
    assert "DDG" in str(pattern)


def test_build_query_with_term():
    pattern = DorkPattern(
        signature='"powered by tripe.com"',
        engine="google",
    )
    query = build_query(pattern, "coffee")
    assert query == '"powered by tripe.com" "coffee"'
    # test multi-word term (still quoted)
    query2 = build_query(pattern, "fresh coffee")
    assert query2 == '"powered by tripe.com" "fresh coffee"'
    # test already-quoted term is left unchanged
    query3 = build_query(pattern, '"already quoted"')
    assert query3 == '"powered by tripe.com" "already quoted"'


def test_validation_fails_on_bad_engine():
    bad_pattern = {"signature": "test", "engine": "invalid-engine"}
    db = get_dork_db().copy()
    db["dorks"]["BadTech"] = [bad_pattern]
    with pytest.raises(ValueError, match="Invalid engine"):
        validate_dork_database(db)


def test_load_dorks():
    db = load_dorks()
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert isinstance(db["dorks"], dict)
    assert len(db["dorks"]) > 0


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) > 0
    assert all(isinstance(p, DorkPattern) for p in patterns)
    assert patterns[0].engine in ("google", "bing")


def test_get_dorks_by_category():
    patterns = get_dorks(category="payment_gateways")
    assert len(patterns) >= 3
    for p in patterns:
        assert isinstance(p, DorkPattern)


def test_get_dorks_by_niches_category():
    patterns = get_dorks(category="niches")
    assert len(patterns) > 0
    techs = {p.signature for p in patterns}
    assert any("book" in s.lower() for s in techs)


def test_list_all_techs():
    techs = list_all_techs()
    assert "Stripe" in techs
    assert "Shopify" in techs
    assert "Books" in techs
    assert isinstance(techs, list)


def test_build_query_no_term():
    pattern = DorkPattern(signature="inurl:shop", engine="bing")
    assert build_query(pattern) == "inurl:shop"
    assert build_query(pattern, "") == "inurl:shop"
    assert build_query(pattern, "   ") == "inurl:shop"


def test_validation_fails_on_unknown_tech():
    db = get_dork_db().copy()
    db["dorks"]["UnknownTech"] = [{"signature": "test", "engine": "google"}]
    with pytest.raises(ValueError, match="not present in tech_to_category mapping"):
        validate_dork_database(db)


def test_validation_fails_on_bad_category_mapping():
    db = get_dork_db().copy()
    db["tech_to_category"]["Foo"] = "nonexistent"
    with pytest.raises(ValueError, match="unknown category"):
        validate_dork_database(db)
