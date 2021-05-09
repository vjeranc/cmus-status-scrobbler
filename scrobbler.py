import configparser
import urllib.request as ur
import urllib.parse as up
import hashlib
import json

GET_TOKEN = 'auth.gettoken'
GET_SESSION = 'auth.getsession'


def url(x, **kwargs):
    return f'{api_url}?{x}'.format(**kwargs)


def get_api_sig(params, secret=None):
    m = hashlib.md5()
    for k in sorted(params):
        m.update(k.encode('utf-8'))
        m.update(params[k].encode('utf-8'))
    m.update(secret.encode('utf-8'))
    return m.hexdigest()


def authenticate(auth_url, api_url, api_key, shared_secret):
    # fetching token that is used to ask for access
    with ur.urlopen(
            api_url,
            up.urlencode(dict(method=GET_TOKEN, api_key=api_key,
                              format='json')).encode('utf-8')) as f:
        token = json.loads(f.read().decode('utf-8'))['token']
    print(f'{auth_url}?api_key={api_key}&token={token}')
    input('Press <Enter> after visiting the link and allowing access...')
    # fetching session with infinite lifetime that is used to scrobble
    params = dict(method=GET_SESSION, api_key=api_key, token=token)
    params['api_sig'] = get_api_sig(params, secret=shared_secret)
    params['format'] = 'json'
    with ur.urlopen(api_url, up.urlencode(params).encode('utf-8')) as f:
        session = json.loads(f.read().decode('utf-8'))['session']
    return dict(session_key=session['key'], username=session['name'])


if __name__ == "__main__":
    conf = configparser.ConfigParser()
    cfg_path = './cmus_status_scrobbler.ini'
    with open(cfg_path) as f:
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
        with open(cfg_path, 'w') as cfg_file:
            conf.write(cfg_file)
