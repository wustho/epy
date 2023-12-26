__all__ = [
    "SpeakerBaseModel",
    "SpeakerSpeechd",
    "SpeakerMimic",
    "SpeakerPico",
    "SpeakerGttsMPV"
]

from epy_reader.speakers.base import SpeakerBaseModel
from epy_reader.speakers.speechd import SpeakerSpeechd
from epy_reader.speakers.mimic import SpeakerMimic
from epy_reader.speakers.pico import SpeakerPico
from epy_reader.speakers.gtts_mpv import SpeakerGttsMPV
