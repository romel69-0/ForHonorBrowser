"""For Honor Browser: local server + app window.  python server.py  (or ForHonorBrowser.exe)"""
import os, sys, re, json, threading, subprocess, webbrowser, socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8765
URL = 'http://127.0.0.1:%d' % PORT
FACTIONS = {'knight', 'viking', 'samurai', 'chinese', 'outlanders'}
# internal (faction, codename) -> in-game hero. Identified by rendering each one (thumbs/sheet_*.jpg);
# male/female codenames of the same hero are merged. Unlisted codenames keep their internal name.
DISPLAY = {
    ('knight', 'apollo'): 'Warden', ('knight', 'joffrey'): 'Conqueror', ('knight', 'tank'): 'Conqueror',
    ('knight', 'assassin'): 'Peacekeeper', ('knight', 'hybrid'): 'Lawbringer', ('knight', 'dlc1centurion'): 'Centurion',
    ('knight', 'dlc2gladiator'): 'Gladiator', ('knight', 'h023darkwarden'): 'Black Prior', ('knight', 'h027warmonger'): 'Warmonger',
    ('knight', 'apollyon'): 'Apollyon (story)', ('', 'h028gryphon'): 'Gryphon', ('knight', 'h028gryphon'): 'Gryphon',
    ('viking', 'arnold'): 'Raider', ('viking', 'champion'): 'Raider', ('viking', 'olaf'): 'Warlord', ('viking', 'olaf '): 'Warlord',
    ('viking', 'tank'): 'Warlord (Gudmundr, story)', ('viking', 'loki'): 'Berserker', ('viking', 'assassin'): 'Berserker (assassin sets)',
    ('viking', 'helga'): 'Valkyrie', ('viking', 'dlc2highlander'): 'Highlander', ('viking', 'dlc3huntress'): 'Shaman',
    ('viking', 'dlc3huntres'): 'Shaman', ('viking', 'h025zealot'): 'Jormungandr', ('', 'h034varangian'): 'Varangian Guard',
    ('viking', 'h034varangian'): 'Varangian Guard',
    ('samurai', 'shindo'): 'Kensei', ('samurai', 'champion'): 'Kensei', ('samurai', 'watanabe'): 'Orochi',
    ('samurai', 'assassin'): 'Orochi (special sets)', ('samurai', 'tank'): 'Shugoki', ('samurai', 'hybrid'): 'Nobushi',
    ('samurai', 'dlc1ninja'): 'Shinobi', ('samurai', 'dlc3ronin'): 'Aramusha', ('samurai', 'h024manslayer'): 'Hitokiri',
    ('', 'h029faceless'): 'Kyoshin', ('samurai', 'h029faceless'): 'Kyoshin', ('samurai', 'h035benkei'): 'Sohei',
    ('samurai', 'h039brawler'): 'Arakure',
    ('chinese', 'dlc4general'): 'Tiandi', ('chinese', 'dlc4oldmaster'): 'Jiang Jun', ('chinese', 'dlc4shaolin'): 'Shaolin',
    ('chinese', 'dlc5bodyguard'): 'Nuxia', ('chinese', 'h026betrayer'): 'Zhanhu',
    ('outlanders', 'h030piratequeen'): 'Pirate', ('outlanders', 'h031medjay'): 'Medjay', ('outlanders', 'h032gazelle'): 'Afeera',
    ('outlanders', 'h033aztec'): 'Ocelotl', ('outlanders', 'h036mongol'): 'Khatun', ('outlanders', 'h037fencer'): 'Virtuosa',
}
FACTION_OF = {'Gryphon': 'knight', 'Varangian Guard': 'viking', 'Kyoshin': 'samurai'}
OUTFIT = re.compile(r'(?i)^(med[fm])_hero_(.+)_(chest|arms|helm)$')


def open_window():
    """app-style window via Edge when present, else the default browser"""
    for p in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
        if os.path.exists(p):
            subprocess.Popen([p, '--app=' + URL, '--window-size=1400,900'])
            return
    webbrowser.open(URL)


def already_running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


if already_running():
    open_window()
    sys.exit(0)

import fh


