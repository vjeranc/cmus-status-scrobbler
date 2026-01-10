"""
This script is a test suite for the cmus_status_scrobbler.py script.
"""
from __future__ import annotations
import datetime
import itertools as it
import logging
import os
import sqlite3
import unittest
from collections.abc import Iterable

from cmus_status_scrobbler import (
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_STOPPED,
    HttpEnv,
    ScrobblingEnv,
    Status,
    calculate_scrobbles,
    make_db_env,
    run_update_scrobble_state,
)


def secs(n: int) -> datetime.timedelta:
	return datetime.timedelta(seconds=n)


def SS(*, cur_time: datetime.datetime, duration: int, file: str,
       status: str) -> Status:
	return make_status(cur_time=cur_time,
	                   duration=duration,
	                   file=file,
	                   status=status)


def utcnow() -> datetime.datetime:
	return datetime.datetime.now(datetime.timezone.utc)


def make_status(
    *,
    cur_time: datetime.datetime,
    duration: int,
    file: str,
    status: str,
) -> Status:
	return Status(
	    status=status,
	    file=file,
	    artist=None,
	    albumartist=None,
	    album=None,
	    discnumber=1,
	    tracknumber=None,
	    title=None,
	    date=None,
	    duration=duration,
	    musicbrainz_trackid=None,
	    cur_time=cur_time.timestamp(),
	)


class TestCalculateScrobbles(unittest.TestCase):

	def assertArrayEqual(self, ar1: Iterable[Status],
	                     ar2: Iterable[Status]) -> None:
		for expected, actual in it.zip_longest(ar1, ar2):
			self.assertEqual(expected, actual)

	def test_simple_play_stop(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=5, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(4), duration=5, file='A', status=STATUS_STOPPED)
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		# track when started playing
		self.assertEqual(STATUS_PLAYING, scrobbles[0].status)
		self.assertEqual(ss[0], scrobbles[0])

	def test_repeat(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=5, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(4), duration=5, file='A', status=STATUS_PLAYING)
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		# track when started playing
		self.assertEqual(STATUS_PLAYING, scrobbles[0].status)
		self.assertEqual(ss[0], scrobbles[0])
		self.assertEqual(ss[1], leftovers[0])

	def test_play_pause(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=5, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(4), duration=5, file='A', status=STATUS_PAUSED)
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual([], scrobbles)
		# track when started playing
		self.assertEqual(ss[0], leftovers[0])
		self.assertEqual(ss[1], leftovers[1])

	def test_play_pause_stopped(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=5, file='A', status=STATUS_PLAYING),
		    SS(
		        cur_time=d+secs(1),  # not enough time
		        duration=5,
		        file='A',
		        status=STATUS_PAUSED),
		    SS(cur_time=d+secs(20),
		       duration=5,
		       file='A',
		       status=STATUS_STOPPED)
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual([], scrobbles)
		self.assertEqual([], leftovers)

	def test_play_pause_play_pause_dotdotdot_stopped(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=10, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(1), duration=10, file='A',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(100),
		       duration=10,
		       file='A',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(101),
		       duration=10,
		       file='A',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(200),
		       duration=10,
		       file='A',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(201),
		       duration=10,
		       file='A',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(300),
		       duration=10,
		       file='A',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(301),
		       duration=10,
		       file='A',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(400),
		       duration=10,
		       file='A',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(401),
		       duration=10,
		       file='A',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(402),
		       duration=10,
		       file='A',
		       status=STATUS_STOPPED)
		]
		scrobbles, leftovers = calculate_scrobbles(ss[:6])
		self.assertEqual([], scrobbles)
		self.assertEqual(6, len(leftovers))
		self.assertArrayEqual(ss[:6], leftovers)
		# trying out with last second missing from scrobblable playtime
		scrobbles, leftovers = calculate_scrobbles(ss[:-3]+[ss[-1]])
		self.assertEqual([], leftovers)
		self.assertEqual([], scrobbles)
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual([], leftovers)
		self.assertEqual(1, len(scrobbles))
		self.assertEqual(ss[0], scrobbles[0])

	def test_play_pause_stopped_enough_time_played(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=5, file='A', status=STATUS_PLAYING),
		    SS(
		        cur_time=d+secs(3),  # enough time played
		        duration=5,
		        file='A',
		        status=STATUS_PAUSED),
		    SS(cur_time=d+secs(20),
		       duration=5,
		       file='A',
		       status=STATUS_STOPPED)
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual([], leftovers)
		self.assertEqual(ss[0], scrobbles[0])

	def test_normal_player_status(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=1, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(2), duration=1, file='B',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(3), duration=1, file='C',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(5), duration=1, file='D',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(7), duration=1, file='E',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(9), duration=1, file='F',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(11),
		       duration=1,
		       file='F',
		       status=STATUS_STOPPED),
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual(6, len(scrobbles))
		self.assertEqual([], leftovers)
		self.assertArrayEqual(ss[:-1], scrobbles)

	def test_pause_play_suffix_leftovers(self) -> None:
		d = utcnow()
		ss = [
		    SS(cur_time=d, duration=1, file='A', status=STATUS_PLAYING),
		    SS(cur_time=d+secs(2), duration=1, file='B',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(3), duration=1, file='C',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(5), duration=1, file='D',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(7), duration=1, file='E',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(9), duration=1, file='F',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(11),
		       duration=1,
		       file='F',
		       status=STATUS_STOPPED),
		    SS(cur_time=d+secs(13),
		       duration=10,
		       file='*',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(15),
		       duration=10,
		       file='*',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(17),
		       duration=10,
		       file='*',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(21),
		       duration=10,
		       file='*',
		       status=STATUS_PAUSED),
		    SS(cur_time=d+secs(23),
		       duration=10,
		       file='*',
		       status=STATUS_PLAYING),
		    SS(cur_time=d+secs(25),
		       duration=10,
		       file='*',
		       status=STATUS_PAUSED),
		]
		scrobbles, leftovers = calculate_scrobbles(ss)
		self.assertEqual(6, len(leftovers))
		self.assertEqual(6, len(scrobbles))
		self.assertArrayEqual(ss[:6], scrobbles)
		# stopped will not be in leftovers
		self.assertArrayEqual(ss[7:], leftovers)

	def test_scrobble_criteria(self) -> None:
		# Should stop when:
		#   1. stopped
		#   2. playing again
		#   3. different file
		d = utcnow()
		for stop in [
		    dict(file='B'),
		    dict(status=STATUS_PLAYING),
		    dict(status=STATUS_STOPPED),
		]:
			file_name = stop.get('file', 'A')
			status_value = stop.get('status', STATUS_PLAYING)
			ss = [
			    SS(cur_time=d, duration=10, file='A', status=STATUS_PLAYING),
			    SS(cur_time=d+secs(10),
			       duration=10,
			       file=file_name,
			       status=status_value),
			]
			scrobbles, leftovers = calculate_scrobbles(ss)
			self.assertEqual(1, len(scrobbles))
			self.assertEqual(ss[0], scrobbles[0])


