#!/usr/bin/env python

from collections import namedtuple
from functools import reduce
from operator import attrgetter
import argparse
import configparser
import datetime
import hashlib
import itertools as it
import json
import logging
import os
import pickle
import sqlite3
import time
import urllib.parse as up
import urllib.request as ur

CONFIG_PATH = '~/.config/cmus/cmus_status_scrobbler.ini'
DB_CONNECT_TIMEOUT = 300
DB_PATH = '~/.config/cmus/cmus_status_scrobbler.sqlite3'
SCROBBLE_BATCH_SIZE = 50

parser = argparse.ArgumentParser(description="Scrobbling.")
parser.add_argument('--auth',
                    action='store_true',
                    help="Add if you're missing session_key in .ini file.")
parser.add_argument('--ini',
                    type=str,
                    default=os.path.expanduser(CONFIG_PATH),
                    help='Path to .ini configuration file.')
parser.add_argument('--db-path',
                    type=str,
                    default=os.path.expanduser(DB_PATH),
                    help='Path to sqlite3 database')
parser.add_argument(
    '--log-path',
    type=str,
    required=False,
    help='If given logging will be saved to desired path (default: no logging)'
)
parser.add_argument('--log-db',
                    action='store_true',
                    default=False,
                    help='If given, SQL queries are logged')


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
        if not status_updates:
            return
        self.con.executemany(
            f"INSERT INTO {self.table_name}(pickle) values (?)",
            [(pickle.dumps(su), ) for su in status_updates])


class CmusStatus:
    stopped = "stopped"
    playing = "playing"
    paused = "paused"


Status = namedtuple('Status', [
    'status', 'file', 'artist', 'albumartist', 'album', 'discnumber',
    'tracknumber', 'title', 'date', 'duration', 'musicbrainz_trackid',
    'cur_time'
])


def get_api_sig(params, secret):
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
             xml=False,
             timeout_secs=10.,
             **params):
    params = dict(**params)
    params['api_key'] = api_key
    params['method'] = method
    params = {k: v for k, v in params.items() if v is not None}
    if shared_secret:
        params['api_sig'] = get_api_sig(params, shared_secret)
    if not xml:
        params['format'] = 'json'
    logging.info(params)
    api_req = ur.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with ur.urlopen(api_req, up.urlencode(params, encoding='utf-8').encode(), timeout=timeout_secs) as f:
            res = f.read().decode('utf-8')
            logging.info(res)
            if not res:
                return None
            if not xml:
                return json.loads(res)
            return res
    except Exception as e:
        if not ignore_request_fail:
            raise e
        logging.exception('Ignoring error.')
    return None


class Scrobbler:
    def __init__(self, name, api_url, api_key, shared_secret, session_key,
                 now_playing, xml=False):
        self.name = name
        self.api_url = api_url
        self.api_key = api_key
        self.shared_secret = shared_secret
        self.sk = session_key
        self.now_playing = now_playing
        self.xml = xml

    @staticmethod
    def auth(auth_url, api_url, api_key, shared_secret, xml=False):
        # fetching token that is used to ask for access
        token = send_req(api_url, api_key,
                         method=ScrobblerMethod.GET_TOKEN, xml=xml)
        if xml:
            token = token.split("<token>")[1].split("</token>")[0]
        else:
            token = token['token']
        print(f'{auth_url}?' + up.urlencode(dict(token=token,api_key=api_key)))
        input('Press <Enter> after visiting the link and allowing access...')
        # fetching session with infinite lifetime that is used to scrobble
        session = send_req(api_url,
                           api_key,
                           shared_secret=shared_secret,
                           method=ScrobblerMethod.GET_SESSION,
                           token=token, xml=xml)
        if xml:
            session = dict(
                key=session.split("<key>")[1].split("</key>")[0],
                name=session.split("<name>")[1].split("</name>")[0])
        else:
            session = session['session']
        return dict(session_key=session['key'], username=session['name'])

    @staticmethod
    def make_scrobble(i, su):
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
        logging.info(f'Scrobbling previous tracks for {self.name}')
        # ignoring status updates with status other than playing
        playing_sus = filter(lambda x: x.status == CmusStatus.playing,
                             status_updates)
        batch_scrobble_request = reduce(lambda a, b: {
            **a,
            **b
        }, [Scrobbler.make_scrobble(i, su) for (i, su) in enumerate(playing_sus)],
                                        dict(sk=self.sk))
        if not batch_scrobble_request:
            return
        send_req(self.api_url,
                 self.api_key,
                 shared_secret=self.shared_secret,
                 method=ScrobblerMethod.SCROBBLE,
                 xml=self.xml,
                 timeout_secs=5.,  # scrobbling is not critical (saved in db)
                 **batch_scrobble_request)

    def send_now_playing(self, cur):
        if not self.now_playing or cur.status != CmusStatus.playing:
            return

        logging.info(f'Sending now playing for {self.name}')
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
                 xml=self.xml,
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
    sus = sorted(status_updates, key=attrgetter('cur_time'))
    prefix_end = get_prefix_end_exclusive_idx(sus)
    lsus = sus[:prefix_end]
    # I am incapable of having simple thoughts. The pause is messing me up.
    # I use these two variables to scrobble paused tracks.
    ptbp = 0  # played time before pausing
    ptbp_status = None
    for cur, nxt, nxt2 in it.zip_longest(lsus, lsus[1:], lsus[2:]):
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
    return scrobbles, leftovers + sus[prefix_end:]


