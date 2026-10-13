# For Honor Browser

Browse, customize and export the 3D models in your own copy of **For Honor** (PC).

It reads the game's `.forge` archives directly, shows any of ~48,000 models in a 3D view, lets you mix armour parts and recolour them, and exports the result as glTF (`.glb`) for Blender or any other 3D tool.

> **Unofficial fan tool.** Not affiliated with or endorsed by Ubisoft. It contains no game files: you need your own installed copy of For Honor. Exported models remain Ubisoft's property; keep them for personal use and do not redistribute them.

## Features

- **Hero browser:** 46 hero groups and about 1,770 outfits, listed under their in-game names (Warden, Raider, Orochi, …). One click loads chest, arms and helm together.
- **Mix and match:** pick helm, chest and arms separately from any outfit of the same hero.
- **Colours and materials:** three tint colours driven by each armour's own colour-mask texture, plus metal presets (Iron, Black Iron, Tar, Silver, White Iron, Gold, Bronze, Copper, custom).
- **Everything else:** search all models by internal name: weapons, ornaments, props.
- **Export:**
  - *Customized set*: one `.glb` with your part mix, colours and material.
  - *Original parts*: one `.glb` per part, untinted, with full-size textures.
- **Offline and read-only:** the game is never launched or modified. Nothing touches the running game or its anti-cheat.

## Requirements

- Windows, with For Honor installed (Steam or Ubisoft Connect).
- Internet access on first page load (three.js is loaded from a CDN).
- Running from source: Python 3.10+ with `numpy` and `Pillow`.

## Run

**From a release:** download `ForHonorBrowser.exe`, put it in an empty folder and double-click it.

**From source:**

```bash
pip install -r requirements.txt
python server.py
```

A console window shows progress and the app opens at `http://127.0.0.1:8765` (as an Edge app window when Edge is installed).

- **First start indexes the game** (about 3 minutes). Later starts take about 15 seconds.
- The index lives in a `cache` folder next to the app and is rebuilt automatically after a game update.
- The game folder is found automatically in the usual Steam and Ubisoft Connect locations. If it is not found, a folder picker opens: choose the folder that contains `forhonor.exe`. The choice is saved in `settings.json` next to the app.
- **Game folder…** (bottom of the left panel) changes the folder later; the app then re-indexes and reloads. The `FH_GAME` environment variable overrides both.
- Close the console window to quit.

## Use

1. **Heroes tab:** open a hero and click an outfit.
2. **Customize panel (right):** change helm, chest or arms, enable colours, pick a material.
3. **All models tab:** search by internal name and press `+` to add weapons or ornaments to the scene.
4. **Export** with one of the two buttons.

## Limits

- Static bind pose, highest level of detail only. No skeleton, skin weights or animations are exported.
- Diffuse, normal and colour-mask maps only. Colours and materials are an approximation of the game's system, not its real palettes or shaders.
- The material preset recolours everything the colour mask does not cover, which includes skin and cloth on some outfits.
- Weapons load at the origin, not in the hero's hand. Parts mix only within one hero.
- Some things do not decode or are misplaced: most effect ("GFX", "Mood") objects, level files (first 400 meshes, all at the origin), Lawbringer's shoulder pads, and a few placeholder outfits.
- Hero names were assigned by looking at renders of each internal codename. `h038titan` and a few one-off groups still show internal names; corrections are welcome (`DISPLAY` table in `server.py`).

## How it works

`fh.py` is a self-contained reader for the game's formats; [docs/FORMATS.md](docs/FORMATS.md) describes them.

- **Archives:** `.forge` v27 containers. Index blocks are XXTEA-encrypted; payloads are Oodle (Leviathan) streams, decoded with the `oo2core_7_win64.dll` that ships with the game.
- **Objects:** serialized object files link meshes, materials and texture sets by 64-bit ids.
- **Meshes:** stream-of-arrays vertex buffers (skinned and static variants) with clustered index buffers and per-LOD submesh tables.
- **Textures:** block-compressed (BC1/BC3/BC5) mip chains, some stored as byte deltas against a base texture.

`server.py` serves a small local web app (`index.html`, three.js) and builds `.glb` files on demand.

### Files

| File | Purpose |
|---|---|
| `fh.py` | Format reader and GLB builder. `python fh.py` runs a self-check. |
| `server.py`, `index.html` | The app. |
| `_test.py` | Builds N random models and reports failures. |
| `_sheet.py`, `_blsheet.py`, `_montage.py` | Render one outfit per hero into contact sheets (needs Blender). |

### Build the exe

```bash
pip install pyinstaller
python -m PyInstaller --onefile --console --name ForHonorBrowser --add-data "%CD%\index.html;." --distpath . --workpath build --specpath build server.py
```

## Legal

This project is an independent, non-commercial interoperability tool. It ships no Ubisoft code, keys or assets, and only reads files from a legitimately installed copy of the game. For Honor and all related assets are trademarks and copyrights of Ubisoft Entertainment. Use of extracted assets is your responsibility; do not redistribute them or use them commercially.

## Licence

Code: [MIT](LICENSE). Game assets are not covered and stay the property of Ubisoft.
