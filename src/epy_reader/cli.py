import textwrap
import argparse
import os
import sys
import shutil
from difflib import SequenceMatcher as SM
from typing import Tuple, List, Optional

from epy_reader import __version__
from epy_reader.lib import is_url, coerce_to_int, truncate, get_ebook_obj
from epy_reader.models import LibraryItem
from epy_reader.state import State
from epy_reader.parser import parse_html


def cleanup_library(state: State) -> None:
    """Cleanup non-existent file from library"""
    library_items = state.get_from_history()
    for item in library_items:
        if not os.path.isfile(item.filepath) and not is_url(item.filepath):
            state.delete_from_library(item.filepath)


def get_nth_file_from_library(state: State, n) -> Optional[LibraryItem]:
    library_items = state.get_from_history()
    try:
        return library_items[n - 1]
    except IndexError:
        return None


def get_matching_library_item(
    state: State, pattern: str, threshold: float = 0.5
) -> Optional[LibraryItem]:
    matches: List[Tuple[LibraryItem, float]] = []  # [(library_item, match_value), ...]
    library_items = state.get_from_history()
    if not library_items:
        return None

    for item in library_items:
        tomatch = f"{item.title} - {item.author}"  # item.filepath
        match_value = sum(
            [i.size for i in SM(None, tomatch.lower(), pattern.lower()).get_matching_blocks()]
        ) / float(len(pattern))
        matches.append(
            (
                item,
                match_value,
            )
        )

    sorted_matches = sorted(matches, key=lambda x: -x[1])
    first_match_item, first_match_value = sorted_matches[0]
    if first_match_item and first_match_value >= threshold:
        return first_match_item
    else:
        return None


def print_reading_history(state: State) -> None:
    termc, _ = shutil.get_terminal_size()
    library_items = state.get_from_history()
    if not library_items:
        print("No Reading History.")
        return

    print("Reading History:")
    dig = len(str(len(library_items) + 1))
    tcols = termc - dig - 2
    for n, item in enumerate(library_items):
        print(
            "{} {}".format(
                str(n + 1).rjust(dig),
                truncate(str(item), "...", tcols, tcols - 3),
            )
        )


def parse_cli_args() -> argparse.Namespace:
    prog = "epy"
    positional_arg_help_str = "[PATH | # | PATTERN | URL]"
    args_parser = argparse.ArgumentParser(
        prog=prog,
        usage=f"%(prog)s [-h] [-r] [-d] [-v] {positional_arg_help_str}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read ebook in terminal",
        epilog=textwrap.dedent(
            f"""\
        examples:
          {prog} /path/to/ebook    read /path/to/ebook file
          {prog} 3                 read #3 file from reading history
          {prog} count monte       read file matching 'count monte'
                                from reading history
        """
        ),
    )
    args_parser.add_argument("-r", "--history", action="store_true", help="print reading history")
    args_parser.add_argument("-d", "--dump", action="store_true", help="dump the content of ebook")
    args_parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"v{__version__}",
        help="print version and exit",
    )
    args_parser.add_argument(
        "ebook",
        action="store",
        nargs="*",
        metavar=positional_arg_help_str,
        help="ebook path, history number, pattern or URL",
    )
    return args_parser.parse_args()


def find_file() -> Tuple[str, bool]:
    args = parse_cli_args()
    state = State()
    cleanup_library(state)

    if args.history:
        print_reading_history(state)
        sys.exit()

    if len(args.ebook) == 0:
        last_read = state.get_last_read()
        if last_read:
            return last_read, args.dump
        else:
            sys.exit("ERROR: Found no last read ebook file.")

    elif len(args.ebook) == 1:
        nth = coerce_to_int(args.ebook[0])
        if nth is not None:
            file = get_nth_file_from_library(state, nth)
            if file:
                return file.filepath, args.dump
            else:
                print(f"ERROR: #{nth} file not found.")
                print_reading_history(state)
                sys.exit(1)
        elif is_url(args.ebook[0]):
            return args.ebook[0], args.dump
        elif os.path.isfile(args.ebook[0]):
            return args.ebook[0], args.dump

    pattern = " ".join(args.ebook)
    match = get_matching_library_item(state, pattern)
    if match:
        return match.filepath, args.dump
    else:
        sys.exit("ERROR: Found no matching ebook from history.")


def dump_ebook_content(filepath: str) -> None:
    ebook = get_ebook_obj(filepath)
    try:
        try:
            ebook.initialize()
        except Exception as e:
            sys.exit("ERROR: Badly-structured ebook.\n" + str(e))
        for i in ebook.contents:
            content = ebook.get_raw_text(i)
            src_lines = parse_html(content)
            assert isinstance(src_lines, tuple)
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j + "\n\n").encode("utf-8"))
    finally:
        ebook.cleanup()