class ScrobblerMethod:
    GET_TOKEN = 'auth.gettoken'
    GET_SESSION = 'auth.getsession'
    NOW_PLAYING = 'track.updateNowPlaying'
    SCROBBLE = 'track.scrobble'


def update_scrobble_state(db, scrobbler, new_status_update):
    sus = db.get_status_updates()
    sus.append(new_status_update)
    db.save_status_updates([new_status_update])
    scrobbles, leftovers = calculate_scrobbles(sus)
    failed_scrobbles = []
    for i in range(0, len(scrobbles), SCROBBLE_BATCH_SIZE):
        try:
            scrobbler.scrobble(scrobbles[i:i + SCROBBLE_BATCH_SIZE])
        except Exception:
            logging.exception('Scrobbling failed')
            # tracks need to be scrobbled in correct order. If the first
            # batch fails then other batches need to be left for later too.
            failed_scrobbles.extend(scrobbles[i:])
            break
    db.clear()
    db.save_status_updates(failed_scrobbles + leftovers)


def setup_logging(log_path):
    logging.basicConfig(filename=log_path or '/tmp/cmus_scrobbler.log',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        format='%(process)d %(asctime)s %(levelname)s %(name)s %(message)s',
                        level=logging.DEBUG)


def get_conf(conf_path):
    if not os.path.exists(conf_path):
        raise FileNotFoundError(f'{conf_path} does not exist.')
    conf = configparser.ConfigParser()
    with open(conf_path, 'r') as f:
        conf.read_file(f)
    return conf

DB_CONNECT_RETRY_SLEEP_SECS = 10
def db_connect(db_path, log_db=False):
    con = sqlite3.connect(db_path, timeout=DB_CONNECT_TIMEOUT)
    if log_db:
        con.set_trace_callback(logging.debug)
    # retry above command instead until it succeeds
    while True:
        try:
            con.execute('BEGIN IMMEDIATE')
            break
        except sqlite3.OperationalError:
            time.sleep(DB_CONNECT_RETRY_SLEEP_SECS)
    # when multiple status updates arrive one after another, then
    # if there is no blocking mechanism the order of status updates
    # will not be correct
    # Try it out without BEGIN immediate and hold pause-play
    return con


def get_scrobblers(conf):
    api_key = conf['global'].get('api_key')
    shared_secret = conf['global'].get('shared_secret')
    scrs = []
    for section in conf.sections():
        if section == 'global':
            continue
        scrs.append(
            Scrobbler(
                section, conf[section]['api_url'],
                conf[section].get('api_key', api_key),
                conf[section].get('shared_secret', shared_secret),
                conf[section].get('session_key'), conf[section].getboolean(
                    'now_playing', conf['global'].getboolean('now_playing')),
                conf[section].getboolean('format_xml', conf['global'].getboolean('format_xml'))))
    return scrs


def auth(conf):
    api_key = conf['global'].get('api_key')
    shared_secret = conf['global'].get('shared_secret')
    format_xml = conf['global'].getboolean('format_xml')
    for section in conf.sections():
        if section == 'global':
            continue
        if 'session_key' in conf[section]:
            print(f'Session key already active for {section}. Skipping...')
            continue
        try:
            conf[section].update(
                Scrobbler.auth(
                    conf[section]['auth_url'], conf[section]['api_url'],
                    conf[section].get('api_key', api_key),
                    conf[section].get('shared_secret', shared_secret),
                    conf[section].getboolean('format_xml', format_xml),
                )
            )
        except Exception:
            logging.exception('Authentication failed.')
    return conf


def main():
    args, rest = parser.parse_known_args()
    conf_path = args.ini
    conf = get_conf(conf_path)
    setup_logging(args.log_path or conf['global'].get('log_path'))
    if args.auth:
        with open(conf_path, 'w') as f:
            auth(conf).write(f)
        exit()
    status = parse_cmus_status_line(rest)
    scrobblers = get_scrobblers(conf)
    with db_connect(conf['global'].get('db_path', args.db_path),
                    log_db=conf['global'].get('log_db', args.log_db)) as con:
        logging.info(repr(status))
        for scr in scrobblers:
            update_scrobble_state(StatusDB(con, scr.name), scr, status)
    for scr in scrobblers:
        scr.send_now_playing(status)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception('Error happened')
