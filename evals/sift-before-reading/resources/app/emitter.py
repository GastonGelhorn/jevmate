# emitter: posts batches to the front
import time


class HttpEmitter:
    def post(self, batch):
        status, headers = self._send(batch)
        if status in (429, 503):
            # backpressure: honour Retry-After, otherwise exponential backoff capped at 30 s
            wait = float(headers.get('retry-after') or 0) or min(30.0, 0.5 * 2 ** self.attempt)
            time.sleep(wait)
            self.attempt += 1
            return self.post(batch)
        return status
