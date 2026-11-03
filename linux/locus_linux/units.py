# SPDX-License-Identifier: GPL-3.0-or-later
"""Imperial display conversions; route files and engine speeds stay metric."""
import re

KPH_PER_MPH = 1.609344
METRES_PER_MILE = 1609.344


def to_mph(kph):
    return kph / KPH_PER_MPH


def to_kph(mph):
    return mph * KPH_PER_MPH


def speed_text(text):
    def convert(match):
        first = f"{to_mph(float(match[1])):.1f}"
        if match[3]:
            first += match[2] + f"{to_mph(float(match[3])):.1f}"
        return first + " mph"
    return re.sub(r"(\d+(?:\.\d+)?)(?:([–-])(\d+(?:\.\d+)?))? km/h", convert, text)
