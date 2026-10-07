import pytest
from backend.core import counters


@pytest.fixture(autouse=True)
def _reset_counters():
    counters.reset()


def test_incr_and_get():
    assert counters.get("my_counter") == 0
    counters.incr("my_counter", 1)
    counters.incr("my_counter", 2)
    assert counters.get("my_counter") == 3


def test_labels():
    counters.incr("reqs", 1, method="GET", status="200")
    counters.incr("reqs", 2, status="200", method="GET")  # different order
    counters.incr("reqs", 1, method="POST", status="200")

    assert counters.get("reqs", method="GET", status="200") == 3
    assert counters.get("reqs", method="POST", status="200") == 1
    assert counters.get("reqs", method="GET", status="500") == 0


def test_snapshot():
    counters.incr("a", 1, route="/foo")
    counters.incr("b", 5)

    snap = counters.snapshot()
    assert snap["a"] == {(("route", "/foo"),): 1}
    assert snap["b"] == {(): 5}
