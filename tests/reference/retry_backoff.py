"""Solution de référence — décorateur de retry à backoff exponentiel."""
from __future__ import annotations

import functools
import time


def retry(attempts: int = 3, base_delay: float = 0.01,
          exceptions: tuple[type[BaseException], ...] = (Exception,)):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for n in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if n == attempts - 1:
                        raise
                    if base_delay:
                        time.sleep(base_delay * (2 ** n))
        return wrapper
    return deco
