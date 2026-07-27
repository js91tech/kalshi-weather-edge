from kalshi_weather_edge.brackets import bracket_probability


def test_less_greater_between_basic():
    # Tight distribution around 80F
    mu, sigma = 80.0, 1.0
    p_less, _, hi = bracket_probability("less", None, 77, mu, sigma)
    assert hi == 76
    assert p_less < 0.05

    p_gt, lo, _ = bracket_probability("greater", 84, None, mu, sigma)
    assert lo == 85
    assert p_gt < 0.01

    p_mid, lo2, hi2 = bracket_probability("between", 79, 80, mu, sigma)
    assert lo2 == 79 and hi2 == 80
    assert p_mid > 0.3
