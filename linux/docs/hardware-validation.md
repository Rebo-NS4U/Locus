# Physical iPhone validation

Date: 2026-11-03. Host: Arch Linux, Python 3.14. Phone OS: iOS 26.6.2.
No device identifiers, owner names, pairing records or real coordinates are stored
in this repository. Test coordinates were public sample points in London around
51.5000, −0.1200. The user authorized temporary location/movement testing and clear.

## USB results

| Check | Result / evidence |
| --- | --- |
| Detection and trust | One USB iPhone found; pairing and Developer Mode confirmed. |
| Developer image | Prepare completed successfully. |
| Rootless transport | Opened and closed DVT location service without administrator phone transport. |
| Fixed location | Initial command and repeated refresh returned successfully; four set acknowledgements during the initial 3.2-second observation. |
| Joystick | Eastward commands advanced the acknowledged target about 2.58 meters; release held the same target through another refresh. This distance is computed from commands, not a GPS measurement. |
| Route | Progress advanced, remained unchanged during pause, advanced after resume, then held on stop. |
| Loop | Progress reset after reaching the end of a short route. |
| Clear and cleanup | Sent Apple's clear-location command, closed the transport, removed the recovery journal and observed no further set calls after cleanup. |
| Final local state | No unresolved Locus session journal remained. |

Set operations await the device's developer-service response. Apple's clear API
does not return an independent real-GPS measurement. These tests verify the
command pathway and application lifecycle; they do not prove the visible location
in Maps or every other phone app. No screenshots or GPS readings from the phone
were collected.

Initial host setup exposed missing `usbmuxd`. Installing it while the phone was
already connected required applying the installed device rule and rescanning the
service after its USB node permissions changed. Reconnecting the phone after
installation is the ordinary setup path. Locus now reports an actionable message
when that service is unavailable.

## Wi-Fi result

Saved trusted pairing and enabled Wi-Fi connections over USB. Both the normal
trusted-device scan and a longer direct Bonjour scan found zero Apple wireless
service advertisements. No Wi-Fi location coordinates were sent. USB's prior
clear/cleanup had already completed before this check.

Wi-Fi set/refresh/clear cannot be marked verified until the unlocked phone is
reachable on the same LAN and advertises its wireless service. The transport's
identity, heartbeat and cleanup logic has automated coverage with controlled
doubles, which is not a replacement for this hardware check.

## Still pending

The 0.3 mixed-segment planner and automatic driving speeds were verified with
automated motion tests, the native demo editor and live public routing/speed-data
requests. They have not been separately re-tested on the physical phone. The USB
command tests above describe the earlier controller workflow.

- Independent observation of the selected and cleared location inside a phone app.
- Physical cable unplug/replug during an active session and clear after recovery.
- Wi-Fi set/refresh/clear on a discoverable phone, and restoration after a Wi-Fi loss.
