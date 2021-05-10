import unittest
from cmus_status_scrobbler import calculate_scrobbles, CmusStatus
import datetime
from collections import namedtuple


def secs(n):
    return datetime.timedelta(seconds=n)


SmplSts = namedtuple('SmplSts', 'cur_time duration file status')


class TestCalculateScrobbles(unittest.TestCase):
    def test_simple_play_stop(self):
        d = datetime.datetime.utcnow()
        ss = [
            SmplSts(cur_time=d,
                    duration=5,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(4),
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
            SmplSts(cur_time=d,
                    duration=5,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(4),
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
            SmplSts(cur_time=d,
                    duration=5,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(4),
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
            SmplSts(cur_time=d,
                    duration=5,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(
                cur_time=d + secs(1),  # not enough time
                duration=5,
                file='A',
                status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(20),
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
            SmplSts(cur_time=d,
                    duration=10,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(1),
                    duration=10,
                    file='A',
                    status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(100),
                    duration=10,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(101),
                    duration=10,
                    file='A',
                    status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(200),
                    duration=10,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(201),
                    duration=10,
                    file='A',
                    status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(300),
                    duration=10,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(301),
                    duration=10,
                    file='A',
                    status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(400),
                    duration=10,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(cur_time=d + secs(401),
                    duration=10,
                    file='A',
                    status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(401),
                    duration=10,
                    file='A',
                    status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss[:6])
        self.assertEqual([], scrobbles)
        self.assertEqual(5, len(leftovers))
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
            SmplSts(cur_time=d,
                    duration=5,
                    file='A',
                    status=CmusStatus.playing),
            SmplSts(
                cur_time=d + secs(3),  # enough time played
                duration=5,
                file='A',
                status=CmusStatus.paused),
            SmplSts(cur_time=d + secs(20),
                    duration=5,
                    file='A',
                    status=CmusStatus.stopped)
        ]
        scrobbles, leftovers = calculate_scrobbles(ss)
        self.assertEqual([], leftovers)
        self.assertEqual(ss[0], scrobbles[0])


if __name__ == '__main__':
    unittest.main()
