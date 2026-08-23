import pytest
from solution import retry

def test_succeeds_after_failures():
    calls = []
    @retry(attempts=3, base_delay=0)
    def f():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("nope")
        return "ok"
    assert f() == "ok"
    assert len(calls) == 3

def test_reraises_original():
    @retry(attempts=2, base_delay=0)
    def f():
        raise KeyError("boom")
    with pytest.raises(KeyError):
        f()

def test_does_not_retry_unlisted():
    calls = []
    @retry(attempts=5, base_delay=0, exceptions=(ValueError,))
    def f():
        calls.append(1)
        raise TypeError
    with pytest.raises(TypeError):
        f()
    assert len(calls) == 1

def test_preserves_metadata():
    @retry(attempts=1, base_delay=0)
    def named():
        """docstring conservée"""
        return 1
    assert named.__name__ == "named"
    assert "conservée" in named.__doc__

def test_kwargs():
    @retry(attempts=2, base_delay=0)
    def g(a, b=2):
        return a + b
    assert g(1, b=5) == 6
