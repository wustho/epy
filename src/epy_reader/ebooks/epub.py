import dataclasses
import os
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import unquote, urljoin

from epy_reader.ebooks.base import Ebook
from epy_reader.models import BookMetadata, TocEntry


class Epub(Ebook):
    NAMESPACE = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops",
        # Dublin Core
        "DC": "http://purl.org/dc/elements/1.1/",
    }

    def __init__(self, fileepub: str):
        self.path: str = os.path.abspath(fileepub)
        self.file: Union[zipfile.ZipFile, str] = zipfile.ZipFile(fileepub, "r")

        # populate these attributes
        # by calling self.initialize()
        self.root_filepath: str
        self.root_dirpath: str

    def get_meta(self) -> BookMetadata:
        assert isinstance(self.file, zipfile.ZipFile)
        # why self.file.read(self.root_filepath) problematic
        # content_opf = ET.fromstring(self.file.open(self.root_filepath).read())
        content_opf = ET.parse(self.file.open(self.root_filepath))
        return Epub._get_metadata(content_opf)

    @staticmethod
    def _get_metadata(content_opf: ET.ElementTree) -> BookMetadata:
        metadata: Dict[str, Optional[str]] = {}
        for field in dataclasses.fields(BookMetadata):
            element = content_opf.find(f".//DC:{field.name}", Epub.NAMESPACE)
            if element is not None:
                metadata[field.name] = element.text

        return BookMetadata(**metadata)

    @staticmethod
    def _get_contents(content_opf: ET.ElementTree) -> Tuple[str, ...]:
        # cont = ET.parse(self.file.open(self.root_filepath)).getroot()
        manifests: List[Tuple[str, str]] = []
        for manifest_elem in content_opf.findall("OPF:manifest/*", Epub.NAMESPACE):
            # EPUB3
            # if manifest_elem.get("id") != "ncx" and manifest_elem.get("properties") != "nav":
            if (
                manifest_elem.get("media-type") != "application/x-dtbncx+xml"
                and manifest_elem.get("properties") != "nav"
            ):
                manifest_id = manifest_elem.get("id")
                assert manifest_id is not None
                manifest_href = manifest_elem.get("href")
                assert manifest_href is not None
                manifests.append((manifest_id, manifest_href))

        spines: List[str] = []
        contents: List[str] = []
        for spine_elem in content_opf.findall("OPF:spine/*", Epub.NAMESPACE):
            idref = spine_elem.get("idref")
            assert idref is not None
            spines.append(idref)
        for spine in spines:
            for manifest in manifests:
                if spine == manifest[0]:
                    # book_contents.append(root_dirpath + unquote(manifest[1]))
                    contents.append(unquote(manifest[1]))
                    manifests.remove(manifest)
                    # TODO: test is break necessary
                    break

        return tuple(contents)

    @staticmethod
    def _get_tocs(toc: ET.Element, version: str, contents: Sequence[str]) -> Tuple[TocEntry, ...]:
        try:
            # EPUB3
            if version in {"1.0", "2.0"}:
                navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", Epub.NAMESPACE)
            elif version == "3.0":
                navPoints = toc.findall(
                    "XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a", Epub.NAMESPACE
                )

            toc_entries: List[TocEntry] = []
            for navPoint in navPoints:
                if version in {"1.0", "2.0"}:
                    src_elem = navPoint.find("DAISY:content", Epub.NAMESPACE)
                    assert src_elem is not None
                    src = src_elem.get("src")

                    name_elem = navPoint.find("DAISY:navLabel/DAISY:text", Epub.NAMESPACE)
                    assert name_elem is not None
                    name = name_elem.text
                elif version == "3.0":
                    src_elem = navPoint
                    assert src_elem is not None
                    src = src_elem.get("href")

                    name = "".join(list(navPoint.itertext()))

                assert src is not None
                src_id = src.split("#")

                try:
                    idx = contents.index(unquote(src_id[0]))
                except ValueError:
                    continue

                # assert name is not None
                # NOTE: skip empty label
                if name is not None:
                    toc_entries.append(
                        TocEntry(
                            label=name,
                            content_index=idx,
                            section=src_id[1] if len(src_id) == 2 else None,
                        )
                    )
        except AttributeError as e:
            # TODO:
            if DEBUG:
                raise e

        return tuple(toc_entries)

    def initialize(self) -> None:
        assert isinstance(self.file, zipfile.ZipFile)

        container = ET.parse(self.file.open("META-INF/container.xml"))
        rootfile_elem = container.find("CONT:rootfiles/CONT:rootfile", Epub.NAMESPACE)
        assert rootfile_elem is not None
        self.root_filepath = rootfile_elem.attrib["full-path"]
        self.root_dirpath = (
            os.path.dirname(self.root_filepath) + "/"
            if os.path.dirname(self.root_filepath) != ""
            else ""
        )

        content_opf = ET.parse(self.file.open(self.root_filepath))
        version = content_opf.getroot().get("version")

        contents = Epub._get_contents(content_opf)
        self.contents = tuple(urljoin(self.root_dirpath, content) for content in contents)

        if version in {"1.0", "2.0"}:
            # "OPF:manifest/*[@id='ncx']"
            relative_toc = content_opf.find(
                "OPF:manifest/*[@media-type='application/x-dtbncx+xml']", Epub.NAMESPACE
            )
        elif version == "3.0":
            relative_toc = content_opf.find("OPF:manifest/*[@properties='nav']", Epub.NAMESPACE)
        else:
            raise RuntimeError(f"Unsupported Epub version: {version}")
        assert relative_toc is not None
        relative_toc_path = relative_toc.get("href")
        assert relative_toc_path is not None
        toc_path = self.root_dirpath + relative_toc_path
        toc = ET.parse(self.file.open(toc_path)).getroot()
        self.toc_entries = Epub._get_tocs(toc, version, contents)  # *self.contents (absolute path)

    def get_raw_text(self, content_path: Union[str, ET.Element]) -> str:
        assert isinstance(self.file, zipfile.ZipFile)
        assert isinstance(content_path, str)

        max_tries: Optional[int] = None  # 1 if DEBUG else None

        # use try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # seems like caused by multiprocessing
        tries = 0
        while True:
            try:
                content = self.file.open(content_path).read()
                break
            except zlib.error as e:
                tries += 1
                if max_tries is not None and tries >= max_tries:
                    raise e

        return content.decode("utf-8")

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        assert isinstance(self.file, zipfile.ZipFile)
        return impath, self.file.read(impath)

    def cleanup(self) -> None:
        pass
