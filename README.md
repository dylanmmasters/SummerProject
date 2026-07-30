# 🏈 Pi Sports Ticker

A physical, always-on sports scoreboard built for a small Raspberry Pi touchscreen. 
It tracks your favorite teams across the NBA, MLB, NHL, NFL, NCAAF, MLS, and the Premier League (plus upcoming UFC fights)
Automatically switching into a live in-game view — complete with sport-specific overlays like shots on goal, bonus indicators, ball/strike counts, and down & distance — whenever one of your teams is playing.
A built-in web dashboard lets you add or remove tracked teams from any phone or laptop on your network, no SSH required.


## Hardware
- Raspberry Pi 4 — Vilros Basic Starter Kit (4GB, fan-cooled ABS case)
- Hosyond 7" IPS Touchscreen — 800x480 DSI display, capacitive touch, driver-free MIPI interface
- Boots directly into the app in kiosk-style fullscreen — no monitor, keyboard, or mouse needed after setup

## Features
- Live rotation — Cycles through each tracked team's next/current game every few seconds
- Live game view — Tap the game to jump into a full-screen live scoreboard with sport-specific detail:
  - 🏒 NHL: period/clock + shots on goal
  - 🏀 NBA: quarter/clock + bonus indicators
  - ⚾ MLB: inning, count, outs, and baserunners on a diamond
  - 🏈 NFL/NCAAF: quarter/clock, down & distance, and ball position
  - ⚽ MLS/EPL: match clock and status
- Final score screen — Tap "LAST" to see the final result of each team's most recently completed game (with the winner highlighted)
- Auto-detects live games — If a locked/live game goes final while you're watching, it automatically transitions to the final score screen instead of dropping back to the rotation
- Touch controls — Cycle, lock, next-game, and back buttons built for finger taps on the touchscreen
- Web-based team manager — A lightweight Flask site (served on port 5000) lets you search each league's teams and add/remove them from the rotation from any device on your Wi-Fi
- Persistent config — Tracked teams are saved to a local JSON file and reloaded on every restart
- Startup notification — Pings an ntfy.sh topic with the Pi's local IP/web UI link on boot, so you always know where to find the dashboard
- Hidden exit gesture — An invisible tap zone in the top-right corner quits the fullscreen app for maintenance
- 🥊UFC: shows when the next UFC event is. UFC does not currently have a free api for live stats

## Tech Stack
- Pygame Fullscreen rendering loop, touch input, live/final scoreboard graphics
- Flask Web dashboard for managing tracked teams
- ESPN's public API	Schedules, live scores, box scores, and team logos
- zoneinfo Local time conversion for upcoming game times (America/New_York)

## Getting Started
1. Flash & set up the Pi
- Flash Raspberry Pi OS to your SD card using Raspberry Pi Imager, and enable SSH/Wi-Fi during setup if desired. Remember your username and hostname. https://www.raspberrypi.com/software/
- Insert the SD card into the Pi and connect the Hosyond display via the DSI ribbon cable per its included instructions
— It's plug-and-play so you can now turn the Pi on

2. Upload code to Pi
- Open a terminal
- Connect to the Pi using:
  ```bash
  ssh USERNAME@HOSTNAME
  ``` 
    - USERNAME = the login name on the Pi (ex: my_pi4)
    - HOSTNAME = the Pi’s network name (ex: sportsTicker.local)
    - IP_ADDRESS = the Pi’s actual network IP (ex: 192.168.1.xx)
- Clone GitHub
  ```bash
  git clone https://github.com/USERNAME/SummerProject.git
  ```
  - Confirm Clone
  ```bash
  cd SummerProject/Sports
  ls -la
  ```
    - Should see images, my_teams.json, sports_display_laptop.py, and sports_display_OLED.py
- Install dependencies
  ```bash
  pip install pygame flask requests --break-system-packages
  ```
- Run Code
  ```bash
  export DISPLAY=:0
  python3 sports_display_OLED.py
  ```

3. Setting up auto-start
   ```bash
   sudo raspi-config
   ```
    - Navigate to: System Options → Auto Login → Yes → Yes → Ok → Finish → Yes
    - Wait for Pi to finish restarting
   ```bash
   ssh USERNAME@HOSTNAME
   ```
    - Create the autostart foler and file
   ```bash
   mkdir -p ~/.config/autostart
   nano ~/.config/autostart/sports_ticker.desktop
   ```
    - Paste this and replace USERNAME with yours
    ```bash
    [Desktop Entry]
    Type=Application
    Name=Sports Ticker
    Exec=/usr/bin/python3 /home/USERNAME/SummerProject/Sports/sports_display_OLED.py
    Path=/home/USERNAME/SummerProject/Sports
    X-GNOME-Autostart-enabled=true
    ```
    - Save with Ctrl+O, Enter, and then Ctrl+X
    - Finally, reboot
    ```bash
    sudo reboot
    ```
        
Now the Pi boots straight into the live ticker, no login required.
The display can be closed by clicking the top-right corner of the screen. 

## Managing Teams
Visit http://IP_ADDRESS:5000 from your phone or computer to:
- View all currently tracked teams
- Search and add teams from any supported league
- Remove teams from the rotation

Changes take effect immediately — no restart needed.


## Notes

Currently tuned for a 800x480 display; other resolutions will need layout adjustments in the rendering functions.
Uses ESPN's unofficial public API endpoints, which may change without notice.
Built and tested for personal, non-commercial use.
Use sports_display_laptop.py to test implementations. This code does not make the screen fullscreen or hide your cursor, thus making the display easy to use on a laptop/monitor. Feel free to fork and adapt for your own setup.
