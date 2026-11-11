# Edit Mode (modelare manuala 3D) in Model Studio

Doua moduri, comutate din pilula **Object | Edit** de pe viewport (sau Tab pe tastatura):
- **Object Mode** – neschimbat: Add / Hierarchy / Properties, Move / Rotate / Scale, primitive.
- **Edit Mode** – geometrie reala: varfuri + fete poligonale. Butoane noi in Object Mode: **Edit** (intra in Edit Mode pe forma selectata) si **Join** (alipeste mai multe forme intr-un singur mesh).

Orice primitiva devine mesh editabil la intrarea in Edit Mode (un pas normal de Undo). `Add > Mesh` porneste direct de la un cub editabil.

## Unelte
Selectie: Vertex / Edge / Face (tap, **Multi** pentru selectie multipla, All / None / Invert, X-ray, Focus).
Extrude · Inset · Bevel · Loop Cut (slider live + Apply/Cancel) · Subdivide · Merge · Weld · Delete · Fill · Flip · Mirror X.
Move / Rotate / Scale cu gizmo-ul existent, pe varfurile selectate. Undo / Redo = cel din header.

## Tastatura (desktop)
Tab = Object/Edit · 1/2/3 = Vertex/Edge/Face · A / Alt+A = toate / niciuna · E Extrude · I Inset · B Bevel · Ctrl+R Loop Cut ·
M Merge · F Fill · X / Del Delete · G/R/S = Move/Rotate/Scale · Enter/Esc = Apply/Cancel · Ctrl+Z / Ctrl+Shift+Z · Shift = selectie multipla.

## Arhitectura
- `Part.type = "mesh"` + `Part.mesh = { v:[x,y,z,...], f:[[i,j,k,...],...] }` – acelasi model de piese, aceeasi ierarhie, acelasi Undo/Redo, salvare si Shop.
- `createObject()` construieste geometria din mesh, deci jocurile, avatarul, hainele si Shop-ul il randeaza fara cod nou.
- `src/studio3d/meshData.ts` (nucleu pur, testabil), `editSelection.ts`, `editOps.ts` (unelte), `editOverlay.ts` (three.js), `EditPanel.tsx` (UI tactil).
- Backend: `studio_routes.validate_mesh` (limite: 6000 varfuri / 6000 fete per mesh, 20000 / 20000 per model).
- Teste: `sh frontend/scripts/test-mesh.sh` si `python3 backend/astran_sandbox/test_studio_mesh.py`.