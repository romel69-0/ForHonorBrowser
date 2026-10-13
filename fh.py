"""For Honor asset reader: forge containers, Oodle payloads, object files, meshes, textures -> GLB.
Reads the user's own install only. Format notes: docs/FORMATS.md"""
import os, sys, struct, ctypes, pickle, re, io, json, mmap, glob, shutil
import numpy as np
from PIL import Image

HERE = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))   # cache lives next to the exe
RES = getattr(sys, '_MEIPASS', HERE)   # bundled files when frozen
SETTINGS = os.path.join(HERE, 'settings.json')


def game_ok(p):
    """a For Honor folder has the forge archives and the Oodle dll"""
    return bool(p) and os.path.exists(os.path.join(p, 'oo2core_7_win64.dll')) and bool(glob.glob(os.path.join(p, '*.forge')))


def _find_game():
    """FH_GAME, else the folder saved by the folder picker, else the usual Steam / Ubisoft Connect locations"""
    if os.environ.get('FH_GAME'):
        return os.environ['FH_GAME']
    try:
        p = json.load(open(SETTINGS)).get('game')
        if game_ok(p):
            return p
    except (OSError, ValueError):
        pass
    tails = (r'SteamLibrary\steamapps\common\For Honor', r'Program Files (x86)\Steam\steamapps\common\For Honor',
             r'Steam\steamapps\common\For Honor', r'Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\For Honor',
             r'Games\For Honor')
    for d in 'CDEFGH':
        for t in tails:
            p = d + ':\\' + t
            if game_ok(p):
                return p
    return ''


def set_game(p):
    global GAME, _oodle
    GAME = os.path.normpath(p)
    _oodle = None
    json.dump({'game': GAME}, open(SETTINGS, 'w'))


GAME = _find_game()
CACHE = os.path.join(HERE, 'cache')
M32 = 0xffffffff
M64 = 0xffffffffffffffff
COMMON_SKELETON = 0xd086734112b8202c   # medf_common_skeleton object file
GLB_VERSION = 10    # bump when the GLB builder changes; old cached files are ignored
MESH_T = b'\x68\x95\x5d\x41'      # 0x415d9568 mesh record
MAT_T = b'\xc3\x17\xc8\x85'       # 0x85c817c3 material record
BODY = b'\x53\xae\xea\xf8'        # mesh body magic
PRINT = set(range(32, 127))


# ---------------------------------------------------------------- crypto
def _crc_table():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04c11db7) & M32 if c & 0x80000000 else (c << 1) & M32
        t.append(c)
    return t


_T = _crc_table()
KEYS = [tuple(_T[i * 8 + j] for j in p) for p in ((6, 5, 2, 1), (6, 4, 3, 2), (7, 6, 4, 2)) for i in range(32)]


def xxtea_dec(data, key, off=0):
    """XXTEA decrypt as the game does it: key tweaked by offset, tail bytes xored with offset bytes."""
    n = len(data) // 4
    k0 = ((key[0] | (key[1] << 32)) - off) & M64
    k1 = ((key[2] | (key[3] << 32)) + off) & M64
    k = (k0 & M32, k0 >> 32, k1 & M32, k1 >> 32)
    out = bytearray(data)
    if n >= 2:
        v = list(struct.unpack('<%dI' % n, data[:n * 4]))
        rounds = 6 + 52 // n
        s = (rounds * 0x9e3779b9) & M32
        y = v[0]
        while rounds:
            e = (s >> 2) & 3
            for p in range(n - 1, 0, -1):
                z = v[p - 1]
                mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((s ^ y) + (k[(p & 3) ^ e] ^ z))
                v[p] = (v[p] - mx) & M32
                y = v[p]
            z = v[n - 1]
            mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((s ^ y) + (k[e] ^ z))
            v[0] = (v[0] - mx) & M32
            y = v[0]
            s = (s - 0x9e3779b9) & M32
            rounds -= 1
        out[:n * 4] = struct.pack('<%dI' % n, *v)
    tail = len(data) & 3
    if tail:
        ob = struct.pack('<Q', off & M64)
        for i in range(tail):
            out[n * 4 + i] ^= ob[i]
    return bytes(out)


# ---------------------------------------------------------------- oodle (game's own dll)
_oodle = None
_OCAP = 1 << 27


