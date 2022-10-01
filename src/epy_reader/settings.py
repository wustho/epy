from enum import Enum


class DoubleSpreadPadding(Enum):
    LEFT = 10
    MIDDLE = 7
    RIGHT = 10


# add image viewers here
# sorted by most widely used
VIEWER_PRESET_LIST = (
    "feh",
    "imv",
    "gio",
    "gnome-open",
    "gvfs-open",
    "xdg-open",
    "kde-open",
    "firefox",
)

DICT_PRESET_LIST = (
    "wkdict",
    "sdcv",
    "dict",
)
