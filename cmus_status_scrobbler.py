#!/usr/bin/env python3
"""
cmus_status_scrobbler entry point and core logic.

Design: a ReaderT-like pattern where effectful operations are captured in
explicit env objects (HTTP/DB) built from fully-resolved config. Pure
functions (parsing, scrobble decision logic) sit at the top level, while
effectful programs (auth, update scrobble state) take envs first to keep
dependencies explicit and tests configurable.
"""
from __future__ import annotations

import argparse
import dataclasses
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from operator import attrgetter
from typing import (
    Annotated,
    Dict,
    List,
    NamedTuple,
    Optional,
    TYPE_CHECKING,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
if TYPE_CHECKING:
	from typing_extensions import TypeAlias, TypeGuard
STATUS_STOPPED = 'stopped'
STATUS_PLAYING = 'playing'
STATUS_PAUSED = 'paused'

SCROBBLER_GET_TOKEN = 'auth.gettoken'
SCROBBLER_GET_SESSION = 'auth.getsession'
SCROBBLER_NOW_PLAYING = 'track.updateNowPlaying'
SCROBBLER_SCROBBLE = 'track.scrobble'

KEYS_TO_REDACT = [b'api_key', b'sk', b'api_sig', b'token', b'session_key']

JSONValue: 'TypeAlias' = Union[str, int, float, bool, None, List['JSONValue'],
                               Dict[str, 'JSONValue']]


@dataclass(frozen=True)
class ArgDef:
	flags: tuple[str, ...]
	help: str
	action: Optional[str] = None
	required: bool = False


@dataclass(frozen=True)
class Args:
	auth: Annotated[
	    bool,
	    ArgDef(
	        flags=('--auth', ),
	        help="Add if you're missing session_key in .ini file.",
	        action='store_true',
	    ),
	]
	ini: Annotated[
	    str,
	    ArgDef(
	        flags=('--ini', ),
	        help='Path to .ini configuration file.',
	    ),
	]
	db_path: Annotated[
	    str,
	    ArgDef(
	        flags=('--db-path', ),
	        help='Path to sqlite3 database',
	    ),
	]
	log_path: Annotated[
	    Optional[str],
	    ArgDef(
	        flags=('--log-path', ),
	        help=
	        'If given logging will be saved to desired path (default: no logging)',
	    ),
	]
	log_db: Annotated[
	    bool,
	    ArgDef(
	        flags=('--log-db', ),
	        help='If given, SQL queries are logged',
	        action='store_true',
	    ),
	]
	cur_time: Annotated[
	    Optional[float],
	    ArgDef(
	        flags=('--cur-time', ),
	        help='Override current time for status update (unix timestamp).',
	    ),
	]


@dataclass(frozen=True)
class AppDefaults:
	config_path: str
	db_path: str
	db_connect_timeout: int
	db_connect_retry_attempts: int
	db_connect_retry_sleep_secs: int
	scrobble_batch_size: int
	http_user_agent: str
	http_default_timeout_secs: float
	http_scrobble_timeout_secs: float


@dataclass(frozen=True)
class GlobalConfig:
	api_key: str
	shared_secret: str
	db_path: str
	log_db: bool
	now_playing: bool
	format_xml: bool
	log_path: Optional[str]


@dataclass(frozen=True)
class ServiceConfig:
	name: str
	api_url: str
	auth_url: str
	api_key: str
	shared_secret: str
	session_key: Optional[str]
	now_playing: bool
	format_xml: bool


@dataclass(frozen=True)
class AppConfig:
	global_config: GlobalConfig
	services: list[ServiceConfig]


class Status(NamedTuple):
	status: str
	file: str
	artist: Optional[str]
	albumartist: Optional[str]
	album: Optional[str]
	discnumber: Optional[Union[str, int]]
	tracknumber: Optional[str]
	title: Optional[str]
	date: Optional[str]
	duration: Optional[Union[str, int]]
	musicbrainz_trackid: Optional[str]
	cur_time: float


def build_parser(args_type: type[Args],
                 defaults: AppDefaults) -> argparse.ArgumentParser:
	arg_defaults = args_type(
	    auth=False,
	    ini=os.path.expanduser(defaults.config_path),
	    db_path=os.path.expanduser(defaults.db_path),
	    log_path=None,
	    log_db=False,
	    cur_time=None,
	)
	parser = argparse.ArgumentParser(description='Scrobbling.')
	type_hints = get_type_hints(args_type, include_extras=True)
	for field in dataclasses.fields(args_type):
		hint = type_hints[field.name]
		arg_def: Optional[ArgDef] = None
		base_type = hint
		if get_origin(hint) is Annotated:
			args = get_args(hint)
			base_type = args[0]
			for meta in args[1:]:
				if isinstance(meta, ArgDef):
					arg_def = meta
		if arg_def is None:
			raise ValueError(f'Missing ArgDef for {field.name}')
		arg_type = base_type
		origin = get_origin(base_type)
		if origin is Union:
			args = get_args(base_type)
			if len(args)!=2 or type(None) not in args:
				raise ValueError(
				    f'Unsupported union type for {field.name}: {base_type}')
			arg_type = args[0] if args[1] is type(None) else args[1]
		if arg_def.action in {'store_true', 'store_false'}:
			if arg_type is not bool:
				raise ValueError(
				    f'Action {arg_def.action} requires bool for {field.name}')
		elif arg_type not in {str, int, float}:
			raise ValueError(f'Unsupported type for {field.name}: {arg_type}')
		default_value = getattr(arg_defaults, field.name)
		if arg_def.action is not None:
			parser.add_argument(
			    *arg_def.flags,
			    action=arg_def.action,
			    default=default_value,
			    required=arg_def.required,
			    help=arg_def.help,
			)
		else:
			parser.add_argument(
			    *arg_def.flags,
			    type=arg_type,
			    default=default_value,
			    required=arg_def.required,
			    help=arg_def.help,
			)
	return parser


@dataclass(frozen=True)
class DBEnv:
	create: Callable[[], None]
	get_status_updates: Callable[[], list[Status]]
	clear: Callable[[], None]
	save_status_updates: Callable[[list[Status]], None]


def make_db_env(
    *,
    con: sqlite3.Connection,
    table_name: str,
) -> DBEnv:

	def status_db_table() -> str:
		return f'status_updates_{table_name}'

	def create() -> None:
		con.execute(
		    f'CREATE TABLE IF NOT EXISTS {status_db_table()} (pickle BLOB)')

	def get_status_updates() -> list[Status]:
		cur = con.cursor()
		cur.execute(f'SELECT * FROM {status_db_table()}')
		status_updates: list[Status] = []
		for row in cur:
			loaded = pickle.loads(row[0])
			if not isinstance(loaded, Status):
				raise TypeError('Unexpected status update payload.')
			status_update = loaded
			if isinstance(status_update.cur_time, datetime.datetime):
				# FIXME remove in 3 years, assuming everyone is on latest
				status_update = status_update._replace(
				    cur_time=status_update.cur_time.timestamp())
			status_updates.append(status_update)
		return status_updates

	def clear() -> None:
		con.execute(f'DELETE FROM {status_db_table()}')

	def save_status_updates(status_updates: list[Status]) -> None:
		if not status_updates:
			return
		con.executemany(
		    f'INSERT INTO {status_db_table()}(pickle) values (?)',
		    [(pickle.dumps(su), ) for su in status_updates],
		)

	return DBEnv(
	    create=create,
	    get_status_updates=get_status_updates,
	    clear=clear,
	    save_status_updates=save_status_updates,
	)


@dataclass(frozen=True)
class HttpEnv:
	auth: Callable[[], dict[str, str]]
	scrobble: Callable[[list[Status]], None]
	send_now_playing: Callable[[Status], None]


def make_http_env(
    *,
    service_config: ServiceConfig,
    defaults: AppDefaults,
    session_key: Optional[str],
    logger: logging.LoggerAdapter[logging.Logger],
) -> HttpEnv:

	def is_json_value(value: JSONValue) -> TypeGuard[JSONValue]:
		if value is None or isinstance(value, (str, int, float, bool)):
			return True
		if isinstance(value, list):
			return all(is_json_value(item) for item in value)
		if isinstance(value, dict):
			return all(
			    isinstance(key, str) and is_json_value(item)
			    for key, item in value.items())
		return False

	def send_req(
	    *,
	    ignore_request_fail: bool,
	    method: str,
	    timeout_secs: Optional[float],
	    params: dict[str, Optional[str]],
	) -> JSONValue:

		def safe_utf8_encode(text: str) -> bytes:
			try:
				return text.encode('utf-8')
			except UnicodeEncodeError:
				return text.encode('utf-8', errors='ignore')

		def get_api_sig(encoded_params: dict[bytes, bytes],
		                secret: str) -> str:
			sig = hashlib.md5()
			for key in sorted(encoded_params):
				sig.update(key)
				sig.update(encoded_params[key])
			sig.update(secret.encode('utf-8'))
			return sig.hexdigest()

		def redact_dict(data: dict[bytes, bytes]) -> dict[bytes, bytes]:
			return {
			    key: (b'<REDACTED>' if key in KEYS_TO_REDACT else value)
			    for key, value in data.items()
			}

		merged: dict[str, str] = {
		    'api_key': service_config.api_key,
		    'method': method,
		}
		for key, value in params.items():
			if value is None:
				continue
			merged[key] = value

		encoded_params = {
		    safe_utf8_encode(key): safe_utf8_encode(value)
		    for key, value in merged.items()
		}
		if method!=SCROBBLER_GET_TOKEN:
			encoded_params[b'api_sig'] = safe_utf8_encode(
			    get_api_sig(encoded_params, service_config.shared_secret))
		if not service_config.format_xml:
			encoded_params[b'format'] = b'json'
		logger.info(redact_dict(encoded_params))
		api_req = ur.Request(service_config.api_url,
		                     headers={'User-Agent': defaults.http_user_agent})
		timeout = (defaults.http_default_timeout_secs
		           if timeout_secs is None else timeout_secs)
		try:
			with ur.urlopen(
			    api_req,
			    up.urlencode(encoded_params).encode(),
			    timeout=timeout,
			) as response:
				payload: str = response.read().decode('utf-8')
				logger.info(payload)
				if not payload:
					return None
				if not service_config.format_xml:
					loaded = json.loads(payload)
					if not is_json_value(loaded):
						raise ValueError('Unexpected JSON response.')
					return loaded
				return payload
		except Exception:
			if not ignore_request_fail:
				raise
			logger.exception('Ignoring error.')
		return None

	def auth() -> dict[str, str]:

		def require_text(value: JSONValue, label: str) -> str:
			if not isinstance(value, str):
				raise ValueError(f'Missing {label} in response.')
			return value

		def require_dict(value: JSONValue, label: str) -> dict[str, JSONValue]:
			if not isinstance(value, dict):
				raise ValueError(f'Missing {label} in response.')
			return value

		token_response = send_req(
		    ignore_request_fail=False,
		    method=SCROBBLER_GET_TOKEN,
		    timeout_secs=None,
		    params={},
		)
		if service_config.format_xml:
			token_payload = require_text(token_response, 'token')
			token = token_payload.split('<token>')[1].split('</token>')[0]
		else:
			token_dict = require_dict(token_response, 'token')
			token_value = token_dict.get('token')
			token = require_text(token_value, 'token')
		print(f'{service_config.auth_url}?'+
		      up.urlencode(dict(token=token, api_key=service_config.api_key)))
		input('Press <Enter> after visiting the link and allowing access...')
		session_response = send_req(
		    ignore_request_fail=False,
		    method=SCROBBLER_GET_SESSION,
		    timeout_secs=None,
		    params={'token': token},
		)
		if service_config.format_xml:
			session_payload = require_text(session_response, 'session')
			key = session_payload.split('<key>')[1].split('</key>')[0]
			name = session_payload.split('<name>')[1].split('</name>')[0]
			session: dict[str, JSONValue] = {'key': key, 'name': name}
		else:
			session_dict = require_dict(session_response, 'session')
			session_value = session_dict.get('session')
			session = require_dict(session_value, 'session')
		session_key = require_text(session.get('key'), 'session key')
		username = require_text(session.get('name'), 'username')
		return {'session_key': session_key, 'username': username}

	def scrobble(status_updates: list[Status]) -> None:

		def make_scrobble(i: int,
		                  status_update: Status) -> dict[str, Optional[str]]:
			return {
			    f'artist[{i}]':
			    status_update.artist,
			    f'track[{i}]':
			    status_update.title,
			    f'timestamp[{i}]':
			    str(int(status_update.cur_time)),
			    f'album[{i}]':
			    status_update.album,
			    f'trackNumber[{i}]':
			    status_update.tracknumber,
			    f'mbid[{i}]':
			    status_update.musicbrainz_trackid,
			    f'albumArtist[{i}]':
			    status_update.albumartist
			    if status_update.artist!=status_update.albumartist else None,
			    f'duration[{i}]':
			    None if status_update.duration is None else str(
			        status_update.duration),
			}

		if not status_updates:
			return
		logger.info('Scrobbling previous tracks')
		playing_updates = [
		    update for update in status_updates
		    if update.status==STATUS_PLAYING
		]
		if not playing_updates:
			return
		batch_scrobble_request: dict[str, Optional[str]] = {'sk': session_key}
		for i, status_update in enumerate(playing_updates):
			batch_scrobble_request.update(make_scrobble(i, status_update))
		send_req(
		    ignore_request_fail=False,
		    method=SCROBBLER_SCROBBLE,
		    timeout_secs=defaults.http_scrobble_timeout_secs,
		    params=batch_scrobble_request,
		)

	def send_now_playing(cur: Status) -> None:
		if not service_config.now_playing or cur.status!=STATUS_PLAYING:
			return
		logger.info('Sending now playing')
		params = dict(
		    artist=cur.artist,
		    track=cur.title,
		    album=cur.album,
		    trackNumber=cur.tracknumber,
		    duration=None if cur.duration is None else str(cur.duration),
		    albumArtist=cur.albumartist
		    if cur.artist!=cur.albumartist else None,
		    mbid=cur.musicbrainz_trackid,
		    sk=session_key,
		)
		send_req(
		    ignore_request_fail=True,
		    method=SCROBBLER_NOW_PLAYING,
		    timeout_secs=None,
		    params=params,
		)

	return HttpEnv(
	    auth=auth,
	    scrobble=scrobble,
	    send_now_playing=send_now_playing,
	)


@dataclass(frozen=True)
class ScrobblingEnv:
	http: HttpEnv
	db: DBEnv
	logger: logging.LoggerAdapter[logging.Logger]


def make_scrobbling_env(
    *,
    con: sqlite3.Connection,
    http_env: HttpEnv,
    table_name: str,
    logger: logging.LoggerAdapter[logging.Logger],
) -> ScrobblingEnv:
	return ScrobblingEnv(
	    http=http_env,
	    db=make_db_env(con=con, table_name=table_name),
	    logger=logger,
	)


def parse_cmus_status_line(
    parts: Sequence[str],
    logger: logging.LoggerAdapter[logging.Logger],
) -> Status:
	logger.info(parts)
	cur_time = datetime.datetime.now(datetime.timezone.utc).timestamp()
	musicbrainz_trackid = None
	discnumber: Optional[Union[str, int]] = 1
	tracknumber = None
	date = None
	album = None
	albumartist = None
	artist = None
	status = ''
	file = ''
	title = None
	duration: Optional[Union[str, int]] = None
	for key, value in zip(parts[::2], parts[1::2]):
		if key=='cur_time':
			try:
				cur_time = float(value)
			except ValueError:
				cur_time = datetime.datetime.now(
				    datetime.timezone.utc).timestamp()
		elif key=='musicbrainz_trackid':
			musicbrainz_trackid = value
		elif key=='discnumber':
			discnumber = value
		elif key=='tracknumber':
			tracknumber = value
		elif key=='date':
			date = value
		elif key=='album':
			album = value
		elif key=='albumartist':
			albumartist = value
		elif key=='artist':
			artist = value
		elif key=='status':
			status = value
		elif key=='file':
			file = value
		elif key=='title':
			title = value
		elif key=='duration':
			duration = value
	return Status(
	    status=status,
	    file=file,
	    artist=artist,
	    albumartist=albumartist,
	    album=album,
	    discnumber=discnumber,
	    tracknumber=tracknumber,
	    title=title,
	    date=date,
	    duration=duration,
	    musicbrainz_trackid=musicbrainz_trackid,
	    cur_time=cur_time,
	)


def calculate_scrobbles(
    status_updates: Sequence[Status],
    perc_thresh: float = 0.5,
    secs_thresh: int = 4*60,
) -> tuple[list[Status], list[Status]]:

	def has_played_enough(
	    start_ts: float,
	    end_ts: float,
	    duration_value: Optional[Union[str, int]],
	    played_before_pause: float = 0.0,
	) -> bool:
		if duration_value is None:
			return False
		duration = int(duration_value)
		total = end_ts-start_ts+played_before_pause
		return total/duration>=perc_thresh or total>=secs_thresh

	def equal_tracks(first: Status, second: Status) -> bool:
		return first.file==second.file

	def get_prefix_end_exclusive_idx(sus: Sequence[Status]) -> int:
		r_su = list(reversed(sus))
		for i, (cur, prv) in enumerate(zip(r_su, r_su[1:])):
			if (cur.status==STATUS_STOPPED or not equal_tracks(cur, prv)
			    or cur.status==prv.status or prv.status==STATUS_STOPPED):
				return len(r_su)-i
		return 0  # all statuses do not result in a scrobble

	scrobbles: list[Status] = []
	leftovers: list[Status] = []
	if not status_updates or len(status_updates)==1:
		return scrobbles, list(status_updates)

	# if status updates array has a suffix of playing/paused updates with same
	# track, then these tracks need to be immediatelly leftovers
	sus = sorted(status_updates, key=attrgetter('cur_time'))
	prefix_end = get_prefix_end_exclusive_idx(sus)
	lsus = sus[:prefix_end]
	# I am incapable of having simple thoughts. The pause is messing me up.
	# I use these two variables to scrobble paused tracks.
	played_before_pause = 0.0
	played_before_pause_status: Optional[Status] = None
	for cur, nxt, nxt2 in it.zip_longest(lsus, lsus[1:], lsus[2:]):
		if cur.status in [STATUS_STOPPED, STATUS_PAUSED]:
			continue
		if nxt is None:
			leftovers.append(cur)
			break
		played_enough = has_played_enough(
		    cur.cur_time,
		    nxt.cur_time,
		    cur.duration,
		    played_before_pause=played_before_pause
		    if played_before_pause_status
		    and equal_tracks(played_before_pause_status, cur) else 0.0,
		)

		if (not equal_tracks(cur, nxt)
		    or nxt.status in [STATUS_STOPPED, STATUS_PLAYING]):
			if played_enough:
				scrobbles.append(cur)
			played_before_pause = 0.0
			played_before_pause_status = None
			continue

		# files are equal and nxt status paused
		if nxt2 is None:
			leftovers.append(cur)
			leftovers.append(nxt)
			continue

		if equal_tracks(cur, nxt2) and nxt2.status==STATUS_PLAYING:
			# playing continued, keeping already played time for next
			played_before_pause += nxt.cur_time-cur.cur_time
			played_before_pause_status = cur if not played_before_pause_status else played_before_pause_status
			continue
		# playing did not continue, nxt2 file is not None and it's either a
		# different file or it's the same file but status is not playing
		# in this case we just check if played enough otherwise no scrobble
		if played_enough:
			scrobbles.append(played_before_pause_status or cur)
	return scrobbles, leftovers+sus[prefix_end:]


def run_update_scrobble_state(
    env: ScrobblingEnv,
    new_status_update: Status,
    scrobble_batch_size: int,
) -> None:
	env.db.create()
	status_updates = env.db.get_status_updates()
	status_updates.append(new_status_update)
	env.db.save_status_updates([new_status_update])
	scrobbles, leftovers = calculate_scrobbles(status_updates)
	failed_scrobbles: list[Status] = []
	for i in range(0, len(scrobbles), scrobble_batch_size):
		try:
			env.http.scrobble(scrobbles[i:i+scrobble_batch_size], )
		except Exception:
			env.logger.exception('Scrobbling failed')
			# tracks need to be scrobbled in correct order. If the first
			# batch fails then other batches need to be left for later too.
			failed_scrobbles.extend(scrobbles[i:])
			break
	env.db.clear()
	env.db.save_status_updates(failed_scrobbles+leftovers)


def setup_logging(log_path: Optional[str]) -> None:
	tmp_dir = '/tmp'
	for name in ['TMPDIR', 'TEMP', 'TEMPDIR', 'TMP']:
		value = os.environ.get(name)
		if value is not None:
			tmp_dir = value
			break
	logging.basicConfig(
	    filename=log_path or os.path.join(tmp_dir, 'cmus_scrobbler.log'),
	    datefmt='%Y-%m-%d %H:%M:%S',
	    format=
	    '%(process)d %(asctime)s %(levelname)s %(name)s %(service)s %(message)s',
	    level=logging.DEBUG,
	)


def get_conf(conf_path: str) -> configparser.ConfigParser:
	if not os.path.exists(conf_path):
		raise FileNotFoundError(f'{conf_path} does not exist.')
	conf = configparser.ConfigParser()
	with open(conf_path, 'r') as handle:
		conf.read_file(handle)
	return conf


def read_global_config(
    conf: configparser.ConfigParser,
    *,
    default_db_path: str,
    default_log_db: bool,
) -> GlobalConfig:
	api_key = conf['global'].get('api_key')
	shared_secret = conf['global'].get('shared_secret')
	if api_key is None or shared_secret is None:
		raise KeyError('Missing api_key/shared_secret in global config.')
	return GlobalConfig(
	    api_key=api_key,
	    shared_secret=shared_secret,
	    db_path=conf['global'].get('db_path', default_db_path),
	    log_db=conf['global'].getboolean('log_db', fallback=default_log_db),
	    now_playing=conf['global'].getboolean('now_playing', fallback=False),
	    format_xml=conf['global'].getboolean('format_xml', fallback=False),
	    log_path=conf['global'].get('log_path'),
	)


def read_service_config(
    conf: configparser.ConfigParser,
    global_config: GlobalConfig,
    section: str,
) -> ServiceConfig:
	api_url = conf[section].get('api_url')
	auth_url = conf[section].get('auth_url')
	if api_url is None or auth_url is None:
		raise KeyError(f'Missing api_url/auth_url for {section}.')
	api_key = conf[section].get('api_key', global_config.api_key)
	shared_secret = conf[section].get('shared_secret',
	                                  global_config.shared_secret)
	if api_key is None or shared_secret is None:
		raise KeyError(f'Missing credentials for {section}.')
	return ServiceConfig(
	    name=section,
	    api_url=api_url,
	    auth_url=auth_url,
	    api_key=api_key,
	    shared_secret=shared_secret,
	    session_key=conf[section].get('session_key'),
	    now_playing=conf[section].getboolean(
	        'now_playing',
	        global_config.now_playing,
	    ),
	    format_xml=conf[section].getboolean(
	        'format_xml',
	        global_config.format_xml,
	    ),
	)


def build_app_config(
    conf: configparser.ConfigParser,
    *,
    default_db_path: str,
    default_log_db: bool,
) -> AppConfig:
	global_config = read_global_config(
	    conf,
	    default_db_path=default_db_path,
	    default_log_db=default_log_db,
	)
	services: list[ServiceConfig] = []
	for section in conf.sections():
		if section=='global':
			continue
		services.append(read_service_config(conf, global_config, section))
	return AppConfig(global_config=global_config, services=services)


def db_connect(
    db_path: str,
    *,
    log_db: bool = False,
    connect_timeout: int,
    retry_attempts: int,
    retry_sleep_secs: int,
    logger: logging.LoggerAdapter[logging.Logger],
) -> sqlite3.Connection:
	con = sqlite3.connect(db_path, timeout=connect_timeout)
	if log_db:
		con.set_trace_callback(logger.debug)
	# BEGIN IMMEDIATE can return SQLITE_BUSY. After it succeeds, no other query
	# will return SQLITE_BUSY.
	# Retrying opens the possibility of incorrect event order but it should not
	# happen with normal human level usage.
	# Way around this would be to serialize all events in a single continuously
	# running process. That was not the point of this simple script.
	# 10 retries leaves enough room for scrobble ops to finish and release the
	# db lock.
	for _ in range(retry_attempts):
		try:
			con.execute('BEGIN IMMEDIATE')
			break
		except sqlite3.OperationalError:
			time.sleep(retry_sleep_secs)
	else:
		raise Exception('Could not connect to db.')
	# when multiple status updates arrive one after another, then
	# if there is no blocking mechanism the order of status updates
	# will not be correct
	# Try it out without BEGIN immediate and hold pause-play
	return con


def run_auth(
    http_env: HttpEnv,
    conf: configparser.ConfigParser,
    service_name: str,
    logger: logging.LoggerAdapter[logging.Logger],
) -> configparser.ConfigParser:
	try:
		conf[service_name].update(http_env.auth())
	except Exception:
		logger.exception('Authentication failed.')
	return conf


def main() -> None:
	defaults = AppDefaults(
	    config_path='~/.config/cmus/cmus_status_scrobbler.ini',
	    db_path='~/.config/cmus/cmus_status_scrobbler.sqlite3',
	    db_connect_timeout=300,
	    db_connect_retry_attempts=10,
	    db_connect_retry_sleep_secs=10,
	    scrobble_batch_size=50,
	    http_user_agent='Mozilla/5.0',
	    http_default_timeout_secs=10.0,
	    http_scrobble_timeout_secs=5.0,
	)
	parser = build_parser(Args, defaults)
	parsed_args, rest = parser.parse_known_args()
	args = Args(**vars(parsed_args))
	conf_path = args.ini
	conf = get_conf(conf_path)
	app_config = build_app_config(
	    conf,
	    default_db_path=args.db_path,
	    default_log_db=args.log_db,
	)
	setup_logging(args.log_path or app_config.global_config.log_path)
	logger = logging.getLogger('cmus_status_scrobbler')
	base_logger = logging.LoggerAdapter(logger, {'service': '-'})
	if args.auth:
		with open(conf_path, 'w') as handle:
			for service_config in app_config.services:
				service_logger = logging.LoggerAdapter(
				    logger,
				    {'service': service_config.name},
				)
				if service_config.session_key is not None:
					print(
					    f'Session key already active for {service_config.name}. Skipping...'
					)
					continue
				http_env = make_http_env(
				    service_config=service_config,
				    defaults=defaults,
				    session_key=None,
				    logger=service_logger,
				)
				run_auth(http_env, conf, service_config.name, service_logger)
			conf.write(handle)
		return
	status = parse_cmus_status_line(rest, base_logger)
	if args.cur_time is not None:
		status = status._replace(cur_time=args.cur_time)
	with db_connect(
	    app_config.global_config.db_path,
	    log_db=app_config.global_config.log_db,
	    connect_timeout=defaults.db_connect_timeout,
	    retry_attempts=defaults.db_connect_retry_attempts,
	    retry_sleep_secs=defaults.db_connect_retry_sleep_secs,
	    logger=base_logger,
	) as con:
		base_logger.info(repr(status))
		for service_config in app_config.services:
			service_logger = logging.LoggerAdapter(
			    logger,
			    {'service': service_config.name},
			)
			http_env = make_http_env(
			    service_config=service_config,
			    defaults=defaults,
			    session_key=service_config.session_key,
			    logger=service_logger,
			)
			env = make_scrobbling_env(
			    con=con,
			    http_env=http_env,
			    table_name=service_config.name,
			    logger=service_logger,
			)
			run_update_scrobble_state(env, status,
			                          defaults.scrobble_batch_size)
			http_env.send_now_playing(status)


if __name__=='__main__':
	main()