def oodle_decode(blob, rawlen):
    global _oodle
    if _oodle is None:
        dll = ctypes.WinDLL(os.path.join(GAME, 'oo2core_7_win64.dll'))
        fn = dll.OodleLZ_Decompress
        fn.restype = ctypes.c_int64
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_int64, ctypes.c_int]
        _oodle = (fn, ctypes.create_string_buffer(_OCAP + 0x10000))
    fn, out = _oodle
    if rawlen > _OCAP:
        return None
    n = len(blob)
    src = ctypes.create_string_buffer(blob + b'\0' * 64, n + 64)
    if fn(src, n, out, rawlen, 0, 1, 0, None, 0, None, None, None, 0, 0) != rawlen:
        return None
    return ctypes.string_at(out, rawlen)


# ---------------------------------------------------------------- forge
def read_forge_index(path):
    with open(path, 'rb') as f:
        head = f.read(8)
        key = next((k for k in KEYS if xxtea_dec(head, k, 0) == b'scimitar'), None)
        if key is None:
            raise ValueError('unknown forge key: ' + path)

        def unit(pos, n):
            f.seek(pos)
            return xxtea_dec(f.read(n), key, pos)
        version, off_b = struct.unpack('<IQ', unit(9, 0x411)[:12])
        b = struct.unpack('<IIQIIIIIQ', unit(off_b, 0x2c))
        pos = b[8]
        out = []
        for _ in range(b[7]):
            s0, s4, s8, s20, s28, s2c, s10, s18 = struct.unpack('<IIQQIIQQ', unit(pos, 0x30))
            for i in range(s0):
                off, ida, idb, size = struct.unpack('<QIII', unit(s8 + 0x14 * i, 0x14))
                out.append((off, (idb << 32) | ida, size))
            pos = s20 if s20 != M64 else pos + 0x30
        return out


def _untrail(body):
    if len(body) < 8:
        return None
    raw, magic = struct.unpack('<II', body[-8:])
    if magic & 0xfffffff0 != 0xf3508ea0:
        return None
    codec = magic & 0xf
    if codec == 11:
        return oodle_decode(body[:-8], raw)
    if codec == 10:
        return body[:-8]
    return None   # ponytail: LZO/zstd codecs exist in the engine but no shipped data seen using them


def decode_payload(b, eid):
    d = _untrail(b)
    if d is not None:
        return d
    if len(b) >= 9:
        body = b[:-1]
        n = len(body)
        if b[-1] == 1:
            key = (eid & M32, eid >> 32, eid & M32, eid >> 32)
            m = min(n, 0x400)
            body = xxtea_dec(body[:m], key, n) + body[m:]
        d = _untrail(body)
        if d is not None:
            return d
    return b


# ---------------------------------------------------------------- helpers
def first_name(d):
    if len(d) >= 17 and d[10] in (0x26, 0x27):
        ln = d[15]
        nm = d[16:16 + ln]
        if len(nm) == ln and ln and all(c in PRINT for c in nm):
            return struct.unpack_from('<I', d, 11)[0], nm.decode('latin1')
    return 0, ''


def u64_at_every_offset(buf):
    a = np.frombuffer(buf, np.uint8)
    n = len(a) - 7
    if n <= 0:
        return np.zeros(0, np.uint64)
    v = np.zeros(n, np.uint64)
    for k in range(8):
        v |= a[k:k + n].astype(np.uint64) << np.uint64(8 * k)
    return v


def find_ids(buf, sorted_ids):
    """[(offset, id)] for every u64 in buf that is in sorted_ids (np.uint64, sorted)."""
    v = u64_at_every_offset(buf)
    if not len(v) or not len(sorted_ids):
        return []
    pos = np.searchsorted(sorted_ids, v)
    pos[pos >= len(sorted_ids)] = 0
    hit = np.nonzero(sorted_ids[pos] == v)[0]
    return [(int(o), int(v[o])) for o in hit]


