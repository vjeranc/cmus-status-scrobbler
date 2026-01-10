"""
End-to-end tests for cmus_status_scrobbler.py using a stub HTTP server.
"""
from __future__ import annotations
import io
import json
import os
import pickle
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse as up
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from cmus_status_scrobbler import STATUS_PLAYING, Status
PYTHON_EXECUTABLE = sys.executable
CMUS_STATUS_SCROBBLER_PATH = './cmus_status_scrobbler.py'


@dataclass(frozen=True)
class RequestRecord:
	path: str
	params: dict[str, list[str]]


@dataclass(frozen=True)
class ServerState:
	xml: bool
	requests: list[RequestRecord]
	lock: threading.Lock
	make_response: Callable[[str], str]
	fail_methods: set[str]


class StubScrobblerServer:

	def __init__(self,
	             xml: bool = False,
	             fail_methods: set[str]|None = None) -> None:
		state = ServerState(
		    xml=xml,
		    requests=[],
		    lock=threading.Lock(),
		    make_response=self._make_response,
		    fail_methods=fail_methods or set(),
		)

		class StubRequestHandler(BaseHTTPRequestHandler):

			def do_POST(self) -> None:
				length = int(self.headers.get('Content-Length', '0'))
				body = self.rfile.read(length)
				params = up.parse_qs(body.decode('utf-8'),
				                     keep_blank_values=True)
				record = RequestRecord(path=self.path, params=params)
				with state.lock:
					state.requests.append(record)
				method = params.get('method', [''])[0]
				if method in state.fail_methods:
					self.send_response(500)
					self.end_headers()
					return
				response = state.make_response(method)
				self.send_response(200)
				content_type = 'text/xml' if state.xml else 'application/json'
				self.send_header('Content-Type', content_type)
				self.end_headers()
				self.wfile.write(response.encode('utf-8'))

			def log_message(self, format: str, *args: str) -> None:
				return

		self._state = state
		self._server = ThreadingHTTPServer(('127.0.0.1', 0),
		                                   StubRequestHandler)
		self.base_url = f'http://127.0.0.1:{self._server.server_address[1]}/'
		self._thread = threading.Thread(target=self._server.serve_forever)
		self._thread.daemon = True
		self._thread.start()

	def _make_response(self, method: str) -> str:
		if method=='auth.gettoken':
			if self._state.xml:
				return '<lfm><token>TEST_TOKEN</token></lfm>'
			return json.dumps({'token': 'TEST_TOKEN'})
		if method=='auth.getsession':
			if self._state.xml:
				return ('<lfm><session><key>TEST_SK</key>'
				        '<name>tester</name></session></lfm>')
			return json.dumps(
			    {'session': {
			        'key': 'TEST_SK',
			        'name': 'tester'
			    }})
		if self._state.xml:
			return '<lfm status="ok"></lfm>'
		return json.dumps({})

	def reset(self) -> None:
		with self._state.lock:
			self._state.requests.clear()

	def get_requests(self) -> list[RequestRecord]:
		with self._state.lock:
			return list(self._state.requests)

	def stop(self) -> None:
		self._server.shutdown()
		self._server.server_close()
		self._thread.join(timeout=2)


