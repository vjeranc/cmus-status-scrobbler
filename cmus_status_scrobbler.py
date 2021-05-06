import sys
import time
import urllib
import argparse
import configparser
import os
from collections import namedtuple
import logging
import datetime

CONFIG_PATH = '~/.config/cmus/cmus_status_scrobbler.ini'

parser = argparse.ArgumentParser(description="Scrobbling.")
parser.add_argument('--ini',
                    type=argparse.FileType('r'),
                    default=os.path.expanduser(CONFIG_PATH))
parser.add_argument('--now-playing', type=bool, default=True, required=False)
parser.add_argument('--api-url', type=str, required=False)
parser.add_argument('--auth-url', type=str, required=False)


class CmusStatus:
    stopped = "stopped"
    playing = "playing"
    paused = "paused"


class Log:
    @staticmethod
    def missing_playing_status(track):
        msg = 'Track {:}: missing playing status - not scrobbled'
        logging.info(msg.format(track.file))

    @staticmethod
    def not_played_enough(track):
        msg = 'Track {:}: has not played enough - not scrobbled'
        logging.info(msg.format(track.file))


Status = namedtuple('Status', [
    'status', 'file', 'artist', 'albumartist', 'album', 'discnumber',
    'tracknumber', 'title', 'date', 'duration', 'musicbrainz_trackid',
    'cur_time'
])


class Scrobbler:
    def auth():
        pass

    def scrobble():
        pass

    def send_now_playing():
        pass


class ScrobbleCache:
    # O_APPEND synchronous writing to file
    def __init__(self):
        pass

    def add():
        pass

    def remove():
        pass

    def clear():
        pass


def parse_cmus_status_line(ls):
    r = dict(
        cur_time=datetime.datetime.utcnow(),
        musicbrainz_trackid=None,
        discnumber=1,
        tracknumber=None,
        date=None,
        album=None,
        albumartist=None,
        artist=None,
    )
    r.update((k, v) for k, v in zip(ls[::2], ls[1::2]))
    logging.info(r)
    return Status(**r)


def has_played_enough(start_ts, end_ts, duration, perc_thresh, secs_thresh):
    duration = int(duration)
    total = (end_ts - start_ts).total_seconds()
    return total / duration >= perc_thresh or total >= secs_thresh


def calculate_scrobbles(status_updates, perc_thresh=0.5, secs_thresh=4 * 60):
    scrobbles, leftovers = [], []
    if not status_updates or len(status_updates) == 1:
        return scrobbles, leftovers

    sus = sorted(status_updates, key='cur_time')
    for cur, nxt in zip(sus, sus[1:]):
        if cur.status == CmusStatus.stopped:
            continue

        hpe = has_played_enough(cur.cur_time, nxt.cur_time, cur.duration,
                                perc_thresh, secs_thresh)

        if (cur.file != nxt.file
                or nxt.status in [CmusStatus.stopped, CmusStatus.playing]):
            if hpe:
                scrobbles.append(cur)
            continue

        # files are equal and status paused


if __name__ == "__main__":
    logging.basicConfig(
        filename='/home/vjeran/.config/cmus/cmus_scrobbler.log',
        encoding='utf-8',
        level=logging.DEBUG,
    )
    # args = parser.parse_args()
    # print(args)
    # conf_parser = configparser.ConfigParser()
    # print(conf_parser.sections())
    # conf_parser.read_file(args.ini)
    # print(conf_parser.sections())
    # args.ini.close()
    # print(conf_parser.items('global'))
    try:
        logging.info(repr(parse_cmus_status_line(sys.argv[1:])))
    except Exception as e:
        logging.error(e)
