from collections import namedtuple

from epy import CharPos, TextMark, TextSpan, resolve_path, HTMLtoLines


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


def test_mark_to_span():
    text = [
        "Lorem ipsum dolor sit amet,",
        "consectetur adipiscing elit.",
        "Curabitur rutrum massa",  # 2
        "pretium, pulvinar ligula a,",  # 3
        "aliquam est. Proin ut lectus",  # 4
        "ac massa fermentum commodo.",  # 5
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

    assert HTMLtoLines._mark_to_spans(
        text, [TextMark(start=CharPos(row=2, col=3), end=CharPos(row=2, col=19))]
    ) == [TextSpan(start=CharPos(row=2, col=3), n_letters=16)]

    assert HTMLtoLines._mark_to_spans(
        text,
        [
            TextMark(start=CharPos(row=2, col=3), end=CharPos(row=3, col=5)),
        ],
    ) == [
        TextSpan(start=CharPos(row=2, col=3), n_letters=19),
        TextSpan(start=CharPos(row=3, col=0), n_letters=5),
    ]

    assert HTMLtoLines._mark_to_spans(
        text,
        [
            TextMark(start=CharPos(row=2, col=3), end=CharPos(row=5, col=3)),
        ],
    ) == [
        TextSpan(start=CharPos(row=2, col=3), n_letters=19),
        TextSpan(start=CharPos(row=3, col=0), n_letters=27),
        TextSpan(start=CharPos(row=4, col=0), n_letters=28),
        TextSpan(start=CharPos(row=5, col=0), n_letters=3),
    ]


def test_span_adjustment():
    # 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Curabitur rutrum massa.'

    text = [
        "Lorem ipsum dolor",
        "sit amet,",
        "consectetur",
        "adipiscing elit.",
        "Curabitur rutrum",
        "massa.",
    ]

    assert HTMLtoLines._adjust_wrapped_spans(
        text, TextSpan(start=CharPos(row=0, col=2), n_letters=5)
    ) == [TextSpan(start=CharPos(row=0, col=2), n_letters=5)]

    assert HTMLtoLines._adjust_wrapped_spans(
        text, TextSpan(start=CharPos(row=0, col=15), n_letters=2)
    ) == [TextSpan(start=CharPos(row=0, col=15), n_letters=2)]

    assert HTMLtoLines._adjust_wrapped_spans(
        text, TextSpan(start=CharPos(row=0, col=14), n_letters=7)
    ) == [
        TextSpan(start=CharPos(row=0, col=14), n_letters=3),
        TextSpan(start=CharPos(row=1, col=0), n_letters=4),
    ]

    # assert HTMLtoLines._adjust_wrapped_spans(
    #     text, TextSpan(start=CharPos(row=1, col=7), n_letters=20)
    # ) == [TextSpan(start=CharPos(row=0, col=14), n_letters=3), TextSpan(start=CharPos(row=1, col=0), n_letters=4)]