class E2ETestBase(unittest.TestCase):
	server: StubScrobblerServer

	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		self.ini_path = os.path.join(self.temp_dir.name, 'test.ini')
		self.db_path = os.path.join(self.temp_dir.name, 'test.sqlite3')

	def tearDown(self) -> None:
		self.temp_dir.cleanup()

	def write_ini(self,
	              base_url: str,
	              session_key: str|None = 'TEST_SK',
	              format_xml: bool = False,
	              now_playing: bool = False) -> None:
		with open(self.ini_path, 'w') as f:
			f.write('[global]\n')
			f.write(f'api_key = TEST_API_KEY\n')
			f.write(f'shared_secret = TEST_SHARED_SECRET\n')
			f.write(f'db_path = {self.db_path}\n')
			f.write(f'now_playing = {str(now_playing).lower()}\n')
			f.write(f'format_xml = {str(format_xml).lower()}\n')
			f.write('\n')
			f.write('[stub]\n')
			f.write(f'api_url = {base_url}\n')
			f.write(f'auth_url = {base_url}auth\n')
			if session_key is not None:
				f.write(f'session_key = {session_key}\n')

	def run_scrobbler(self, *status_args: str) -> None:
		cmd = [
		    PYTHON_EXECUTABLE,
		    CMUS_STATUS_SCROBBLER_PATH,
		    '--ini',
		    self.ini_path,
		    '--db-path',
		    self.db_path,
		]
		cmd.extend(status_args)
		subprocess.run(cmd, check=True)

	def run_status(
	    self,
	    cur_time: int,
	    status: str,
	    file_name: str,
	    duration: int,
	    title: str|None = None,
	) -> None:
		self.run_scrobbler(
		    '--cur-time',
		    str(cur_time),
		    'status',
		    status,
		    'file',
		    file_name,
		    'artist',
		    'Artist',
		    'title',
		    title or file_name,
		    'duration',
		    str(duration),
		)

	def read_db_updates(self) -> list[Status]:
		if not os.path.exists(self.db_path):
			return []
		con = sqlite3.connect(self.db_path)
		try:
			cur = con.cursor()
			cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
			tables = [r[0] for r in cur.fetchall()]
			if not tables:
				return []
			updates = []
			for table in tables:
				cur.execute(f'SELECT * FROM {table}')
				for row in cur.fetchall():
					updates.append(self._unpickle_status(row[0]))
			return updates
		finally:
			con.close()

	def _unpickle_status(self, payload: bytes) -> Status:

		class StatusUnpickler(pickle.Unpickler):

			def find_class(self, module: str, name: str) -> type:
				if module=='__main__' and name=='Status':
					from cmus_status_scrobbler import Status as StatusClass
					return StatusClass
				loaded = super().find_class(module, name)
				if not isinstance(loaded, type):
					raise TypeError('Unexpected pickle class.')
				return loaded

		loaded = StatusUnpickler(io.BytesIO(payload)).load()
		if not isinstance(loaded, Status):
			raise TypeError('Unexpected status payload.')
		return loaded

	def get_scrobble_tracks(self, requests: list[RequestRecord]) -> list[str]:
		tracks = []
		for req in requests:
			params = req.params
			if params.get('method', [''])[0]!='track.scrobble':
				continue
			indices = []
			for key in params.keys():
				if key.startswith('track[') and key.endswith(']'):
					idx = int(key[6:-1])
					indices.append(idx)
			for idx in sorted(indices):
				tracks.append(params.get(f'track[{idx}]', [''])[0])
		return tracks

	def get_requests_by_method(self, method: str) -> list[RequestRecord]:
		return [
		    req for req in self.server.get_requests()
		    if req.params.get('method', [''])[0]==method
		]

	def assert_param(self, params: dict[str, list[str]], key: str,
	                 expected: str) -> None:
		self.assertIn(key, params)
		self.assertEqual(expected, params[key][0])

	def assert_param_present(self, params: dict[str, list[str]],
	                         key: str) -> None:
		self.assertIn(key, params)
		self.assertTrue(params[key][0])

	def get_scrobble_items(
	        self, params: dict[str, list[str]]) -> list[tuple[str, str]]:
		indices = []
		for key in params.keys():
			if key.startswith('track[') and key.endswith(']'):
				indices.append(int(key[6:-1]))
		items = []
		for idx in sorted(indices):
			track = params.get(f'track[{idx}]', [''])[0]
			timestamp = params.get(f'timestamp[{idx}]', [''])[0]
			items.append((track, timestamp))
		return items


