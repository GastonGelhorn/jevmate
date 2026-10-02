from src.render import table

def test_table():
    assert table([[1, 2]]) == '1 | 2'
