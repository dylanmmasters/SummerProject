🏈 Pi Sports Ticker

A physical, always-on sports scoreboard built for a small Raspberry Pi touchscreen. 
It tracks your favorite teams across the NBA, MLB, NHL, NFL, NCAAF, MLS, and the Premier League (plus upcoming UFC fights)
Automatically switching into a live in-game view — complete with sport-specific overlays like shots on goal, bonus indicators, ball/strike counts, and down & distance — whenever one of your teams is playing.
A built-in web dashboard lets you add or remove tracked teams from any phone or laptop on your network, no SSH required.


Hardware
- Raspberry Pi 4 — Vilros Basic Starter Kit (4GB, fan-cooled ABS case)
- Hosyond 7" IPS Touchscreen — 800x480 DSI display, capacitive touch, driver-free MIPI interface
- Boots directly into the app in kiosk-style fullscreen — no monitor, keyboard, or mouse needed after setup

Features
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

Tech Stack
-Pygame	  Fullscreen rendering loop, touch input, live/final scoreboard graphics
-Flask	  Web dashboard for managing tracked teams
-ESPN's   public API	Schedules, live scores, box scores, and team logos
-zoneinfo	Local time conversion for upcoming game times (America/New_York)

Getting Started
1. Flash & set up the Pi
Flash Raspberry Pi OS (Bullseye or later, with desktop) to your SD card using Raspberry Pi Imager, and enable SSH/Wi-Fi during setup if desired.
Connect the Hosyond display via the DSI ribbon cable per its included instructions — it's plug-and-play with no display driver installation required.

3. Clone the repo
- git clone https://github.com/<your-username>/pi-sports-ticker.git
- cd pi-sports-ticker

4. Install dependencies
- sudo apt update && sudo apt install -y python3-pip
- pip3 install pygame flask requests

5. Configure your teams
On first run, the app seeds my_teams.json with a default set of teams.
You can either edit that file directly or (recommended) launch the app once and use the web dashboard at http://<pi-ip>:5000 to search and add/remove teams visually.

5. Run it
- python3 sports_ticker.py
The display will launch fullscreen, and the web dashboard will be reachable at http://<pi-ip-address>:5000 from any device on the same network.

6. Launch on boot (kiosk mode)
To have the ticker start automatically when the Pi powers on, set it up as a systemd service:

[Unit]
Description=Pi Sports Ticker
After=graphical.target network-online.target
Wants=network-online.target

[Service]
Environment=DISPLAY=:0
ExecStart=/usr/bin/python3 /home/pi/pi-sports-ticker/sports_ticker.py
WorkingDirectory=/home/pi/pi-sports-ticker
User=pi
Restart=on-failure

[Install]
WantedBy=graphical.target

Enable it with:
- sudo systemctl enable sports-ticker.service
- sudo systemctl start sports-ticker.service

Now the Pi boots straight into the live ticker, no login required.
The live ticker can be closed by clicking the top-right corner of the screen. 

Managing Teams
Visit http://<pi-ip>:5000 from your phone or computer to:
- View all currently tracked teams
- Search and add teams from any supported league
- Remove teams from the rotation

Changes take effect immediately — no restart needed.


Notes
Currently tuned for a 800x480 display; other resolutions will need layout adjustments in the rendering functions.
Uses ESPN's unofficial public API endpoints, which may change without notice.
Built and tested for personal, non-commercial use.

License
MIT — feel free to fork and adapt for your own setup.
