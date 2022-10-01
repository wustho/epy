import dataclasses
import sys
import os
import json
from typing import Mapping, Tuple, Union

import epy_reader.settings as settings
from epy_reader.models import AppData, Key


class Config(AppData):
    def __init__(self):
        setting_dict = dataclasses.asdict(settings.Settings())
        keymap_dict = dataclasses.asdict(settings.CfgDefaultKeymaps())
        keymap_builtin_dict = dataclasses.asdict(settings.CfgBuiltinKeymaps())

        if os.path.isfile(self.filepath):
            with open(self.filepath) as f:
                cfg_user = json.load(f)
            setting_dict = Config.update_dict(setting_dict, cfg_user["Setting"])
            keymap_dict = Config.update_dict(keymap_dict, cfg_user["Keymap"])
        else:
            self.save({"Setting": setting_dict, "Keymap": keymap_dict})

        keymap_dict_tuple = {k: tuple(v) for k, v in keymap_dict.items()}
        keymap_updated = {
            k: tuple([Key(i) for i in v])
            for k, v in Config.update_keys_tuple(keymap_dict_tuple, keymap_builtin_dict).items()
        }

        if sys.platform == "win32":
            setting_dict["PageScrollAnimation"] = False

        self.setting = settings.Settings(**setting_dict)
        self.keymap = settings.Keymap(**keymap_updated)
        # to build help menu text
        self.keymap_user_dict = keymap_dict

    @property
    def filepath(self) -> str:
        return os.path.join(self.prefix, "configuration.json") if self.prefix else os.devnull

    def save(self, cfg_dict):
        with open(self.filepath, "w") as file:
            json.dump(cfg_dict, file, indent=2)

    @staticmethod
    def update_dict(
        old_dict: Mapping[str, Union[str, int, bool]],
        new_dict: Mapping[str, Union[str, int, bool]],
        place_new=False,
    ) -> Mapping[str, Union[str, int, bool]]:
        """Returns a copy of `old_dict` after updating it with `new_dict`"""

        result = {**old_dict}
        for k, _ in new_dict.items():
            if k in result:
                result[k] = new_dict[k]
            elif place_new:
                result[k] = new_dict[k]

        return result

    @staticmethod
    def update_keys_tuple(
        old_keys: Mapping[str, Tuple[str, ...]],
        new_keys: Mapping[str, Tuple[str, ...]],
        place_new: bool = False,
    ) -> Mapping[str, Tuple[str, ...]]:
        """Returns a copy of `old_keys` after updating it with `new_keys`
        by appending the tuple value and removes duplicate"""

        result = {**old_keys}
        for k, _ in new_keys.items():
            if k in result:
                result[k] = tuple(set(result[k] + new_keys[k]))
            elif place_new:
                result[k] = tuple(set(new_keys[k]))

        return result
