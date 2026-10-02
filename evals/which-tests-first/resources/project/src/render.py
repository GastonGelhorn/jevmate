def table(rows):
    return '\n'.join(' | '.join(map(str, r)) for r in rows)
