"""sample check: builds N random catalog models, reports failures. python _test.py [N] [seed]"""
import fh, json, struct, time, sys, random, collections
db = fh.DB(log=lambda *a: None)
n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
ok = 0; fails = collections.Counter(); notex = 0; t0 = time.time(); ex_fail = []
for fid, name, nm in random.sample(db.catalog, n):
    try:
        g = db.build_glb(fid)
        j = json.loads(g[20:20 + struct.unpack_from('<I', g, 12)[0]])
        ok += 1
        if not any('baseColorTexture' in m['pbrMetallicRoughness'] for m in j['materials']):
            notex += 1
    except Exception as ex:
        k = repr(ex)[:60]
        fails[k] += 1
        if len(ex_fail) < 6: ex_fail.append((name[:50], nm, k))
print('built %d/%d, %d without any texture, %.1fs' % (ok, n, notex, time.time() - t0))
for k, v in fails.most_common(6): print(v, k)
for e in ex_fail: print('  ', e)
