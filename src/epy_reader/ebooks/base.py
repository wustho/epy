import xml.etree.ElementTree as ET
from typing import Tuple, Union

from epy_reader.models import BookMetadata, TocEntry


class Ebook:
    def __init__(self, fileepub: str):
        raise NotImplementedError("Ebook.__init__() not implemented")

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = value

    @property
    def contents(self) -> Union[Tuple[str, ...], Tuple[ET.Element, ...]]:
        return self._contents

    @contents.setter
    def contents(self, value: Union[Tuple[str, ...], Tuple[ET.Element, ...]]) -> None:
        self._contents = value

    @property
    def toc_entries(self) -> Tuple[TocEntry, ...]:
        return self._toc_entries

    @toc_entries.setter
    def toc_entries(self, value: Tuple[TocEntry, ...]) -> None:
        self._toc_entries = value

    def get_meta(self) -> BookMetadata:
        raise NotImplementedError("Ebook.get_meta() not implemented")

    def initialize(self) -> None:
        raise NotImplementedError("Ebook.initialize() not implemented")

    def get_raw_text(self, content: Union[str, ET.Element]) -> str:
        raise NotImplementedError("Ebook.get_raw_text() not implemented")

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        raise NotImplementedError("Ebook.get_img_bytestr() not implemented")

    def cleanup(self) -> None:
        raise NotImplementedError("Ebook.cleanup() not implemented")
