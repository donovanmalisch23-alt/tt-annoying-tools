# Top Speed — web edition

A browser port of [Top Speed](https://github.com/Diamondstar35/top_speed), the audio racing game
remade by Diamondstar35 (based on Top Speed 3 by Playing in the Dark), designed for phones and
tablets on iOS and Android. Everything is played by ear; menus and races are driven by touch
gestures.

## What's included

- The original menu tree: Quick start, Time trial, Single race, MultiPlayer game, Options, Help,
  Check for updates, About, Exit Game, plus track type / track / vehicle / transmission selection.
- All 17 race tracks and 7 street adventures, extracted from the original track catalog
  (`tools/extract_tracks.py`).
- All 12 official vehicles with their original engine, start, horn, throttle, brake, crash and
  backfire sounds.
- The original sounds: surfaces, copilot calls (spoken or tone cues), race information voice
  clips, radio clicks, track noises, weather and ambience, menu sounds, music and the logo.
- Computer opponents, positions and commentary, lap announcements, crashes and bumps, manual and
  automatic gearboxes, track records for time trials.
- The original mobile gestures for menus and driving (see **Help → Touch gestures** in the game).
- Spoken output with the browser's voice, or through VoiceOver / TalkBack via a live region.

Not included: multiplayer (needs the Top Speed server), custom tracks and vehicles, fuel, tire
wear and pit stops.

## Running it

Requirements: Python 3.8 or newer. Nothing to install.

```sh
cd topspeed-web
python3 server.py
```

The server prints the addresses it is listening on:

```
Top Speed web is running.
  On this computer:  http://localhost:8000/
  On your phone:     http://192.168.1.23:8000/   (same Wi-Fi network)
```

- **On the computer running the server:** open `http://localhost:8000/`.
- **On a phone or tablet:** connect it to the same Wi-Fi network as the computer and open the
  "On your phone" address in Safari (iOS) or Chrome (Android). If it does not load, allow Python
  through the computer's firewall for private networks.
- Use `--port 9000` for another port.

Then:

1. Turn the volume up (on iPhone the silent switch can mute web audio on older iOS versions).
   Headphones give the best sense of where you are on the road.
2. Tap **Tap to start**. The Playing in the Dark logo plays (tap to skip), then the main menu.
3. Swipe right/left to move through the menu, swipe up (or double tap) to choose, swipe down to
   go back.
4. Optional: in Safari use **Share → Add to Home Screen**, in Chrome **⋮ → Add to Home screen**,
   to launch it full screen.

### Motion steering (tilt to steer)

iOS only gives motion sensors to pages served over HTTPS. Start the server with:

```sh
python3 server.py --https
```

It creates a self-signed certificate (needs `openssl`) and serves `https://…:8000/`. Your phone
will warn that the certificate is not trusted; accept it to continue. Then turn on
**Options → Controls → Motion steering**. Android Chrome also requires HTTPS for motion
sensors on non-localhost addresses.

## Controls

### Menus

| Gesture | Action |
| --- | --- |
| Swipe right / left | Next / previous item |
| Swipe up, or double tap | Activate |
| Swipe down | Back |
| Tap | Repeat current item |
| Two-finger swipe up / down | First / last item |
| Two-finger swipe left / right | Change a setting, check box or slider |
| On a slider: two-finger swipe up / down | Big step up / down |
| On a slider: three-finger swipe up / down | Maximum / minimum |

### Driving

The screen has three zones, as in the mobile build of the original game.

| Zone | Gesture | Action |
| --- | --- | --- |
| Bottom half | Drag right / left from where you touch | Throttle / brake (further = stronger) |
| Bottom half | Drag up / down | Steer left / right |
| Bottom half | Two-finger swipe up / down | Shift up / down (manual) |
| Bottom half | Double tap | Restart a stalled engine |
| Top left | Hold one finger | Clutch |
| Top left | Double tap | Speed |
| Top left | Two-finger double tap | Race percentage |
| Top left | Three-finger double tap | Lap percentage |
| Top left | Two-finger triple tap | Race time |
| Top left | Three-finger triple tap | Pause / continue |
| Top left | Swipe down | Race menu (resume, restart, quit) |
| Top right | Hold one finger | Horn |
| Top right | Double tap | Lap and turn |
| Top right | Two-finger double tap | Distance driven and to go |
| Top right | Three-finger tap | Gear |
| Top right | Three-finger double tap | Race information |
| Top right | Two-finger swipe up / down | Next / previous player info |
| Top right | Two-finger triple tap | Repeat player info |

**Options → Controls → Touch driving layout** switches to an alternative where dragging up/down
is throttle/brake and left/right steers.

Keyboard (for testing on a computer): arrows drive, A/Z shift, Space horn, Shift clutch,
S/D/G/L/R/E/T/I reports, P pause, Escape race menu.

### Screen readers

Menus are real buttons, so VoiceOver and TalkBack can operate them directly. Set
**Options → Speech → Speech output** to *screen reader only* to avoid hearing two voices. While
racing, turn the screen reader off (or use its pass-through gesture) so the game receives your
touches.

## Project layout

```
index.html, css/style.css     page shell
js/main.js                    app, menu tree, race lifecycle
js/menu.js                    audio menu system and menu gestures
js/gestures.js                multi-finger swipe / tap / long-press recognition with zones
js/race.js                    race session (single race, time trial), drive gestures, reports
js/car.js                     player car and computer drivers
js/track.js                   road model, copilot timing, track noises
js/audio.js                   Web Audio engine (frequency / pan / volume per sound)
js/speech.js                  speech synthesis and screen-reader live region
js/data-tracks.js             generated track catalog
js/data-vehicles.js           vehicle table
assets/Sounds/                original sounds (+ .mp3 copies of the .ogg files for older Safari)
server.py                     local web server
tools/extract_tracks.py       regenerates js/data-tracks.js from the original C# catalog
```

## License

Top Speed is free software under the GNU General Public License version 3; this port and the
bundled original assets are under the same license (`assets/LICENSE-original-GPLv3.txt`).
