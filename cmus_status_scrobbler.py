import argparse
import configparser
from collections import namedtuple
import datetime
import hashlib
import json
import logging
import os
import sys
import time
import urllib.parse as up
import urllib.request as ur

CONFIG_PATH = '~/.config/cmus/cmus_status_scrobbler.ini'

parser = argparse.ArgumentParser(description="Scrobbling.")
parser.add_argument('--ini', type=str, default=os.path.expanduser(CONFIG_PATH))
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


def get_api_sig(params, secret=None):
    m = hashlib.md5()
    for k in sorted(params):
        m.update(k.encode('utf-8'))
        m.update(params[k].encode('utf-8'))
    m.update(secret.encode('utf-8'))
    return m.hexdigest()


class Scrobbler:
    def __init__(self, api_key, shared_secret):
        self.api_key = api_key
        self.shared_secret = shared_secret

    def auth():
        pass

    def scrobble():
        pass

    def send_now_playing(cur_status):
        if cur_status.status != CmusStatus.playing:
            return


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


class ScrobblerMethod:
    GET_TOKEN = 'auth.gettoken'
    GET_SESSION = 'auth.getsession'
    NOW_PLAYING = 'track.updateNowPlaying'


def send_req(api_url, api_key, shared_secret=None, method=None, **params):
    params = dict(**params)
    params['api_key'] = api_key
    params['method'] = method
    if shared_secret:
        params['api_sig'] = get_api_sig(params, secret=shared_secret)
    params['format'] = 'json'
    api_req = ur.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})

    with ur.urlopen(api_req, up.urlencode(params).encode('utf-8')) as f:
        return json.loads(f.read().decode('utf-8'))


def authenticate(auth_url, api_url, api_key, shared_secret):
    # fetching token that is used to ask for access
    # headers= makes it work on libre.fm
    token = send_req(api_url, api_key,
                     method=ScrobblerMethod.GET_TOKEN)['token']
    print(f'{auth_url}?api_key={api_key}&token={token}')
    input('Press <Enter> after visiting the link and allowing access...')
    # fetching session with infinite lifetime that is used to scrobble
    session = send_req(api_url,
                       api_key,
                       shared_secret=shared_secret,
                       method=ScrobblerMethod.GET_SESSION,
                       token=token)['session']
    return dict(session_key=session['key'], username=session['name'])


if __name__ == "__main__":
    logging.basicConfig(
        filename='/home/vjeran/.config/cmus/cmus_scrobbler.log',
        encoding='utf-8',
        level=logging.DEBUG,
    )
    args = parser.parse_args()
    print(args)
    conf_path = args.ini
    if not os.path.exists(conf_path):
        raise FileNotFoundError(f'{conf_path} does not exist.')

    conf = configparser.ConfigParser()
    with open(conf_path, 'r') as f:
        conf.read_file(f)
    api_key, shared_secret = None, None  # using global if local not defined
    for section in conf.sections():
        if section == 'global':
            api_key = conf[section]['api_key']
            shared_secret = conf[section]['shared_secret']
            continue
        if 'session_key' in conf[section]:
            print(f'Session key already active for {section}. Skipping...')
            continue
        conf[section].update(
            authenticate(
                conf[section]['auth_url'], conf[section]['api_url'],
                conf[section].get('api_key', None) or api_key,
                conf[section].get('shared_secret', None) or shared_secret))
        with open(conf_path, 'w') as f:
            conf.write(f)

    try:
        logging.info(repr(parse_cmus_status_line(sys.argv[1:])))
    except Exception as e:
        logging.error(e)