def decode_mesh(a):
    """mesh buffer -> positions (game space, Z up), normals, uvs, [(material index, triangles)] for LOD0."""
    u = struct.unpack_from('<18I', a, 0)
    stride, sizes = u[2], u[3:8]
    H = 0x54
    N = sizes[0] // stride

    def stream(k):
        return np.frombuffer(a, np.uint8, N * 4, H + 4 * N * k).reshape(N, 4)
    if stride == 0x28:      # skinned: float positions
        P = np.column_stack([stream(k).view('<f4') for k in range(3)]).astype(np.float32)
        nrm, uv = stream(3), stream(6).view('<f2')
    elif stride == 0x18:    # static: int16 positions
        A = stream(0).view('<i2').astype(np.float32)
        B = stream(1).view('<i2').astype(np.float32)
        P = np.column_stack([A[:, 0], A[:, 1], B[:, 0]]) * B[:, 1:2] / 32768.0   # 4th component = signed scale
        nrm, uv = stream(2), stream(5).view('<f2')
    else:
        raise ValueError('unknown vertex stride 0x%x' % stride)
    nrm = (nrm[:, :3].astype(np.float32) - 127.5) / 127.5
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
    idx = np.frombuffer(a, '<u2', sizes[1] // 2, H + sizes[0]).astype(np.uint32)
    p = H + sum(sizes)
    subs = []
    recs = []
    tot = 0
    while p + 32 <= len(a) and tot < N:     # LOD0 = records until their vertex counts cover the buffer
        r = struct.unpack_from('<8I', a, p)
        vc, cs, cc, mat = r[2], r[3], r[4], r[5]
        if vc == 0 or vc > N or (cs + cc) * 192 > len(idx):
            break
        tri = idx[cs * 192:(cs + cc) * 192].reshape(-1, 3)
        tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])]
        if len(tri):
            subs.append((len(recs), tri))
        recs.append(mat)
        tot += vc
        p += 32
    return P, nrm, uv.astype(np.float32), subs


def _qmat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def skeleton_world(d, base=None):
    """bone records (type 0x95741049) -> {crc32(name): world matrix}. Local transform = second pos/quat block.
    With base (a reference pose), bones found there keep that pose and only bones chained to them are added."""
    recs = {}
    for m in re.finditer(rb'\x49\x10\x74\x95', d):
        p = m.start()
        if p < 8 or p + 90 > len(d):
            continue
        bid = struct.unpack_from('<Q', d, p - 8)[0]
        h = struct.unpack_from('<I', d, p + 4)[0]
        kind = d[p + 8]
        q = p + 9
        parent = None
        if kind == 2:
            parent = struct.unpack_from('<Q', d, q)[0]
            q += 8
        elif kind != 3:
            continue
        f = struct.unpack_from('<16f', d, q)
        L = np.eye(4)
        L[:3, :3] = _qmat(f[12:16])
        L[:3, 3] = f[8:11]
        recs[bid] = (h, parent, L)
    W = dict(base or {})

    def world(bid, depth=0):
        h, parent, L = recs[bid]
        if base is not None and h in base:
            return base[h]
        if parent in recs and depth < 200:
            P = world(parent, depth + 1)
            return None if P is None else P @ L
        return L if base is None else None
    for bid in recs:
        M = world(bid)
        if M is not None:
            W.setdefault(recs[bid][0], M)
    return W


def bind_correction(reg, ref):
    """Skinned vertices are stored in the space defined by the mesh's inverse bind matrices. Most outfits use the
    reference pose itself; some (older) ones use another space. Returns a 4x4 to reference space, or None."""
    if reg[80:84] != b'\xa1\xe7\xf0\x9e':
        return None
    n = struct.unpack_from('<I', reg, 68)[0]
    for k in range(min(n, 64)):
        e = 80 + 80 * k
        if reg[e:e + 4] != b'\xa1\xe7\xf0\x9e':
            break
        h = struct.unpack_from('<I', reg, e + 4)[0]
        if h in ref:
            C = ref[h] @ np.array(struct.unpack_from('<16f', reg, e + 8)).reshape(4, 4).T
            # ponytail: heroes differ from the reference skeleton by ~1 cm, so only correct clearly different spaces
            if np.linalg.norm(C[:3, 3]) > 0.1 or np.abs(C[:3, :3] - np.eye(3)).max() > 0.1:
                return C
            return None
    return None


def _dds(w, h, fourcc, data):
    hdr = bytearray(128)
    struct.pack_into('<4sIIIIII', hdr, 0, b'DDS ', 124, 0x81007, h, w, len(data), 0)
    struct.pack_into('<I', hdr, 28, 1)
    struct.pack_into('<II4s', hdr, 76, 32, 4, fourcc)
    struct.pack_into('<I', hdr, 108, 0x1000)
    return bytes(hdr) + data


