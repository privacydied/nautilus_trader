def test_year_share_impossible():
    """Feasibility test that a max year share <= 0.45 is impossible with only two years of data."""
    years = 2
    min_possible_share = 1 / years
    assert min_possible_share >= 0.45, "Year gate should be unsatisfiable"
