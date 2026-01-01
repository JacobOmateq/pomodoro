# Pomodoro Timer

A terminal-based Pomodoro timer with statistics tracking, web interface, and CalDAV calendar synchronization.

## Features

- 🍅 **Terminal-based timer** - Simple command-line interface for tracking work sessions
- 📊 **Statistics dashboard** - Beautiful web interface to view your productivity metrics
- 📅 **CalDAV sync** - Automatically sync sessions to your calendar
- 🎨 **Color-coded tasks** - Each task gets a unique color for easy visualization
- 💾 **Local storage** - All data stored locally in SQLite database
- 📈 **Flexible reporting** - View stats by week, month, year, or all time

## Quick Start

### Install via Homebrew (Recommended)

```bash
brew tap JacobOmateq/pomodoro
brew install pomodoro
```

### Manual Installation

If you don't use Homebrew:

```bash
# Clone the repository
git clone https://github.com/JacobOmateq/pomodoro.git
cd pomodoro

# Install dependencies
pip3 install -r requirements.txt

# Make executable and add to PATH
chmod +x pomodoro.py
sudo ln -s $(pwd)/pomodoro.py /usr/local/bin/pomodoro
```

## Usage

### Start a Pomodoro Session

```bash
# Default 25-minute session
pomodoro coding

# Custom duration (supports h, m, and combinations)
pomodoro learning_option_trading 1h
pomodoro reading 30m
pomodoro deep_work 2h30m
```

### View Your Statistics

**Web Interface** (recommended):
```bash
pomodoro stats
```
This opens a web browser with interactive charts and detailed statistics.

**Terminal Stats**:
```bash
pomodoro s w    # Week statistics
pomodoro s m    # Month statistics
pomodoro s y    # Year statistics
pomodoro s a    # All-time statistics
```

### Examples

```bash
# Start a 25-minute coding session
pomodoro coding

# Start a 1-hour learning session
pomodoro learning 1h

# Start a 2.5-hour deep work session
pomodoro deep_work 2h30m

# View this week's productivity
pomodoro s w

# Open the web dashboard
pomodoro stats
```

## Configuration

All configuration and data is stored in `~/.pomodoro/`:

- **`sessions.db`** - SQLite database containing all your session history
- **`task_colors.json`** - Automatic color assignments for each task
- **`caldav_config.json`** - CalDAV calendar synchronization settings

### Setting up CalDAV Sync

1. Create/edit `~/.pomodoro/caldav_config.json`:
```json
{
  "url": "https://your-calendar-server.com/caldav/",
  "username": "your-username",
  "password": "your-password",
  "calendar_name": "Pomodoro Sessions"
}
```

2. Sessions will automatically sync to your calendar when configured.

## Requirements

- Python 3.x
- macOS or Linux (Windows support via WSL)

Dependencies are automatically installed with Homebrew or via `pip3 install -r requirements.txt`.

## Troubleshooting

**Command not found after installation:**
- Make sure `/opt/homebrew/bin` (or `/usr/local/bin` for Intel Macs) is in your PATH
- Try restarting your terminal

**Web interface won't open:**
- Check if port 5000 is already in use
- The server runs on `http://localhost:5000` by default

**Statistics not showing:**
- Make sure you've completed at least one Pomodoro session
- Check that `~/.pomodoro/sessions.db` exists

---

## For Developers

### Development Setup

```bash
# Clone the repository
git clone https://github.com/JacobOmateq/pomodoro.git
cd pomodoro

# Install dependencies
pip3 install -r requirements.txt

# Run directly
python3 pomodoro.py coding 25m
```

### Project Structure

- `pomodoro.py` - Main application file
- `requirements.txt` - Python dependencies
- `Formula/pomodoro.rb` - Homebrew formula
- `update_formula_sha.sh` - Helper script to update SHA256 checksum

### Updating the Homebrew Formula

When updating the repository, update the SHA256 in the formula:

```bash
./update_formula_sha.sh main
```

Then update the formula in the `homebrew-pomodoro` repository:

```bash
cd ../homebrew-pomodoro
cp ../pomodoro/Formula/pomodoro.rb Formula/pomodoro.rb
git add Formula/pomodoro.rb
git commit -m "Update pomodoro formula"
git push
```

### Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### License

[Add your license here]