class TestAuthFlow(E2ETestBase):

	def test_auth_json(self) -> None:
		server = StubScrobblerServer(xml=False)
		try:
			self.write_ini(server.base_url, session_key=None, format_xml=False)
			cmd = [
			    PYTHON_EXECUTABLE,
			    CMUS_STATUS_SCROBBLER_PATH,
			    '--ini',
			    self.ini_path,
			    '--auth',
			]
			process = subprocess.run(
			    cmd,
			    input='\n',
			    text=True,
			    check=True,
			)
			self.assertEqual(0, process.returncode)
			with open(self.ini_path, 'r') as f:
				content = f.read()
			self.assertIn('session_key = TEST_SK', content)
			self.assertIn('username = tester', content)
			requests = server.get_requests()
			self.assertEqual(2, len(requests))
			token_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='auth.gettoken'
			]
			session_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='auth.getsession'
			]
			self.assertEqual(1, len(token_reqs))
			self.assertEqual(1, len(session_reqs))
			token_params = token_reqs[0].params
			session_params = session_reqs[0].params
			self.assert_param(token_params, 'api_key', 'TEST_API_KEY')
			self.assert_param(token_params, 'format', 'json')
			self.assert_param(session_params, 'api_key', 'TEST_API_KEY')
			self.assert_param(session_params, 'token', 'TEST_TOKEN')
			self.assert_param(session_params, 'format', 'json')
			self.assert_param_present(session_params, 'api_sig')
		finally:
			server.stop()

	def test_auth_xml(self) -> None:
		server = StubScrobblerServer(xml=True)
		try:
			self.write_ini(server.base_url, session_key=None, format_xml=True)
			cmd = [
			    PYTHON_EXECUTABLE,
			    CMUS_STATUS_SCROBBLER_PATH,
			    '--ini',
			    self.ini_path,
			    '--auth',
			]
			process = subprocess.run(
			    cmd,
			    input='\n',
			    text=True,
			    check=True,
			)
			self.assertEqual(0, process.returncode)
			with open(self.ini_path, 'r') as f:
				content = f.read()
			self.assertIn('session_key = TEST_SK', content)
			self.assertIn('username = tester', content)
			requests = server.get_requests()
			self.assertEqual(2, len(requests))
			token_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='auth.gettoken'
			]
			session_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='auth.getsession'
			]
			self.assertEqual(1, len(token_reqs))
			self.assertEqual(1, len(session_reqs))
			token_params = token_reqs[0].params
			session_params = session_reqs[0].params
			self.assert_param(token_params, 'api_key', 'TEST_API_KEY')
			self.assertNotIn('format', token_params)
			self.assert_param(session_params, 'api_key', 'TEST_API_KEY')
			self.assert_param(session_params, 'token', 'TEST_TOKEN')
			self.assertNotIn('format', session_params)
			self.assert_param_present(session_params, 'api_sig')
		finally:
			server.stop()


