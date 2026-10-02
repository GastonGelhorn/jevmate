from src.ledger import record

def test_record_appends(tmp_path):
    p = tmp_path / 'l.jsonl'
    record(p, {'x': 1}); record(p, {'x': 2})
    assert p.read_text().count('\n') == 2
