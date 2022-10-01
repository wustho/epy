import os
import tempfile
import contextlib
import shutil
import xml.etree.ElementTree as ET
from typing import Union, Tuple

from epy_reader.ebooks.epub import Epub
from epy_reader.models import BookMetadata
from epy_reader.tools import unpack_kindle_book


class Mobi(Epub):
    def __init__(self, filemobi: str):
        self.path = os.path.abspath(filemobi)
        self.file = tempfile.mkdtemp(prefix="epy-")

        # populate these attribute
        # by calling self.initialize()
        self.root_filepath: str
        self.root_dirpath: str

    def get_meta(self) -> BookMetadata:
        # why self.file.read(self.root_filepath) problematic
        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            content_opf = ET.parse(f)  # .getroot()
        return Epub._get_metadata(content_opf)

    def initialize(self) -> None:
        assert isinstance(self.file, str)

        with contextlib.redirect_stdout(None):
            unpack_kindle_book(self.path, self.file, epubver="A", use_hd=True)
            # TODO: add cleanup here

        self.root_dirpath = os.path.join(self.file, "mobi7")
        self.toc_path = os.path.join(self.root_dirpath, "toc.ncx")
        version = "2.0"

        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            content_opf = ET.parse(f)  # .getroot()

        contents = Epub._get_contents(content_opf)
        self.contents = tuple(os.path.join(self.root_dirpath, content) for content in contents)

        with open(self.toc_path) as f:
            toc = ET.parse(f).getroot()
        self.toc_entries = Epub._get_tocs(toc, version, contents)  # *self.contents (absolute path)

    def get_raw_text(self, content_path: Union[str, ET.Element]) -> str:
        assert isinstance(content_path, str)
        with open(content_path, encoding="utf8") as f:
            content = f.read()
        # return content.decode("utf-8")
        return content

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        # TODO: test on windows
        # if impath "Images/asdf.png" is problematic
        image_abspath = os.path.join(self.root_dirpath, impath)
        image_abspath = os.path.normpath(image_abspath)  # handle crossplatform path
        with open(image_abspath, "rb") as f:
            src = f.read()
        return impath, src

    def cleanup(self) -> None:
        assert isinstance(self.file, str)
        shutil.rmtree(self.file)
        return
