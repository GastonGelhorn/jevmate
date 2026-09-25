import time

class Backoff:
    def delay(self, attempt):
        return min(8.0, 0.5 * 2 ** (attempt - 1))

def retry_after(headers, attempt):
    ra = headers.get('retry-after')
    if ra and ra.isdigit():
        return float(ra)
    return Backoff().delay(attempt)
