import random
from solution import merge

def test_empty():
    assert merge([]) == []

def test_overlap():
    assert merge([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]

def test_touching():
    assert merge([(1, 2), (2, 5)]) == [(1, 5)]

def test_unordered_and_malformed():
    assert merge([(6, 2), (1, 1)]) == [(1, 1), (2, 6)]

def test_full_containment():
    assert merge([(1, 10), (3, 4)]) == [(1, 10)]

def test_fuzz_against_reference():
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(0, 12)
        iv = [tuple(sorted((rng.randint(0, 30), rng.randint(0, 30))))
              for _ in range(n)]
        covered = set()
        for a, b in iv:
            covered |= set(range(a, b + 1))
        out = merge(list(iv))
        got = set()
        for a, b in out:
            assert a <= b
            got |= set(range(a, b + 1))
        assert got == covered
        assert out == sorted(out)
