# Navi's Mint Torrent

A lightweight terminal BitTorrent client with a simple full-screen interface.

---

## Features

- 🧲 Download using magnet links
- 📄 Open `.torrent` files
- 📂 Choose your download location
- 📊 Live download progress
- ⌨️ Keyboard-friendly terminal interface
- ⚡ Automatically installs required Python packages

---

# Installation

Clone the repository:

```bash
git clone <repository-url>
cd <repository-folder>
```

Install Python 3.12+ if you don't already have it.

---

# Dependencies

The application uses:

- Python 3.12+
- `typer`
- `python-libtorrent`
- `windows-curses` (Windows only)

Most Python dependencies are installed automatically on first launch.

### Linux

If `python-libtorrent` isn't available through pip:

**Ubuntu/Debian**

```bash
sudo apt install python3-libtorrent
```

**Arch Linux**

```bash
sudo pacman -S python-libtorrent-rasterbar
```

---

# Running Navi's Mint

Launch the interactive interface:

```bash
python navi_torrent.py
```

Download from a magnet link:

```bash
python navi_torrent.py "magnet:?xt=..."
```

Download from a torrent file:

```bash
python navi_torrent.py file.torrent
```

Choose a custom download folder:

```bash
python navi_torrent.py file.torrent --path ~/Downloads
```

---

# Keyboard Controls

| Key | Action |
|------|--------|
| ↑ ↓ | Navigate |
| Enter | Select |
| ← | Go back |
| Space | Select current folder |
| Esc | Cancel |
| Ctrl + C | Quit |

---

# Default Download Location

```
~/Downloads/torrents
```

---

## Navi Torrent

Simple. Fast. Terminal-first.
