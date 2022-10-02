from typing import Any, Optional, Tuple
from urllib.parse import urljoin, urlparse


def is_url(string: str) -> bool:
    try:
        tmp = urlparse(string)
        return all([tmp.scheme, tmp.netloc])
    except ValueError:
        return False


def coerce_to_int(string: str) -> Optional[int]:
    try:
        return int(string)
    except ValueError:
        return None


def truncate(teks: str, subtitution_text: str, maxlen: int, startsub: int = 0) -> str:
    """
    Truncate text

    eg.
    :param teks: 'This is long silly dummy text'
    :param subtitution_text:  '...'
    :param maxlen: 12
    :param startsub: 3
    :return: 'This...ly dummy text'
    """
    if startsub > maxlen:
        raise ValueError("Var startsub cannot be bigger than maxlen.")
    elif len(teks) <= maxlen:
        return teks
    else:
        lensu = len(subtitution_text)
        beg = teks[:startsub]
        mid = (
            subtitution_text
            if lensu <= maxlen - startsub
            else subtitution_text[: maxlen - startsub]
        )
        end = teks[startsub + lensu - maxlen :] if lensu < maxlen - startsub else ""
        return beg + mid + end


def tuple_subtract(tuple_one: Tuple[Any, ...], tuple_two: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """
    Returns tuple with members in tuple_one
    but not in tuple_two
    """
    return tuple(i for i in tuple_one if i not in tuple_two)


def resolve_path(current_dir: str, relative_path: str) -> str:
    """
    Resolve path containing dots
    eg. '/foo/bar/book.html' + '../img.png' = '/foo/img.png'
    NOTE: '/' suffix is important to tell that current dir in 'bar'
    """
    # can also using os.path.normpath()
    # but if the image in zipfile then posix path is mandatory
    return urljoin(current_dir, relative_path)
