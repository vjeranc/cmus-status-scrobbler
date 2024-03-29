<p align=center><img src="https://user-images.githubusercontent.com/4954310/186776680-213451db-cbd5-45ff-8f36-29f4fb17459c.svg" width=30% height=30%></p>

# cmus-status-scrobbler

![tests passing status](https://github.com/vjeranc/cmus-status-scrobbler/actions/workflows/run-tests.yml/badge.svg?branch=main)

Works with [cmus](https://cmus.github.io/). Requires Python 3 and has no
additional dependencies.

**Features:**

* offline mode,
* multiple servers,
* now playing request,
* uses [MusicBrainz](https://musicbrainz.org) id if present,
* handles pause status well and
* is a short-lived program (not a continuously running process).

## How to use?

Leave `cmus_status_scrobbler.ini` file as is after removing servers you don't use.
1. Call the program with `--auth` option and follow instructions.
2. After authenticating with services, `cmus_status_scrobbler.ini` file is edited and saved with new credentials.
3. Set `cmus_status_scrobbler.py` as your only status display program by invoking
   `:set status_display_program=path/to/cmus_status_scrobbler.py` in `cmus` or add it to your
   existing script for `cmus`.

Bash script example:
```bash
# some other display programs
cmus_status_scrobbler.py "$@" &
# more display programs
```

### Termux

https://github.com/vjeranc/cmus-status-scrobbler/issues/10#issuecomment-1970103562
Make sure to run `chmod +rx status_display_program.sh` (or `chmod +rx cmus_status_scrobbler.py`) before `:set status_display_program=...`.

## Configuration

Example file `cmus_status_scrobbler.ini` is in the repository.

It is assumed that this configuration file is stored in `~/.config/cmus/`
directory. You can configure `db_path` and other options if you do not like the
defaults.

Delete the block for the service that you aren't using.

## Handling pause

Pausing a track will not make it scrobble. Continuing the paused track and 
finishing or stopping it will result in a scrobble if new playing time and 
playing time before pause satisfy the scrobble requirement (playing time >=
50% of track duration or at least 4 minutes).

## Implementation details

Uses [sqlite3](https://docs.python.org/3/library/sqlite3.html) to support
offline mode and to synchronize the processes in case of multiple status
updates (like holding the pause/play button too long).

## My own usage

I use this Python script to scrobble to librefm, lastfm, listenbrainz and
my own family scrobbling service.
