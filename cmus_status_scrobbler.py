import argparse
import configparser
from collections import namedtuple
import datetime
import hashlib
import json
import logging
import os
import sys
import urllib.parse as up
import urllib.request as ur
import itertools as it
from operator import attrgetter
import sqlite3
import pickle
from functools import reduce

CONFIG_PATH = '~/.config/cmus/cmus_status_scrobbler.ini'
DB_PATH = '~/.config/cmus/cmus_status_scrobbler.sqlite3'

parser = argparse.ArgumentParser(description="Scrobbling.")
parser.add_argument('--ini', type=str, default=os.path.expanduser(CONFIG_PATH))
parser.add_argument('--db-path', type=str, default=os.path.expanduser(DB_PATH))
parser.add_argument('--now-playing', type=bool, default=True, required=False)
parser.add_argument('--api-url', type=str, required=False)
parser.add_argument('--auth-url', type=str, required=False)


class StatusDB:
    def __init__(self, connection, table_name):
        self.con = connection
        self.table_name = f'status_updates_{table_name}'
        self.create()

    def create(self):
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table_name} (pickle BLOB)")

    def get_status_updates(self):
        cur = self.con.cursor()
        cur.execute(f"SELECT * FROM {self.table_name}")
        status_updates = []
        for row in cur:
            status_updates.append(pickle.loads(row[0]))
        return status_updates

    def clear(self):
        self.con.execute(f"DELETE FROM {self.table_name}")

    def save_status_updates(self, status_updates):
        self.con.executemany(
            f"INSERT INTO {self.table_name}(pickle) values (?)",
            [(pickle.dumps(su), ) for su in status_updates])


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


def send_req(api_url,
             api_key,
             ignore_request_fail=False,
             shared_secret=None,
             method=None,
             **params):
    params = dict(**params)
    params['api_key'] = api_key
    params['method'] = method
    params = {k: v for k, v in params.items() if v is not None}
    if shared_secret:
        params['api_sig'] = get_api_sig(params, secret=shared_secret)
    params['format'] = 'json'
    logging.info(params)
    api_req = ur.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with ur.urlopen(api_req, up.urlencode(params).encode('utf-8')) as f:
            res = f.read().decode('utf-8')
            logging.info(res)
            return json.loads(res) if res else None
    except Exception as e:
        if not ignore_request_fail:
            raise e
        logging.exception('Ignoring this error.')
    return None


class Scrobbler:
    def __init__(self, api_url, api_key, shared_secret, session_key):
        self.api_url = api_url
        self.api_key = api_key
        self.shared_secret = shared_secret
        self.sk = session_key

    def auth():
        pass

    def make_scrobble(self, i, su):
        return {
            f'artist[{i}]':
            su.artist,
            f'track[{i}]':
            su.title,
            f'timestamp[{i}]':
            str(
                int(
                    su.cur_time.replace(
                        tzinfo=datetime.timezone.utc).timestamp())),
            f'album[{i}]':
            su.album,
            f'trackNumber[{i}]':
            su.tracknumber,
            f'mbid[{i}]':
            su.musicbrainz_trackid,
            f'albumArtist[{i}]':
            su.albumartist if su.artist != su.albumartist else None,
            f'duration[{i}]':
            su.duration,
        }

    def scrobble(self, status_updates):
        if not status_updates:
            return
        # ignoring status updates with status other than playing
        # TODO do batches of 50
        playing_sus = filter(lambda x: x.status == CmusStatus.playing,
                             status_updates)
        batch_scrobble_request = reduce(lambda a, b: {
            **a,
            **b
        }, [self.make_scrobble(i, su) for (i, su) in enumerate(playing_sus)],
                                        dict(sk=self.sk))
        if not batch_scrobble_request:
            return
        send_req(self.api_url,
                 self.api_key,
                 shared_secret=self.shared_secret,
                 method=ScrobblerMethod.SCROBBLE,
                 **batch_scrobble_request)

    def send_now_playing(self, cur):
        if cur.status != CmusStatus.playing:
            return
        params = dict(artist=cur.artist,
                      track=cur.title,
                      album=cur.album,
                      trackNumber=cur.tracknumber,
                      duration=cur.duration,
                      albumArtist=cur.albumartist
                      if cur.artist != cur.albumartist else None,
                      mbid=cur.musicbrainz_trackid,
                      sk=self.sk)
        send_req(self.api_url,
                 self.api_key,
                 ignore_request_fail=True,
                 shared_secret=self.shared_secret,
                 method=ScrobblerMethod.NOW_PLAYING,
                 **params)


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
    return Status(**r)


def has_played_enough(start_ts,
                      end_ts,
                      duration,
                      perc_thresh,
                      secs_thresh,
                      ptbp=0):
    duration = int(duration)
    total = (end_ts - start_ts).total_seconds() + ptbp
    return total / duration >= perc_thresh or total >= secs_thresh


def equal_tracks(a, b):
    return a.file == b.file


def get_prefix_end_exclusive_idx(status_updates):
    r_su = list(reversed(status_updates))
    for i, (cur, prv) in enumerate(zip(r_su, r_su[1:])):
        if (cur.status == CmusStatus.stopped or not equal_tracks(cur, prv)
                or cur.status == prv.status
                or prv.status == CmusStatus.stopped):
            return len(r_su) - i
    return 0  # all statuses do not result in a scrobble


