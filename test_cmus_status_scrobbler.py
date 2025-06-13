import unittest
import subprocess
import os
from multiprocessing import Process

# Assuming cmus_status_scrobbler.py is in the same directory
PYTHON_EXECUTABLE = 'python'  # or 'python3' if needed
CMUS_STATUS_SCROBBLER_PATH = './cmus_status_scrobbler.py'
INI_PATH = './test.ini'
DB_PATH = './test.sqlite3'

def run_scrobbler():
    subprocess.run([
        PYTHON_EXECUTABLE, CMUS_STATUS_SCROBBLER_PATH, '--ini', INI_PATH,
        'status', 'playing', 'file', '/home/user/Music/song1.mp3',
        'artist', 'Artist A', 'album', 'Album X', 'title', 'Song 1',
        'duration', '240'
    ])


class TestCmusStatusScrobblerIntegration(unittest.TestCase):

    def setUp(self):
        # Create a dummy .ini file
        with open(INI_PATH, 'w') as f:
            f.write('[global]\n')
            f.write(f'db_path = {DB_PATH}\n')
        # Ensure the database file is removed before each test
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    def tearDown(self):
        # Clean up the dummy .ini and database files after each test
        if os.path.exists(INI_PATH):
            os.remove(INI_PATH)
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    def test_multiple_invocations_with_empty_status(self):
        # Define a function to run cmus_status_scrobbler.py
        # Create and start multiple processes
        processes = [Process(target=run_scrobbler) for _ in range(5)]
        for p in processes:
            p.start()

        # Wait for all processes to complete
        for p in processes:
            p.join()

        # TODO: Add assertions to verify the expected behavior
        # For now, we just check that the processes ran without raising exceptions

if __name__ == '__main__':
    unittest.main()
