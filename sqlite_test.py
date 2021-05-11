import sqlite3
import pickle
import os

if os.path.exists('test.db'):
    con = sqlite3.connect('test.db')
    with con:
        cur = con.cursor()
        cur.execute("select * from status_updates")
        for row in cur:
            print('row', row)
            print(pickle.loads(row[0]))
else:
    try:
        con = sqlite3.connect('test.db')
        with con:
            con.execute("CREATE TABLE status_updates (pickle BLOB)")
            b = pickle.dumps(dict(a=5, b=3))
            bb = pickle.dumps(dict(a=3, b=1))
            con.executemany("INSERT INTO status_updates(pickle) values (?)",
                            [(b, ), (bb, )])
    except Exception as e:
        print(e)