DB_FILE = 'test.sqlite3'
DB_TABLE_NAME = 'test_table_name'


class TestStatusDB(unittest.TestCase):

	def setUp(self) -> None:
		self.con = sqlite3.connect(DB_FILE)
		self.db_env = make_db_env(con=self.con, table_name=DB_TABLE_NAME)
		self.db_env.create()

	def tearDown(self) -> None:
		self.con.close()
		os.remove(DB_FILE)

	def assertArrayEqual(self, ar1: Iterable[Status],
	                     ar2: Iterable[Status]) -> None:
		for expected, actual in it.zip_longest(ar1, ar2):
			self.assertEqual(expected, actual)

	def update_scrobble_state(self, new_su: Status) -> None:

		def noop_scrobble(_status_updates: list[Status]) -> None:
			return None

		def noop_send_now_playing(_status: Status) -> None:
			return None

		def noop_auth() -> dict[str, str]:
			raise AssertionError('auth should not be called in DB tests.')

		http_env = HttpEnv(
		    auth=noop_auth,
		    scrobble=noop_scrobble,
		    send_now_playing=noop_send_now_playing,
		)
		env = ScrobblingEnv(
		    http=http_env,
		    db=self.db_env,
		    logger=logging.getLogger('test'),
		)
		run_update_scrobble_state(env, new_su, 50)

	def test_update(self) -> None:
		d = datetime.datetime.now()
		sus = [
		    make_status(cur_time=d,
		                duration=5,
		                file='A',
		                status=STATUS_PLAYING),
		    make_status(cur_time=d+secs(1),
		                duration=5,
		                file='A',
		                status=STATUS_PAUSED),
		]
		new_su = make_status(cur_time=d+secs(3),
		                     duration=5,
		                     file='A',
		                     status=STATUS_PLAYING)
		with self.con:
			self.db_env.save_status_updates(sus)
			self.assertArrayEqual(sus, self.db_env.get_status_updates())
			self.update_scrobble_state(new_su)
			n_sus = self.db_env.get_status_updates()
			self.assertArrayEqual(sus+[new_su], n_sus)

	def test_scrobble_update(self) -> None:
		# some tracks will scrobble and will no longer be stored
		d = datetime.datetime.now()
		sus = [
		    make_status(cur_time=d,
		                duration=10,
		                file='B',
		                status=STATUS_PLAYING),
		]
		new_su = make_status(cur_time=d+secs(10),
		                     duration=5,
		                     file='A',
		                     status=STATUS_PLAYING)
		with self.con:
			self.db_env.save_status_updates(sus)
			self.update_scrobble_state(new_su)
			n_sus = self.db_env.get_status_updates()
			self.assertArrayEqual([new_su], n_sus)


if __name__=='__main__':
	unittest.main()
