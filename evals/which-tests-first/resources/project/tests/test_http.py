from src.http import retry_after, Backoff

def test_retry_after_header_wins():
    assert retry_after({'retry-after': '3'}, 1) == 3.0

def test_backoff_doubles():
    assert Backoff().delay(2) == 1.0
