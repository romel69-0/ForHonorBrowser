import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root (fh.py)
import bpy, json, math, os, sys
from mathutils import Vector
D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'thumbs')
idx = json.load(open(os.path.join(D, 'index.json')))
only = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
for key, v in idx.items():
    if only and key not in only: continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for f in v['files']:
        bpy.ops.import_scene.gltf(filepath=f)
    sc = bpy.context.scene
    eng = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    sc.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in eng else 'BLENDER_EEVEE'
    sc.render.resolution_x = 360; sc.render.resolution_y = 520
    w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
    w.node_tree.nodes['Background'].inputs[0].default_value = (0.4, 0.42, 0.45, 1)
    for ang, en in ((40, 4.0), (-120, 2.0)):
        l = bpy.data.lights.new('s', 'SUN'); l.energy = en
        lo = bpy.data.objects.new('s', l); sc.collection.objects.link(lo); lo.rotation_euler = (math.radians(55), 0, math.radians(ang))
    cam = bpy.data.cameras.new('c'); cam.lens = 60
    co = bpy.data.objects.new('c', cam); sc.collection.objects.link(co); sc.camera = co
    ctr = Vector((0, 0, 0.95))
    co.location = ctr + Vector((1.6, -4.6, 0.3)); co.rotation_euler = (ctr - co.location).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(D, key + '.png'); bpy.ops.render.render(write_still=True)
