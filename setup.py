import sys
from setuptools import setup

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="epy-reader",
    version="2022.4.18",
    description="Terminal/CLI Ebook (epub, fb2, mobi, azw3) Reader",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/wustho/epy",
    author="Benawi Adha",
    author_email="benawiadha@gmail.com",
    license="GPL-3.0",
    keywords=["epub", "epub3", "fb2", "mobi", "azw3", "CLI", "Terminal", "Reader"],
    python_requires="~=3.7",
    py_modules=["epy"],
    packages=["epy_extras", "epy_extras.KindleUnpack"],
    entry_points={"console_scripts": ["epy = epy:main"]},
    install_requires=["windows-curses;platform_system=='Windows'"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
)
