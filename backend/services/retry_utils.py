import random


def compute_backoff_seconds(attempts: int, base_seconds: int = 60, max_seconds: int = 3600) -> int:
    if attempts <= 0:
        return base_seconds
    backoff = min(max_seconds, base_seconds * (2 ** (attempts - 1)))
    jitter = random.randint(0, max(1, base_seconds // 2))
    return int(backoff + jitter)
