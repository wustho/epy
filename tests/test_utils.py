from collections import namedtuple

from epy import resolve_path


def test_resolve_path():
    UnresolvedPath = namedtuple("UnresolvedPath", ["current_dir", "relative_path"])

    inputs = [
        UnresolvedPath("/aaa/bbb/book.html", "../ccc.png"),
        UnresolvedPath("/aaa/bbb/book.html", "../../ccc.png"),
        UnresolvedPath("aaa/bbb/book.html", "../../ccc.png"),
    ]

    expecteds = [
        "/aaa/ccc.png",
        "/ccc.png",
        "ccc.png",
    ]

    for input, expected in zip(inputs, expecteds):
        assert resolve_path(input.current_dir, input.relative_path) == expected
