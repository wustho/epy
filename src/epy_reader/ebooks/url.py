from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from pathlib import PurePosixPath
from typing import Tuple

from epy_reader.ebooks import Ebook
from epy_reader.models import BookMetadata
from epy_reader.lib import is_url
from epy_reader import __version__


class URL(Ebook):
    _header = {
        "User-Agent": f"epy/v{__version__}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }

    def __init__(self, url: str):
        self.path = url
        self.file = url
        self.contents = ("_",)
        self.toc_entries = tuple()

    def get_meta(self) -> BookMetadata:
        return BookMetadata()

    def initialize(self) -> None:
        try:
            with urlopen(Request(self.path, headers=URL._header)) as response:
                self.html = response.read().decode()
        except HTTPError as e:
            raise e
        except URLError as e:
            raise e

    def get_raw_text(self, _) -> str:
        return self.html

    def get_img_bytestr(self, src: str) -> Tuple[str, bytes]:
        image_url = src if is_url(src) else urljoin(self.path, src)
        # TODO: catch error on request
        with urlopen(Request(image_url, headers=URL._header)) as response:
            byte_str = response.read()
        return PurePosixPath(urlparse(src).path).name, byte_str

    def cleanup(self) -> None:
        return