class TestScrobbleE2E(E2ETestBase):

	def setUp(self) -> None:
		super().setUp()
		self.server = StubScrobblerServer(xml=False)
		self.write_ini(self.server.base_url, session_key='TEST_SK')

	def tearDown(self) -> None:
		self.server.stop()
		super().tearDown()

	def test_simple_play_stop(self) -> None:
		base = 1000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+4, 'stopped', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assert_param(params, 'format', 'json')
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base))], self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual([], self.read_db_updates())

	def test_repeat(self) -> None:
		base = 2000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+4, 'playing', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assert_param(params, 'format', 'json')
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base))], self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		updates = self.read_db_updates()
		self.assertEqual(1, len(updates))

	def test_play_pause(self) -> None:
		base = 3000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+4, 'paused', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual([], tracks)
		self.assertEqual([], self.get_requests_by_method('track.scrobble'))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		updates = self.read_db_updates()
		self.assertEqual(2, len(updates))

	def test_play_pause_stopped(self) -> None:
		base = 4000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+1, 'paused', 'A', 5)
		self.run_status(base+20, 'stopped', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual([], tracks)
		self.assertEqual([], self.get_requests_by_method('track.scrobble'))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual([], self.read_db_updates())

	def test_play_pause_play_pause_dotdotdot_stopped(self) -> None:
		base = 5000
		self.run_status(base, 'playing', 'A', 10)
		self.run_status(base+1, 'paused', 'A', 10)
		self.run_status(base+100, 'playing', 'A', 10)
		self.run_status(base+101, 'paused', 'A', 10)
		self.run_status(base+200, 'playing', 'A', 10)
		self.run_status(base+201, 'paused', 'A', 10)
		self.run_status(base+300, 'playing', 'A', 10)
		self.run_status(base+301, 'paused', 'A', 10)
		self.run_status(base+400, 'playing', 'A', 10)
		self.run_status(base+401, 'paused', 'A', 10)
		self.run_status(base+402, 'stopped', 'A', 10)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assert_param(params, 'format', 'json')
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base))], self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual([], self.read_db_updates())

	def test_play_pause_stopped_enough_time_played(self) -> None:
		base = 6000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+3, 'paused', 'A', 5)
		self.run_status(base+20, 'stopped', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assert_param(params, 'format', 'json')
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base))], self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual([], self.read_db_updates())

	def test_normal_player_status(self) -> None:
		base = 7000
		self.run_status(base, 'playing', 'A', 1)
		self.run_status(base+2, 'playing', 'B', 1)
		self.run_status(base+3, 'playing', 'C', 1)
		self.run_status(base+5, 'playing', 'D', 1)
		self.run_status(base+7, 'playing', 'E', 1)
		self.run_status(base+9, 'playing', 'F', 1)
		self.run_status(base+11, 'stopped', 'F', 1)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A', 'B', 'C', 'D', 'E', 'F'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		items = []
		for req in requests:
			params = req.params
			self.assert_param(params, 'api_key', 'TEST_API_KEY')
			self.assert_param(params, 'sk', 'TEST_SK')
			self.assert_param(params, 'format', 'json')
			self.assert_param_present(params, 'api_sig')
			items.extend(self.get_scrobble_items(params))
		self.assertEqual([
		    ('A', str(base)),
		    ('B', str(base+2)),
		    ('C', str(base+3)),
		    ('D', str(base+5)),
		    ('E', str(base+7)),
		    ('F', str(base+9)),
		], items)
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual([], self.read_db_updates())

	def test_pause_play_suffix_leftovers(self) -> None:
		base = 8000
		self.run_status(base, 'playing', 'A', 1)
		self.run_status(base+2, 'playing', 'B', 1)
		self.run_status(base+3, 'playing', 'C', 1)
		self.run_status(base+5, 'playing', 'D', 1)
		self.run_status(base+7, 'playing', 'E', 1)
		self.run_status(base+9, 'playing', 'F', 1)
		self.run_status(base+11, 'stopped', 'F', 1)
		self.run_status(base+13, 'playing', '*', 10)
		self.run_status(base+15, 'paused', '*', 10)
		self.run_status(base+17, 'playing', '*', 10)
		self.run_status(base+21, 'paused', '*', 10)
		self.run_status(base+23, 'playing', '*', 10)
		self.run_status(base+25, 'paused', '*', 10)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A', 'B', 'C', 'D', 'E', 'F'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		items = []
		for req in requests:
			params = req.params
			self.assert_param(params, 'api_key', 'TEST_API_KEY')
			self.assert_param(params, 'sk', 'TEST_SK')
			self.assert_param(params, 'format', 'json')
			self.assert_param_present(params, 'api_sig')
			items.extend(self.get_scrobble_items(params))
		self.assertEqual([
		    ('A', str(base)),
		    ('B', str(base+2)),
		    ('C', str(base+3)),
		    ('D', str(base+5)),
		    ('E', str(base+7)),
		    ('F', str(base+9)),
		], items)
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		updates = self.read_db_updates()
		self.assertEqual(6, len(updates))

	def test_scrobble_criteria(self) -> None:
		base = 9000
		for idx, stop_status in enumerate(['playing', 'stopped']):
			self.server.reset()
			if os.path.exists(self.db_path):
				os.remove(self.db_path)
			offset = idx*100
			self.run_status(base+offset, 'playing', 'A', 10)
			if stop_status=='playing':
				self.run_status(base+offset+10, 'playing', 'A', 10)
			else:
				self.run_status(base+offset+10, 'stopped', 'A', 10)
			tracks = self.get_scrobble_tracks(self.server.get_requests())
			self.assertEqual(['A'], tracks)
			requests = self.get_requests_by_method('track.scrobble')
			self.assertEqual(1, len(requests))
			params = requests[0].params
			self.assert_param(params, 'api_key', 'TEST_API_KEY')
			self.assert_param(params, 'sk', 'TEST_SK')
			self.assert_param(params, 'format', 'json')
			self.assert_param_present(params, 'api_sig')
			self.assertEqual([('A', str(base+offset))],
			                 self.get_scrobble_items(params))
			self.assertEqual(
			    [], self.get_requests_by_method('track.updateNowPlaying'))
			if stop_status=='playing':
				self.assertEqual(1, len(self.read_db_updates()))
			else:
				self.assertEqual([], self.read_db_updates())
		self.server.reset()
		if os.path.exists(self.db_path):
			os.remove(self.db_path)
		self.run_status(base+200, 'playing', 'A', 10)
		self.run_status(base+210, 'playing', 'B', 10)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assert_param(params, 'format', 'json')
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base+200))],
		                 self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))
		self.assertEqual(1, len(self.read_db_updates()))

	def test_xml_scrobble(self) -> None:
		self.server.stop()
		self.server = StubScrobblerServer(xml=True)
		self.write_ini(self.server.base_url,
		               session_key='TEST_SK',
		               format_xml=True)
		base = 10000
		self.run_status(base, 'playing', 'A', 5)
		self.run_status(base+4, 'stopped', 'A', 5)
		tracks = self.get_scrobble_tracks(self.server.get_requests())
		self.assertEqual(['A'], tracks)
		requests = self.get_requests_by_method('track.scrobble')
		self.assertEqual(1, len(requests))
		params = requests[0].params
		self.assert_param(params, 'api_key', 'TEST_API_KEY')
		self.assert_param(params, 'sk', 'TEST_SK')
		self.assertNotIn('format', params)
		self.assert_param_present(params, 'api_sig')
		self.assertEqual([('A', str(base))], self.get_scrobble_items(params))
		self.assertEqual([],
		                 self.get_requests_by_method('track.updateNowPlaying'))


