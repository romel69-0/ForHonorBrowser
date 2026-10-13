"""build one complete outfit per (faction, hero) into thumbs/ and report mesh anomalies"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root (fh.py)
import fh, re, os, json, struct, collections, numpy as np
db = fh.DB(log=lambda *a: None)
OUT = os.path.join(fh.HERE, 'thumbs'); os.makedirs(OUT, exist_ok=True)
FACTIONS = {'knight', 'viking', 'samurai', 'chinese', 'outlanders'}
pat = re.compile(r'(?i)^(med[fm])_hero_(.+)_(chest|arms|helm)$')
G = collections.defaultdict(dict)
for e, name, n in db.catalog:
    m = pat.match(name.strip())
    if not m: continue
    t = m.group(2).lower().split('_')
    fac = t.pop(0) if t[0] in FACTIONS and len(t) > 1 else ''
    G[(fac, t[0])].setdefault(('_'.join(t[1:]), m.group(1).lower()), {})[m.group(3).lower()] = e
index = {}
for (fac, hero), outfits in sorted(G.items()):
    full = [(k, v) for k, v in sorted(outfits.items()) if len(v) == 3]
    if not full:
        print('NO COMPLETE', fac, hero, len(outfits)); continue
    k, parts = full[0]
    key = '%s_%s' % (fac or 'x', hero)
    files = []
    for part, fid in parts.items():
        try:
            d, ps, lm, b = db.model_parts(fid)
            for nm, ah, mats, reg in ps:
                a = db.mesh_asset(ah, reg)
                if not a or a[:4] != b'\x13\0\0\0':
                    print('  NOT MESH', key, part, nm, None if a is None else a[:4].hex()); continue
                try:
                    P, nrm, uv, subs = fh.decode_mesh(a)
                except Exception as ex:
                    print('  DECODE FAIL', key, part, nm, repr(ex)[:80]); continue
                C = fh.bind_correction(reg, fh.skeleton_world(d, db.ref_pose()))
                if C is not None: P = P @ C[:3, :3].T + C[:3, 3]
                c = (P.min(0) + P.max(0)) / 2
                if not subs: print('  NO SUBMESH', key, part, nm)
                if abs(c[0]) > 1.2 or abs(c[1]) > 1.2 or c[2] < -0.3 or c[2] > 2.6 or not np.isfinite(P).all():
                    print('  OUT OF PLACE', key, part, nm, np.round(c, 2).tolist(), 'pal', reg[80:84] == b'\xa1\xe7\xf0\x9e', 'corr', C is not None)
            g = db.build_glb(fid, maxtex=512)
            p = os.path.join(OUT, '%s_%s.glb' % (key, part)); open(p, 'wb').write(g); files.append(p)
        except Exception as ex:
            print('  BUILD FAIL', key, part, repr(ex)[:100])
    index[key] = dict(outfit=k[0], body=k[1], n_outfits=len(outfits), files=files)
json.dump(index, open(os.path.join(OUT, 'index.json'), 'w'), indent=1)
print(len(index), 'heroes')