def calculate_scrobbles(status_updates, perc_thresh=0.5, secs_thresh=4 * 60):
    scrobbles, leftovers = [], []
    if not status_updates or len(status_updates) == 1:
        return scrobbles, status_updates or leftovers

    # if status updates array has a suffix of playing/paused updates with same
    # track, then these tracks need to be immediatelly leftovers
    prefix_end = get_prefix_end_exclusive_idx(status_updates)
    sus = sorted(status_updates[:prefix_end], key=attrgetter('cur_time'))
    # I am incapable of having simple thoughts. The pause is messing me up.
    # I use these two variables to scrobble paused tracks.
    ptbp = 0  # played time before pausing
    ptbp_status = None
    for cur, nxt, nxt2 in it.zip_longest(sus, sus[1:], sus[2:]):
        if cur.status in [CmusStatus.stopped, CmusStatus.paused]:
            continue
        if nxt is None:
            leftovers.append(cur)
            break
        hpe = has_played_enough(
            cur.cur_time,
            nxt.cur_time,
            cur.duration,
            perc_thresh,
            secs_thresh,
            ptbp=ptbp if ptbp_status and equal_tracks(ptbp_status, cur) else 0)

        if (not equal_tracks(cur, nxt)
                or nxt.status in [CmusStatus.stopped, CmusStatus.playing]):
            if hpe:
                scrobbles.append(cur)
            ptbp = 0
            ptbp_status = None
            continue

        # files are equal and nxt status paused
        if nxt2 is None:
            leftovers.append(cur)
            leftovers.append(nxt)
            continue

        if equal_tracks(cur, nxt2) and nxt2.status == CmusStatus.playing:
            # playing continued, keeping already played time for next
            ptbp += (nxt.cur_time - cur.cur_time).total_seconds()
            ptbp_status = cur if not ptbp_status else ptbp_status
            continue
        # playing did not continue, nxt2 file is not None and it's either a
        # different file or it's the same file but status is not playing
        # in this case we just check if played enough otherwise no scrobble
        if hpe:
            scrobbles.append(ptbp_status or cur)
    return scrobbles, leftovers + status_updates[prefix_end:]


class ScrobblerMethod:
    GET_TOKEN = 'auth.gettoken'
    GET_SESSION = 'auth.getsession'
    NOW_PLAYING = 'track.updateNowPlaying'
    SCROBBLE = 'track.scrobble'


def authenticate(auth_url, api_url, api_key, shared_secret):
    # TODO move to scrobbler
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


def update_scrobble_state(db, scrobbler, new_status_update):
    sus = db.get_status_updates()
    sus.append(new_status_update)
    db.save_status_updates([new_status_update])
    scrobbles, leftovers = calculate_scrobbles(sus)
    # scrobble the scrobbles, if fails then just append the new status update
    # to the db.
    try:
        scrobbler.scrobble(scrobbles)
    except Exception:
        logging.exception('Scrobbling failed')
        # scrobbling failed
        # do not clear and do not use the leftovers
        return
    db.clear()
    db.save_status_updates(leftovers)


def main():
    logging.basicConfig(
        filename='/home/vjeran/.config/cmus/cmus_scrobbler.log',
        encoding='utf-8',
        level=logging.DEBUG,
    )
    logging.info('Starting...')
    logging.info('Parsing arguments')
    logging.info(sys.argv[1:])
    args, _ = parser.parse_known_args()
    logging.info('Arguments parsed')
    logging.info(args)
    conf_path = args.ini
    if not os.path.exists(conf_path):
        raise FileNotFoundError(f'{conf_path} does not exist.')

    conf = configparser.ConfigParser()
    with open(conf_path, 'r') as f:
        conf.read_file(f)
    # TODO test concurrent writes, db should be locked immediatelly
    with sqlite3.connect(conf['global'].get('db_path', args.db_path),
                         timeout=60,
                         isolation_level='IMMEDIATE') as con:
        # using global if local not defined
        api_key = conf['global'].get('api_key')
        shared_secret = conf['global'].get('shared_secret')
        status = parse_cmus_status_line(sys.argv[1:])
        logging.info(repr(status))
        for section in conf.sections():
            if section == 'global':
                continue
            if 'session_key' in conf[section]:
                print(f'Session key already active for {section}. Skipping...')
                continue
            conf[section].update(
                authenticate(conf[section]['auth_url'],
                             conf[section]['api_url'],
                             conf[section].get('api_key', api_key),
                             conf[section].get('shared_secret',
                                               shared_secret)))
            with open(conf_path, 'w') as f:
                conf.write(f)
        for section in conf.sections():
            if section == 'global':
                continue
            logging.info(f'Scrobbling {section}')
            scr = Scrobbler(conf[section]['api_url'],
                            conf[section].get('api_key', api_key),
                            conf[section].get('shared_secret', shared_secret),
                            conf[section]['session_key'])
            if status.status == CmusStatus.playing:
                scr.send_now_playing(status)
            update_scrobble_state(StatusDB(con, section), scr, status)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception('Error happened')
