# Pomodoro Timer

A terminal-based Pomodoro timer with statistics tracking, web interface, and CalDAV calendar synchronization.

## Features

- 🍅 Terminal-based Pomodoro timer
- 📊 Statistics tracking with web interface
- 📅 CalDAV calendar synchronization
- 🎨 Color-coded task visualization
- 💾 SQLite database for session storage
- 📈 View stats by week, month, year, or all time

## Installation

### Option 1: Install via Homebrew (Recommended)

#### Method A: Using a Homebrew Tap

**Step 1: Create a Homebrew Tap Repository**

Create a GitHub repository named `homebrew-pomodoro` (or `homebrew-<yourname>` for a personal tap):

```bash
# Create a new repository on GitHub named 'homebrew-pomodoro'
# Then clone it locally
git clone https://github.com/JacobOmateq/homebrew-pomodoro.git
cd homebrew-pomodoro
```

**Step 2: Add the Formula**

Copy the formula file to your tap:

```bash
# Copy the formula from this repository
cp /path/to/pomodoro/Formula/pomodoro.rb Formula/pomodoro.rb

# Update the SHA256 (optional, but recommended for releases)
# You can get the SHA256 by downloading the zip and running:
# shasum -a 256 /path/to/downloaded/zip

# Commit and push
git add Formula/pomodoro.rb
git commit -m "Add pomodoro formula"
git push origin main
```

**Step 3: Install via Homebrew**

```bash
brew tap JacobOmateq/pomodoro
brew install pomodoro
```

#### Method B: Direct Installation from GitHub

You can also install directly from the GitHub repository:

```bash
brew install --build-from-source /path/to/pomodoro/Formula/pomodoro.rb
```

Or create a tap in your local Homebrew:

```bash
# Create tap directory
mkdir -p $(brew --repository)/Library/Taps/JacobOmateq/homebrew-pomodoro/Formula

# Copy formula
cp Formula/pomodoro.rb $(brew --repository)/Library/Taps/JacobOmateq/homebrew-pomodoro/Formula/

# Install
brew install JacobOmateq/pomodoro/pomodoro
```

**Note:** For production use, it's recommended to create a proper tap repository and optionally create GitHub releases with version tags for better version management.

**Updating the Formula SHA256:**

When you update the repository, you'll need to update the SHA256 in the formula. Use the provided helper script:

```bash
./update_formula_sha.sh [version_tag]
# Example: ./update_formula_sha.sh main
# Example: ./update_formula_sha.sh v1.0.0
```

### Option 2: Manual Installation

1. Clone this repository:
```bash
git clone https://github.com/JacobOmateq/pomodoro.git
cd pomodoro
```

2. Install Python dependencies:
```bash
pip3 install -r requirements.txt
```

3. Make the script executable and add to PATH:
```bash
chmod +x pomodoro.py
sudo ln -s $(pwd)/pomodoro.py /usr/local/bin/pomodoro
```

## Usage

### Start a Pomodoro Session

```bash
# Default 25-minute session
pomodoro <task_name>

# Custom duration
pomodoro learning_option_trading 1h
pomodoro coding 30m
pomodoro reading 2h30m
```

### View Statistics

```bash
# Open web interface
pomodoro stats

# Terminal stats (week)
pomodoro s w

# Terminal stats (month)
pomodoro s m

# Terminal stats (year)
pomodoro s y

# Terminal stats (all time)
pomodoro s a
```

## Configuration

The application stores data in `~/.pomodoro/`:
- `sessions.db` - SQLite database with session history
- `task_colors.json` - Color assignments for tasks
- `caldav_config.json` - CalDAV calendar sync configuration

## Requirements

- Python 3.x
- Flask >= 2.0.0
- icalendar >= 5.0.0
- caldav >= 1.3.0

## License

[Add your license here]

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

