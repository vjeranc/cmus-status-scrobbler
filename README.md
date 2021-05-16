# cmus-status-scrobbler

Works with [cmus](https://cmus.github.io/). Requires Python 3 and has no
additional dependencies.

You can just call it directly in your `status_display_program.sh`:
```bash
cmus_status_scrobbler.py "$@" &
```

**Features:**

* offline mode,
* multiple servers,
* now playing request,
* handles pause status well and
* is a standalone program (not continuously running server).

## Configuration

Example file `cmus_status_scrobbler.ini` is in the repository.

It is assumed that this configuration file is stored in `~/.config/cmus/`
directory. You can configure `db_path` and other options if you do not like the
defaults.

## Handling pause

Pausing a track will not make it scrobble. Continuing the paused track will
result in a scrobble if new playing time and playing time before pause satisfy
the scrobble requirement (playing time >= 50% of track duration or at least 4
minutes).

## Implementation details

Uses [sqlite3](https://docs.python.org/3/library/sqlite3.html) to support
offline mode and to synchronize the processes in case of multiple status
updates (like holding the pause/play button too long).
