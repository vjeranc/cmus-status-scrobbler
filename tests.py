import unittest
from cmus_status_scrobbler import calculate_scrobbles, CmusStatus, StatusDB, update_scrobble_state
import datetime
from collections import namedtuple
import sqlite3
import os
import itertools as it


def secs(n):
    return datetime.timedelta(seconds=n)


SS = namedtuple('SS', 'cur_time duration file status')


class TestCalculateScrobbles(unittest.TestCase):
    def assertArrayEqual(self, ar1, ar2):
        for expected, actual in it.zip_longest(ar1, ar2):
            self.assertEqual(expected, actual)

    def test_simple_play_stop(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(4),
               duration=5,
               file='A',
               status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        # track when started playing
        self.assertEqual(CmusStatus.playing, scrobbles[0].status)
        self.assertEqual(ss[0], scrobbles[0])

    def test_repeat(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(4),
               duration=5,
               file='A',
               status=CmusStatus.playing)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        # track when started playing
        self.assertEqual(CmusStatus.playing, scrobbles[0].status)
        self.assertEqual(ss[0], scrobbles[0])
        self.assertEqual(ss[1], leftovers[0])

    def test_play_pause(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(4),
               duration=5,
               file='A',
               status=CmusStatus.paused)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual([], scrobbles)
        # track when started playing
        self.assertEqual(ss[0], leftovers[0])
        self.assertEqual(ss[1], leftovers[1])

    def test_play_pause_stopped(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(
                cur_time=d + secs(1),  # not enough time
                duration=5,
                file='A',
                status=CmusStatus.paused),
            SS(cur_time=d + secs(20),
               duration=5,
               file='A',
               status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual([], scrobbles)
        self.assertEqual([], leftovers)

    def test_play_pause_play_pause_dotdotdot_stopped(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=10, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(1),
               duration=10,
               file='A',
               status=CmusStatus.paused),
            SS(cur_time=d + secs(100),
               duration=10,
               file='A',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(101),
               duration=10,
               file='A',
               status=CmusStatus.paused),
            SS(cur_time=d + secs(200),
               duration=10,
               file='A',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(201),
               duration=10,
               file='A',
               status=CmusStatus.paused),
            SS(cur_time=d + secs(300),
               duration=10,
               file='A',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(301),
               duration=10,
               file='A',
               status=CmusStatus.paused),
            SS(cur_time=d + secs(400),
               duration=10,
               file='A',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(401),
               duration=10,
               file='A',
               status=CmusStatus.paused),
            SS(cur_time=d + secs(401),
               duration=10,
               file='A',
               status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss[:6])
        self.assertEqual([], scrobbles)
        self.assertEqual(6, len(leftovers))
        self.assertArrayEqual(ss[:6], leftovers)
        # trying out with last second missing from scrobblable playtime
        scrobbles, leftovers = calculate_scrobbles(ss[:-3] + [ss[-1]])
        self.assertEqual([], leftovers)
        self.assertEqual([], scrobbles)
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual([], leftovers)
        self.assertEqual(1, len(scrobbles))
        self.assertEqual(ss[0], scrobbles[0])

    def test_play_pause_stopped_enough_time_played(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(
                cur_time=d + secs(3),  # enough time played
                duration=5,
                file='A',
                status=CmusStatus.paused),
            SS(cur_time=d + secs(20),
               duration=5,
               file='A',
               status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual([], leftovers)
        self.assertEqual(ss[0], scrobbles[0])

    def test_normal_player_status(self):
        d = datetime.datetime.utcnow()
        ss = [
            SS(cur_time=d, duration=1, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(2),
               duration=1,
               file='B',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(3),
               duration=1,
               file='C',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(5),
               duration=1,
               file='D',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(7),
               duration=1,
               file='E',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(9),
               duration=1,
               file='F',
               status=CmusStatus.playing),
            SS(cur_time=d + secs(11),
               duration=1,
               file='F',
               status=CmusStatus.stopped),
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual(6, len(scrobbles))
        self.assertEqual([], leftovers)
        self.assertArrayEqual(ss[:-1], scrobbles)


DB_FILE = 'test.sqlite3'


class TestStatusDB(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(DB_FILE)

    def tearDown(self):
        self.con = None
        os.remove(DB_FILE)

    def build_db(self):
        return StatusDB(self.con, 'test_table_name')

    def assertArrayEqual(self, ar1, ar2):
        for expected, actual in it.zip_longest(ar1, ar2):
            self.assertEqual(expected, actual)

    def test_update(self):
        d = datetime.datetime.now()
        sus = [
            SS(cur_time=d, duration=5, file='A', status=CmusStatus.playing),
            SS(cur_time=d + secs(1),
               duration=5,
               file='A',
               status=CmusStatus.paused)
        ]
        new_su = SS(cur_time=d + secs(3),
                    duration=5,
                    file='A',
                    status=CmusStatus.playing)
        with self.con:
            db = self.build_db()
            db.save_status_updates(sus)
            self.assertArrayEqual(sus, db.get_status_updates())
            update_scrobble_state(db, new_su)
            n_sus = db.get_status_updates()
            self.assertArrayEqual(sus + [new_su], n_sus)

    def test_scrobble_update(self):
        # some tracks will scrobble and will no longer be stored
        d = datetime.datetime.now()
        sus = [
            SS(cur_time=d, duration=10, file='B', status=CmusStatus.playing)
        ]
        new_su = SS(cur_time=d + secs(10),
                    duration=5,
                    file='A',
                    status=CmusStatus.playing)
        with self.con:
            db = self.build_db()
            db.save_status_updates(sus)
            update_scrobble_state(db, new_su)
            n_sus = db.get_status_updates()
            self.assertArrayEqual([new_su], n_sus)


if __name__ == '__main__':
    unittest.main()