class TestNowPlayingFailure(E2ETestBase):

	def test_now_playing_failure_keeps_db(self) -> None:
		server = StubScrobblerServer(
		    xml=False,
		    fail_methods={'track.updateNowPlaying'},
		)
		try:
			self.write_ini(server.base_url,
			               session_key='TEST_SK',
			               format_xml=False,
			               now_playing=True)
			base = 11000
			self.run_status(base, 'playing', 'A', 5)
			requests = server.get_requests()
			update_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='track.updateNowPlaying'
			]
			scrobble_reqs = [
			    req for req in requests
			    if req.params.get('method', [''])[0]=='track.scrobble'
			]
			self.assertEqual(1, len(update_reqs))
			self.assertEqual([], scrobble_reqs)
			updates = self.read_db_updates()
			self.assertEqual(1, len(updates))
			self.assertEqual(STATUS_PLAYING, updates[0].status)
			self.assertEqual('A', updates[0].file)
		finally:
			server.stop()


class TestMultiServiceFailure(E2ETestBase):

	def test_middle_service_scrobble_failure_isolated(self) -> None:
		server1 = StubScrobblerServer(xml=False)
		server2 = StubScrobblerServer(
		    xml=False,
		    fail_methods={'track.scrobble'},
		)
		server3 = StubScrobblerServer(xml=False)
		servers = [server1, server2, server3]
		try:

			def write_ini_multi() -> None:
				with open(self.ini_path, 'w') as handle:
					handle.write('[global]\n')
					handle.write('api_key = TEST_API_KEY\n')
					handle.write('shared_secret = TEST_SHARED_SECRET\n')
					handle.write(f'db_path = {self.db_path}\n')
					handle.write('now_playing = true\n')
					handle.write('format_xml = false\n\n')
					for idx, server in enumerate(servers, start=1):
						name = f'svc{idx}'
						handle.write(f'[{name}]\n')
						handle.write(f'api_url = {server.base_url}\n')
						handle.write(f'auth_url = {server.base_url}auth\n')
						handle.write('session_key = TEST_SK\n')
						handle.write('now_playing = true\n\n')

			def get_requests_by_method(server: StubScrobblerServer,
			                           method: str) -> list[RequestRecord]:
				return [
				    req for req in server.get_requests()
				    if req.params.get('method', [''])[0]==method
				]

			def read_table_updates(table_name: str) -> list[Status]:
				if not os.path.exists(self.db_path):
					return []
				con = sqlite3.connect(self.db_path)
				try:
					cur = con.cursor()
					table = f'status_updates_{table_name}'
					cur.execute(
					    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
					    (table, ),
					)
					if cur.fetchone() is None:
						return []
					cur.execute(f'SELECT * FROM {table}')
					return [
					    self._unpickle_status(row[0])
					    for row in cur.fetchall()
					]
				finally:
					con.close()

			write_ini_multi()
			base = 12000
			self.run_status(base, 'playing', 'A', 5)
			self.run_status(base+4, 'stopped', 'A', 5)

			self.assertEqual(
			    1, len(get_requests_by_method(server1, 'track.scrobble')))
			self.assertEqual(
			    1, len(get_requests_by_method(server2, 'track.scrobble')))
			self.assertEqual(
			    1, len(get_requests_by_method(server3, 'track.scrobble')))
			self.assertEqual(
			    1,
			    len(get_requests_by_method(server1, 'track.updateNowPlaying')))
			self.assertEqual(
			    1,
			    len(get_requests_by_method(server2, 'track.updateNowPlaying')))
			self.assertEqual(
			    1,
			    len(get_requests_by_method(server3, 'track.updateNowPlaying')))

			self.assertEqual([], read_table_updates('svc1'))
			self.assertEqual(1, len(read_table_updates('svc2')))
			self.assertEqual([], read_table_updates('svc3'))
		finally:
			for server in servers:
				server.stop()


if __name__=='__main__':
	unittest.main()
