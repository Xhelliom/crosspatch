import random, string, pytest
from solution import encode, decode

def test_basic():
    assert encode("aaabccd") == "a3bc2d"

def test_empty():
    assert encode("") == ""
    assert decode("") == ""

def test_single():
    assert encode("a") == "a"

@pytest.mark.parametrize("s", ["a11b", "1111", "z9z9z", "aa1"])
def test_digits_roundtrip(s):
    assert decode(encode(s)) == s

def test_fuzz_roundtrip():
    rng = random.Random(1337)
    alpha = string.ascii_lowercase[:4] + "019"
    for _ in range(300):
        s = "".join(rng.choice(alpha) for _ in range(rng.randint(0, 40)))
        assert decode(encode(s)) == s