# ---------------------------------------------------------------- database
class DB:
    def __init__(self, log=print):
        self.log = log
        self._check_cache()
        self._load_forges()
        self._load_gamedata()
        self._load_catalog()

    # ---- cache validity: wipe when the game's forge files change
    def _check_cache(self):
        files = sorted(f for f in glob.glob(os.path.join(GAME, '*.forge')) if '_sound' not in os.path.basename(f))
        if not files:
            raise SystemExit('For Honor not found at ' + GAME + '. Set the FH_GAME environment variable to the game folder.')
        sig = [[os.path.basename(f), os.path.getsize(f), int(os.path.getmtime(f))] for f in files]
        sp = os.path.join(CACHE, 'sig.json')
        if os.path.exists(sp) and json.load(open(sp)) != sig:
            self.log('game files changed, rebuilding cache')
            shutil.rmtree(CACHE)
        os.makedirs(os.path.join(CACHE, 'glb'), exist_ok=True)
        json.dump(sig, open(sp, 'w'))
        self.files = [s[0] for s in sig]

    def _cached(self, name, build):
        p = os.path.join(CACHE, name)
        if os.path.exists(p):
            return pickle.load(open(p, 'rb'))
        v = build()
        pickle.dump(v, open(p + '.tmp', 'wb'), protocol=4)
        os.replace(p + '.tmp', p)
        return v

    # ---- forges
    def _load_forges(self):
        def build():
            out = {}
            for k, n in enumerate(self.files):
                out[n] = read_forge_index(os.path.join(GAME, n))
                if k % 20 == 0:
                    self.log('indexing forges %d/%d' % (k + 1, len(self.files)))
            return out
        self.entries = self._cached('forges.pkl', build)

        def build_loc():
            h, fi, ei = [], [], []
            for k, n in enumerate(self.files):
                for i, (off, eid, size) in enumerate(self.entries[n]):
                    h.append(eid); fi.append(k); ei.append(i)
            h = np.array(h, np.uint64)
            o = np.argsort(h, kind='stable')
            return h[o], np.array(fi, np.uint16)[o], np.array(ei, np.uint32)[o]
        self.loc_h, self.loc_f, self.loc_e = self._cached('loc.pkl', build_loc)
        self._fh = {}

    def locate(self, h):
        p = int(np.searchsorted(self.loc_h, np.uint64(h)))
        if p < len(self.loc_h) and int(self.loc_h[p]) == h:
            return self.files[self.loc_f[p]], int(self.loc_e[p])
        return None

    def read_entry(self, name, i):
        f = self._fh.get(name)
        if f is None:
            f = self._fh[name] = open(os.path.join(GAME, name), 'rb')
        off, eid, size = self.entries[name][i]
        f.seek(off)
        return decode_payload(f.read(size), eid)

    def by_hash(self, h):
        loc = self.locate(h)
        return self.read_entry(*loc) if loc else None

    # ---- gamedata: all objects decoded into one file
    def _load_gamedata(self):
        rawp = os.path.join(CACHE, 'gamedata.raw')

        def build():
            ids, pos, ln, typ, names = [], [], [], [], []
            p = 0
            with open(rawp + '.tmp', 'wb') as raw:
                for n in [f for f in self.files if f.startswith('gamedatapc')]:
                    ents = self.entries[n]
                    for i, (off, eid, size) in enumerate(ents):
                        d = self.read_entry(n, i) or b''
                        t, nm = first_name(d)
                        ids.append(eid); pos.append(p); ln.append(len(d)); typ.append(t); names.append(nm)
                        raw.write(d); p += len(d)
                        if i % 50000 == 0:
                            self.log('decoding %s %d/%d' % (n, i, len(ents)))
            os.replace(rawp + '.tmp', rawp)
            return ids, pos, ln, typ, names
        ids, pos, ln, typ, names = self._cached('gamedata.pkl', build)
        self.gd_pos, self.gd_len, self.gd_typ, self.gd_name = pos, ln, typ, names
        self.ID = {e: i for i, e in enumerate(ids)}     # later forge wins
        self.NAME = {}
        for i, nm in enumerate(names):
            if nm:
                self.NAME[nm] = ids[i]
        self.gd_ids_sorted = np.array(sorted(self.ID), np.uint64)
        self.mat_ids = {ids[i] for i, t in enumerate(typ) if t == 0x85c817c3}
        self.tex_ids = np.array(sorted(ids[i] for i, t in enumerate(typ) if t == 0x59ce4d13), np.uint64)
        self._rawf = open(rawp, 'rb')
        self._raw = mmap.mmap(self._rawf.fileno(), 0, access=mmap.ACCESS_READ)

        def build_deps():
            T = {}
            for i, e in enumerate(ids):
                if e != 0x6d:
                    continue
                d = self._raw[pos[i]:pos[i] + ln[i]]
                n = struct.unpack_from('<I', d, 0)[0]
                p = 4
                for _ in range(n):
                    fid, c = struct.unpack_from('<QI', d, p); p += 12
                    subs = []
                    for _ in range(c):
                        sid, m = struct.unpack_from('<QI', d, p); p += 12
                        subs.append((sid, struct.unpack_from('<%dQ' % m, d, p))); p += 8 * m
                    T[fid] = subs
            return T
        self.deps = self._cached('deps.pkl', build_deps)

    def gd(self, eid):
        i = self.ID.get(eid)
        return None if i is None else self._raw[self.gd_pos[i]:self.gd_pos[i] + self.gd_len[i]]

    def content(self, fid):
        """object file for a gamedata id (8-byte entries are stubs pointing at the real file by hash)."""
        d = self.gd(fid)
        if d is not None and len(d) == 8:
            d = self.by_hash(struct.unpack('<Q', d)[0])
        return d

    # ---- catalog of everything that contains meshes
    def _load_catalog(self):
        inline = re.compile(rb'[\x26\x27]' + re.escape(MESH_T))
        ext = re.compile(rb'\x0f.{8}' + re.escape(MESH_T), re.S)

        def build():
            out = []
            ids = sorted(self.ID, key=lambda e: self.ID[e])
            for k, e in enumerate(ids):
                i = self.ID[e]
                if e in (0x6d, 0x46) or self.gd_len[i] > (64 << 20):
                    continue
                try:
                    d = self.content(e)
                except Exception:
                    d = None
                if not d:
                    continue
                n = len(inline.findall(d)) + len(ext.findall(d))
                if n:
                    out.append((e, first_name(d)[1] or self.gd_name[i] or 'object_%x' % e, n))
                if k % 20000 == 0:
                    self.log('building catalog %d/%d (%d models)' % (k, len(ids), len(out)))
            out.sort(key=lambda r: r[1].lower())
            return out
        self.catalog = self._cached('catalog.pkl', build)
        self.log('ready: %d models' % len(self.catalog))

    # ---- textures
    def tex_data(self, a, size):
        """[size bytes][1] plain, or [delta][u64 base hash][1]: data = delta + base (bytewise)."""
        if len(a) - size == 9:
            base = self.by_hash(struct.unpack_from('<Q', a, size)[0])
            if base is not None:
                x = np.frombuffer(a, np.uint8, size)
                return (x + np.frombuffer(self.tex_data(base, size), np.uint8, size)).tobytes()
        return a[:size]

    def tex_image(self, tid):
        return self._tex(self.gd(tid), [x for sid, l in self.deps.get(tid, []) for x in l])

    def _tex(self, o, hs):
        k = -1 if o is None else o.find(b'\x3d\x4b\x0c\xc3')
        if k < 0:
            return None
        size, w, h = struct.unpack_from('<III', o, k + 4)
        fmt = struct.unpack_from('<I', o, k + 41)[0]
        if not hs and 0 < size <= len(o) - k - 8:
            # small texture with its pixels stored right in the record: square mip chain, size tells the dimensions
            for blk, fourcc in ((8, b'DXT1'), (16, b'DXT5')):
                for e in range(2, 12):
                    n = 1 << e
                    if sum(max(1, (n >> i) // 4) ** 2 * blk for i in range(e + 1)) == size:
                        im = Image.open(io.BytesIO(_dds(n, n, fourcc, bytes(o[k + 8:k + 8 + (n // 4) ** 2 * blk]))))
                        im.load()
                        return im, 2 if blk == 8 else 4
            return None
        if not hs or not w or not h:
            return None
        a = self.by_hash(hs[0])
        if a is None:
            return None
        nb = max(1, w // 4) * max(1, h // 4)
        blk = 16 if size >= nb * 16 else 8
        data = self.tex_data(a, size)[:nb * blk]
        fourcc = b'DXT1' if blk == 8 else (b'ATI2' if fmt == 6 else b'DXT5')
        im = Image.open(io.BytesIO(_dds(w, h, fourcc, data)))
        im.load()
        return im, fmt

    def texture(self, base, ids=(), inline=(), fid=None):
        """by name (top mip first), else the largest of the texture objects listed by an inline texture set"""
        for nm in (base + '_CHRTM_1', base + '_CHRTM_0', base):
            tid = self.NAME.get(nm)
            if tid is None:
                continue
            try:
                r = self.tex_image(tid)
                if r:
                    return r
            except Exception as ex:
                self.log('texture %s failed: %r' % (nm, ex))
        best = None
        for tid in ids:
            try:
                r = self.tex_image(tid)
            except Exception:
                r = None
            if r and (best is None or r[0].size[0] > best[0].size[0]):
                best = r
        for sub, rec in inline:
            try:
                r = self._tex(rec, [x for sid, l in self.deps.get(fid, []) if sid == sub for x in l])
            except Exception:
                r = None
            if r and (best is None or r[0].size[0] > best[0].size[0]):
                best = r
        return best

    # ---- model
    def ref_pose(self):
        if not hasattr(self, '_ref'):
            d = self.by_hash(COMMON_SKELETON)
            # skeleton root is turned 90 degrees about Z relative to mesh space; undo it
            R = np.array([[0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)
            self._ref = {h: R @ M for h, M in skeleton_world(d).items()} if d else {}
        return self._ref

    def mesh_asset(self, ah, reg):
        """mesh buffer for a mesh body; like textures, some are stored as byte deltas against a base asset"""
        a = self.by_hash(ah)
        if a is None:
            return None
        return self.tex_data(a, struct.unpack_from('<I', reg, 36)[0]) if len(reg) >= 40 else a

    def model_parts(self, fid):
        d = self.content(fid)
        if not d:
            raise ValueError('no data for %x' % fid)
        mat_defs = []
        for m in re.finditer(rb'[\x26\x27]' + re.escape(MAT_T), d):
            if m.start() >= 8:
                mat_defs.append((m.start() - 8, struct.unpack_from('<Q', d, m.start() - 8)[0]))
        heads = [m for m in re.finditer(rb'[\x26\x27]' + re.escape(MESH_T) + rb'(.)', d, re.S)]
        bounds = sorted([m.start() for m in heads] + [o for o, _ in mat_defs] + [len(d)])
        regions = []
        for m in heads:
            ln = m.group(1)[0]
            nm = d[m.end():m.end() + ln]
            b = d.find(BODY, m.end() + ln, m.end() + ln + 16)
            if b < 0 or not all(c in PRINT for c in nm):
                continue
            end = next(x for x in bounds if x > m.start())
            regions.append((nm.decode('latin1'), d[b:end]))
        for m in re.finditer(rb'\x0f(.{8})' + re.escape(MESH_T) + rb'(.)', d, re.S):
            ln = m.group(2)[0]
            nm = d[m.end():m.end() + ln]
            if not all(c in PRINT for c in nm):
                continue
            try:
                x = self.by_hash(struct.unpack('<Q', m.group(1))[0])
            except Exception:
                x = None
            if x and x[:4] == BODY:
                regions.append((nm.decode('latin1'), x))
        local_mats = {i: o for o, i in mat_defs}
        known = np.array(sorted(set(local_mats) | self.mat_ids), np.uint64)
        parts, seen = [], set()
        for nm, reg in regions:
            if len(reg) < 64:
                continue
            ah = struct.unpack_from('<Q', reg, 28)[0]
            if ah in seen or nm.lower().endswith('_sim'):     # _sim = cloth simulation proxy, not rendered
                continue
            seen.add(ah)
            mats = []
            last = None
            for o, i in find_ids(reg[64:], known):
                if last is not None and o - last != 9:
                    break
                mats.append(i)
                last = o
            parts.append((nm, ah, mats, reg[:80 + 80 * 64]))
        return d, parts, local_mats, bounds

    def material_textures(self, d, mid, local_mats, bounds):
        """material -> {'DiffuseMap': base name, 'NormalMap': base name}; texture sets are referenced by id,
        either standalone gamedata objects or records inlined in the same file."""
        if mid in local_mats:
            o = local_mats[mid]
            reg = d[o:next(x for x in bounds if x > o + 8)]
            name = first_name(bytes(2) + reg[:300])[1]
        else:
            reg = self.content(mid) or b''
            name = self.gd_name[self.ID[mid]] if mid in self.ID else ''
        local = {}
        for m in re.finditer(rb'[\x26\x27].{4}(.)', d, re.S):
            ln = m.group(1)[0]
            nm = d[m.end():m.end() + ln]
            if m.start() >= 8 and ln >= 4 and len(nm) == ln and all(c in PRINT for c in nm):
                local[struct.unpack_from('<Q', d, m.start() - 8)[0]] = (nm.decode('latin1'), m.start())
        ids = np.array(sorted(local), np.uint64)
        offs = sorted(o for _, o in local.values()) + [len(d)]
        names = [local[i] for o, i in find_ids(reg, ids)]
        names += [(self.gd_name[self.ID[i]], None) for o, i in find_ids(reg, self.gd_ids_sorted) if i > 0xffff]
        found = {}
        for nm, off in names:
            for kind in ('DiffuseMap', 'NormalMap', 'ColorMaskMap'):
                k = nm.find(kind)
                if k > 0 and kind not in found and 'DEC_' not in nm:
                    found[kind] = nm[:k + len(kind)]
                    if off is not None:     # inline texture set: it lists the texture objects by id (their names can differ)
                        end = next(x for x in offs if x > off)
                        spec = d[off:min(end, off + 4096)]
                        found[kind + '_ids'] = [i for o, i in find_ids(spec, self.tex_ids)]
                        # textures embedded in this file: (sub id, record bytes)
                        found[kind + '_inline'] = [(i, d[local[i][1]:next(x for x in offs if x > local[i][1])])
                                                   for o, i in find_ids(spec, ids) if kind in local[i][0] and local[i][1] != off]
        for kind in ('DiffuseMap', 'NormalMap', 'ColorMaskMap'):     # fallback: textures named after the material (minus variant suffixes)
            base = name
            for _ in range(3):
                if kind in found or not base:
                    break
                for cand in (base, base[:-1] + 's' if base[-1:] in 'lr' else base):   # arml/armr share "arms"
                    if any(cand + '_' + kind + x in self.NAME for x in ('_CHRTM_0', '_CHRTM_1', '')):
                        found[kind] = cand + '_' + kind
                        break
                base = base.rsplit('_', 1)[0] if '_' in base else ''
        return name, found

    def build_glb(self, fid, maxtex=1024):
        d, parts, local_mats, bounds = self.model_parts(fid)
        bin_ = bytearray(); views = []; acc = []; meshes = []; nodes = []; images = []; textures = []; materials = []
        matidx = {}; notes = []

        def add_view(b, target=None):
            while len(bin_) % 4:
                bin_.append(0)
            v = dict(buffer=0, byteOffset=len(bin_), byteLength=len(b))
            if target:
                v['target'] = target
            bin_.extend(b); views.append(v)
            return len(views) - 1

        def add_acc(arr, ctype, typ, target=None, minmax=False):
            a = dict(bufferView=add_view(np.ascontiguousarray(arr).tobytes(), target), componentType=ctype, count=len(arr), type=typ)
            if minmax:
                a['min'] = arr.min(0).tolist(); a['max'] = arr.max(0).tolist()
            acc.append(a)
            return len(acc) - 1

        def add_image(im, label):
            if max(im.size) > maxtex:
                im = im.resize((maxtex * im.size[0] // max(im.size), maxtex * im.size[1] // max(im.size)), Image.BILINEAR)
            bio = io.BytesIO(); im.save(bio, 'JPEG', quality=88)
            images.append(dict(bufferView=add_view(bio.getvalue()), mimeType='image/jpeg', name=label))
            textures.append(dict(source=len(images) - 1))
            return len(textures) - 1

        def material(mid):
            if mid in matidx:
                return matidx[mid]
            name, found = ('default', {}) if mid is None else self.material_textures(d, mid, local_mats, bounds)
            if 'meatcap' in name.lower() or name.lower().startswith('actor_template'):   # limb stumps, sim proxies
                matidx[mid] = None
                return None
            m = dict(name=name or 'material', pbrMetallicRoughness=dict(metallicFactor=0.3, roughnessFactor=0.65), doubleSided=True)
            t = self.texture(found['DiffuseMap'], found.get('DiffuseMap_ids', ()), found.get('DiffuseMap_inline', ()), fid) if 'DiffuseMap' in found else None
            if t:
                m['pbrMetallicRoughness']['baseColorTexture'] = dict(index=add_image(t[0].convert('RGB'), name + '_diffuse'))
            else:
                m['pbrMetallicRoughness']['baseColorFactor'] = [0.6, 0.6, 0.62, 1]
            t = self.texture(found['NormalMap'], found.get('NormalMap_ids', ()), found.get('NormalMap_inline', ()), fid) if 'NormalMap' in found else None
            if t and t[1] == 6:                 # BC5: rebuild Z
                a = np.asarray(t[0].convert('RGB')).astype(np.float32) / 255 * 2 - 1
                x, y = a[..., 0], a[..., 1]
                z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
                m['normalTexture'] = dict(index=add_image(Image.fromarray(((np.dstack([x, y, z]) + 1) * 127.5).astype(np.uint8)), name + '_normal'))
            t = self.texture(found['ColorMaskMap'], found.get('ColorMaskMap_ids', ()), found.get('ColorMaskMap_inline', ()), fid) if 'ColorMaskMap' in found and 'baseColorTexture' in m['pbrMetallicRoughness'] else None
            if t:   # colour mask (R/G/B = tintable regions) rides along as a zero-strength emissive map for the viewer
                m['emissiveTexture'] = dict(index=add_image(t[0].convert('RGB'), name + '_colormask'))
                m['emissiveFactor'] = [0, 0, 0]
                m['extras'] = dict(colorMask=True)
            materials.append(m); matidx[mid] = len(materials) - 1
            return matidx[mid]

        pose = skeleton_world(d, self.ref_pose()) if self.ref_pose() else {}   # + this file's helper bones
        for nm, ah, mats, reg in parts[:400]:        # ponytail: cap for level-sized files; raise if whole maps are wanted
            try:
                a = self.mesh_asset(ah, reg)
                if not a or a[:4] != b'\x13\0\0\0':
                    continue
                P, nrm, uv, subs = decode_mesh(a)
                C = bind_correction(reg, pose)
                if C is not None:
                    P = (P @ C[:3, :3].T + C[:3, 3]).astype(np.float32)
                    nrm = (nrm @ C[:3, :3].T).astype(np.float32)
            except Exception as ex:
                notes.append('%s: %r' % (nm, ex))
                continue
            Pg = np.column_stack([P[:, 0], P[:, 2], -P[:, 1]]).astype(np.float32)        # Z-up -> glTF Y-up
            Ng = np.column_stack([nrm[:, 0], nrm[:, 2], -nrm[:, 1]]).astype(np.float32)
            pa = add_acc(Pg, 5126, 'VEC3', 34962, True); na = add_acc(Ng, 5126, 'VEC3', 34962); ua = add_acc(uv, 5126, 'VEC2', 34962)
            prims = []
            for mi, tri in subs:
                mid = mats[mi] if mi < len(mats) else (mats[-1] if mats else None)
                gm = material(mid)
                if gm is None:
                    continue
                prims.append(dict(attributes=dict(POSITION=pa, NORMAL=na, TEXCOORD_0=ua),
                                  indices=add_acc(tri.reshape(-1), 5125, 'SCALAR', 34963), material=gm))
            if prims:
                meshes.append(dict(name=nm, primitives=prims))
                nodes.append(dict(name=nm, mesh=len(meshes) - 1))
        if not meshes:
            raise ValueError('no decodable meshes' + (': ' + '; '.join(notes[:3]) if notes else ''))
        g = dict(asset=dict(version='2.0', generator='ForHonorBrowser'), scene=0, scenes=[dict(nodes=list(range(len(nodes))))],
                 nodes=nodes, meshes=meshes, materials=materials, accessors=acc, bufferViews=views, buffers=[dict(byteLength=len(bin_))])
        if textures:
            g['textures'] = textures; g['images'] = images
        js = json.dumps(g).encode()
        js += b' ' * (-len(js) % 4)
        while len(bin_) % 4:
            bin_.append(0)
        return (struct.pack('<III', 0x46546C67, 2, 28 + len(js) + len(bin_)) + struct.pack('<II', len(js), 0x4E4F534A) + js
                + struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_))


if __name__ == '__main__':
    # self-check: the first 8 bytes of every forge must decrypt to the magic, and one known mesh must decode
    import sys
    db = DB()
    want = sys.argv[1] if len(sys.argv) > 1 else 'warmonger_set05aaf_chest'
    hit = [r for r in db.catalog if want.lower() in r[1].lower()]
    assert hit, 'no catalog entry matching ' + want
    glb = db.build_glb(hit[0][0])
    assert glb[:4] == b'glTF' and len(glb) > 10000
    open(os.path.join(CACHE, 'selfcheck.glb'), 'wb').write(glb)
    print('ok:', hit[0][1], len(glb), 'bytes')
