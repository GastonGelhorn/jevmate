from src.cache import get, put

def test_roundtrip(tmp_path):
    p = tmp_path / 'x.json'
    put(p, {'a': 1})
    assert get(p) == {'a': 1}
