from rtx5090_watcher.decide import is_qualifying
from rtx5090_watcher.models import Observation


def obs(in_stock, price, determined=True):
    return Observation(determined, in_stock, price, "NVIDIA GeForce RTX 5090 Founders Edition", None, "", 1)


def test_threshold_is_inclusive_at_2500():
    assert is_qualifying(obs(True, 250_000), 250_000) is True
    assert is_qualifying(obs(True, 250_001), 250_000) is False
    assert is_qualifying(obs(True, 199_999), 250_000) is True


def test_out_of_stock_never_qualifies_even_at_msrp():
    assert is_qualifying(obs(False, 199_999), 250_000) is False


def test_missing_price_or_undetermined_reading_does_not_qualify():
    assert is_qualifying(obs(True, None), 250_000) is False
    assert is_qualifying(obs(None, 199_999), 250_000) is False
    assert is_qualifying(obs(True, 199_999, determined=False), 250_000) is False