def pick_folder(initial=''):
    """native folder dialog; returns a valid For Honor folder or None when cancelled"""
    import tkinter
    from tkinter import filedialog, messagebox
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        while True:
            p = filedialog.askdirectory(parent=root, initialdir=initial or None,
                                        title='Select your For Honor game folder (the one containing forhonor.exe)')
            if not p:
                return None
            if fh.game_ok(p):
                return p
            messagebox.showerror('For Honor Browser', 'That folder has no For Honor .forge files and oo2core_7_win64.dll.\n'
                                 'Pick the folder that contains forhonor.exe.', parent=root)
    finally:
        root.destroy()


if not fh.game_ok(fh.GAME):
    p = pick_folder()
    if not p:
        sys.exit('No game folder selected.')
    fh.set_game(p)
print('For Honor Browser - reading', fh.GAME)
db = fh.DB()
lock = threading.Lock()      # Oodle output buffer and file handles are shared


def search(q, limit=400):
    words = q.lower().split()
    out = []
    for e, name, n in db.catalog:
        low = name.lower()
        if all(w in low for w in words):
            out.append({'id': '%x' % e, 'name': name.strip(), 'meshes': n})
            if len(out) >= limit:
                break
    return out


def build_heroes():
    """group body files named med[fm]_hero_[faction_]<hero>_<set>_<chest|arms|helm> into hero -> outfit -> parts"""
    heroes = {}
    for e, name, n in db.catalog:
        m = OUTFIT.match(name.strip())
        if not m:
            continue
        toks = m.group(2).lower().split('_')
        faction = toks.pop(0) if toks[0] in FACTIONS and len(toks) > 1 else ''
        code, outfit = toks[0], '_'.join(toks[1:]) or 'default'
        hero = DISPLAY.get((faction, code), code)
        faction = FACTION_OF.get(hero, faction)
        h = heroes.setdefault((faction, hero), {'hero': hero, 'faction': faction, 'outfits': {}, 'codes': set()})
        h['codes'].add(code)
        o = h['outfits'].setdefault((outfit, m.group(1).lower()), {})
        o[m.group(3).lower()] = '%x' % e
    out = []
    for h in sorted(heroes.values(), key=lambda h: (h['faction'], h['hero'])):
        outfits = [{'name': k[0], 'body': k[1], 'parts': v} for k, v in sorted(h['outfits'].items())]
        out.append({'hero': h['hero'], 'faction': h['faction'], 'codes': sorted(h['codes']), 'outfits': outfits})
    return out


HEROES = build_heroes()


def model(fid, full):
    path = os.path.join(fh.CACHE, 'glb', '%x_v%d%s.glb' % (fid, fh.GLB_VERSION, '_full' if full else ''))
    if os.path.exists(path):
        return open(path, 'rb').read()
    with lock:
        glb = db.build_glb(fid, maxtex=4096 if full else 1024)
    with open(path, 'wb') as f:
        f.write(glb)
    return glb


def restart():
    srv.server_close()
    args = [sys.executable] + ([] if getattr(sys, 'frozen', False) else [os.path.abspath(__file__)]) + ['--no-browser']
    subprocess.Popen(args, cwd=fh.HERE)
    os._exit(0)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == '/':
                self.send(200, open(os.path.join(fh.RES, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')
            elif u.path == '/api/search':
                r = {'total': len(db.catalog), 'items': search(q.get('q', [''])[0])}
                self.send(200, json.dumps(r).encode(), 'application/json')
            elif u.path == '/api/game':
                self.send(200, json.dumps({'game': fh.GAME}).encode(), 'application/json')
            elif u.path == '/api/choose':       # folder picker; a new folder needs a fresh index, so restart
                p = pick_folder(fh.GAME)
                changed = bool(p) and os.path.normcase(os.path.normpath(p)) != os.path.normcase(os.path.normpath(fh.GAME))
                if changed:
                    fh.set_game(p)
                self.send(200, json.dumps({'game': fh.GAME, 'restart': changed}).encode(), 'application/json')
                if changed:
                    threading.Thread(target=restart, daemon=True).start()
            elif u.path == '/api/heroes':
                self.send(200, json.dumps(HEROES).encode(), 'application/json')
            elif u.path == '/api/model':
                fid = int(q['id'][0], 16)
                if fid not in db.ID:
                    return self.send(404, b'unknown id', 'text/plain')
                self.send(200, model(fid, q.get('full', ['0'])[0] == '1'), 'model/gltf-binary')
            else:
                self.send(404, b'not found', 'text/plain')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            self.send(500, repr(ex).encode(), 'text/plain')


if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print('running at', URL, '- close this window to quit')
    if '--no-browser' not in sys.argv:
        open_window()
    srv.serve_forever()
