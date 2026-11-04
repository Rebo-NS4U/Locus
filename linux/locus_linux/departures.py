# SPDX-License-Identifier: GPL-3.0-or-later
"""Saved departure rules and playback waits, independent of map routing."""
import math
import time
from datetime import datetime, timedelta


def departure(value=None):
    value = value or {'mode': 'none'}
    mode = value.get('mode', 'none')
    if mode not in ('none', 'manual', 'timer', 'clock'):
        raise ValueError('Choose no stop, Continue, a timer or a departure time')
    result = {'mode': mode}
    if mode == 'timer':
        seconds = value.get('seconds', 60)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 604800:
            raise ValueError('Stop timer must be 1 second to 7 days')
        result['seconds'] = float(seconds)
    if mode == 'clock':
        text = value.get('time', '09:00')
        try:
            parsed = datetime.strptime(text, '%H:%M')
        except (TypeError, ValueError):
            raise ValueError('Departure time must be HH:MM') from None
        result['time'] = parsed.strftime('%H:%M')
    return result


class DepartureSchedule:
    def __init__(self, events, clock=time.time):
        self.events = sorted(events, key=lambda event: event['distance'])
        self.index = 0
        self.waiting = None
        self.remaining = 0
        self.deadline = None
        self.clock = clock

    @property
    def upcoming(self):
        return self.events[self.index] if self.index < len(self.events) else None

    def arrive(self):
        self.waiting = self.upcoming
        if not self.waiting:
            return
        self.remaining = self.waiting.get('seconds', 0)
        if self.waiting['mode'] == 'clock':
            now = datetime.fromtimestamp(self.clock())
            hour, minute = map(int, self.waiting['time'].split(':'))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            # Naive local timestamp uses the OS time-zone rules for the target day.
            self.deadline = target.timestamp()
            self.remaining = max(0, self.deadline - self.clock())

    def release(self):
        self.index += 1
        self.waiting, self.remaining, self.deadline = None, 0, None

    def hold(self, elapsed):
        if not self.waiting:
            return
        mode = self.waiting['mode']
        if mode == 'timer':
            self.remaining = max(0, self.remaining - elapsed)
        elif mode == 'clock':
            self.remaining = max(0, self.deadline - self.clock())
        if mode != 'manual' and self.remaining <= 0:
            self.release()

    def snapshot(self):
        if not self.waiting:
            return None
        return {**self.waiting, 'remaining': self.remaining, 'deadline': self.deadline}
