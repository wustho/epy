from collections import namedtuple

from epy import TextMark, resolve_path, count_marked_text_len, construct_wrapped_line_marks


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


def test_count_marked_text():
    text = [
        "Lorem ipsum dolor sit amet,",
        "consectetur adipiscing elit.",
        "Curabitur rutrum massa",  #2
        "pretium, pulvinar ligula a,",  #3
        "aliquam est. Proin ut lectus",  #4
        "ac massa fermentum commodo.",  #5
        "Duis ac urna a felis mollis",
        "laoreet. Nullam finibus nibh",
        "convallis, commodo nisl sit",
        "amet, vestibulum mauris. Nulla",
        "lacinia ultrices lacinia. Duis",
        "auctor nunc non felis",
        "ultricies, ut egestas tellus",
        "rhoncus. Aenean ultrices",
        "efficitur lacinia. Aliquam",
        "eros lacus, luctus eu lacinia",
        "in, eleifend nec nunc. Nam",
        "condimentum malesuada",
        "facilisis.",
    ]

    assert count_marked_text_len(text, 2, 3, 2, 19) == 17
    assert count_marked_text_len(text, 2, 3, 3, 5) == 25
    assert count_marked_text_len(text, 2, 3, 5, 2) == 77


