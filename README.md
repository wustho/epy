# `$ epy`

![screenshot](https://raw.githubusercontent.com/wustho/epy/master/screenshot.png)

CLI Ebook Reader.

This is just a fork of my own [epr](https://github.com/wustho/epr) with these extra features:

- Supported formats:
  - Epub (.epub, .epub3)
  - FictionBook (.fb2)
  - Mobi (.mobi), but image is not supported in mobi
  - AZW3 (.azw3), some but not all (see [KindleUnpack](https://github.com/kevinhendricks/KindleUnpack))
- Reading progress percentage
- Bookmarks
- External dictionary integration
- Inline formats: **bold** and _italic_ (depend on terminal and font capability. Italic only supported in python>=3.7)

# Installation

## Via PyPI

```shell
$ pip3 install epy-reader
```

## Via Pip+Git

```shell
$ pip3 install git+https://github.com/wustho/epy
```

# Reading Tips Using Epy

When reading using `epy` you might occasionally find triple asteriks `***`.
That means you reach the end of some section in your ebook and the next line (right after those three asteriks, which is in new section) will start at the top of the page.
This might be disorienting, so the best way to get seamless reading experience is by using next-page control (`space`, `l` or `Right`) instead of next-line control (`j` or `Down`).

# Using Mouse

| Key | Action |
| --- | --- |
| `Left Click` (right side of screen) | next page |
| `Left Click` (left side of screen) | prev page |
| `Right Click` | ToC |
| `Scroll Up` | scroll up |
| `Scroll Down` | scroll down |
| `Ctrl` + `Scroll Up` | increase text width |
| `Ctrl` + `Scroll Down` | decrease text width |
