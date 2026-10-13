import json, os, sys
from PIL import Image, ImageDraw
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'thumbs')
idx = json.load(open(os.path.join(D, 'index.json')))
keys = [k for k in idx if os.path.exists(os.path.join(D, k + '.png'))]
per = 10
for s in range(0, len(keys), per):
    chunk = keys[s:s + per]
    W = Image.new('RGB', (360 * 5, 550 * 2), (20, 20, 20)); dr = ImageDraw.Draw(W)
    for j, k in enumerate(chunk):
        x, y = 360 * (j % 5), 550 * (j // 5)
        W.paste(Image.open(os.path.join(D, k + '.png')).convert('RGB'), (x, y))
        dr.text((x + 6, y + 524), '%s  [%s %s]' % (k, idx[k]['outfit'], idx[k]['body']), fill=(255, 220, 120))
    W.save(os.path.join(D, 'sheet_%d.jpg' % (s // per)), quality=85)
print(len(keys))
