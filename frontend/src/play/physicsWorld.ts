// frontend/src/play/physicsWorld.ts
//
// Fizica reala pentru Play Mode, pe cannon-es. Scriptul Luau al jocului (vezi
// backend/astran_sandbox/prelude.luau) trimite operatii "world" (gravitatie,
// densitatea aerului) si "material" (densitate, frecare, elasticitate, lichid),
// plus campurile "material"/"anchored"/"collide"/"vx,vy,vz" pe operatiile
// "create"/"set" ale fiecarui obiect. Acest modul le aplica intr-o simulare
// reala: obiectele MOBILE (Anchored = false) cad, se ciocnesc si se impinng
// dupa masa lor (masa = densitate * volum), exact ca in orice joc cu fizica.
//
// De ce un modul separat: play/[id].tsx are deja peste 600 de linii si
// raspunde de randare, camera, joystick si UI; fizica are propria stare
// (World, corpuri, materiale) si propriul ciclu de update, deci se izoleaza
// mai curat aici. play/[id].tsx doar il porneste, ii trimite operatiile
// primite de la server, si citeste inapoi pozitia/rotatia curenta a fiecarui
// corp pentru randare.
import * as CANNON from "cannon-es";
import * as THREE from "three";
import { SceneObj, SPAWN_TYPE } from "@/src/studio/sceneShared";

export type PhysicsShapeType = "cube" | "sphere" | "cylinder" | "cone" | "pyramid" | "tree";

// Valorile implicite trebuie sa fie identice cu cele din backend/astran_sandbox/prelude.luau
// (worldGravity, worldAir si Material.new), ca un joc fara niciun script de fizica sa se
// comporte exact ca inainte de aceasta functionalitate.
export const DEFAULT_GRAVITY = 18;       // m/s^2 (jos)
export const DEFAULT_AIR_DENSITY = 0.02; // g/cm^3 (aerul e f. putin dens)
export const DEFAULT_MATERIAL: MaterialDef = {
  name: "Default",
  density: 1,      // g/cm^3, apa = 1
  friction: 0.5,
  bounce: 0,
  liquid: false,
};

export type MaterialDef = {
  name: string;
  density: number;
  friction: number;
  bounce: number;
  liquid: boolean;
  color?: string;
};

export type PhysicsCreateOptions = {
  id: string;
  type: PhysicsShapeType;
  x: number; y: number; z: number;
  scale: number;
  anchored: boolean;       // implicit true (fix) - la fel ca in prelude.luau
  collide?: boolean;       // implicit: !anchored (mobil => solid; fix => fara coliziune, ca inainte)
  materialId?: string;     // cheie in tabelul de materiale (vezi setMaterial)
  velocity?: { x: number; y: number; z: number };
};

// Volumul fiecarei forme, la scale = 1 (aceleasi dimensiuni ca in sceneShared.geometryFor
// si in buildMesh din play/[id].tsx: cub 1x1x1, sfera r=0.6, cilindru r=0.5 h=1.2,
// con r=0.6 h=1.2, piramida ~con). Masa = densitate * volum * scale^3 (scalare uniforma).
function unitVolume(type: PhysicsShapeType): number {
  switch (type) {
    case "sphere":
      return (4 / 3) * Math.PI * 0.6 ** 3;       // ~0.9048
    case "cylinder":
      return Math.PI * 0.5 * 0.5 * 1.2;           // ~0.9425
    case "cone":
    case "pyramid":
      return (Math.PI * 0.6 * 0.6 * 1.2) / 3;     // ~0.4524
    case "tree":
      return (Math.PI * 0.7 * 0.7 * 1.6) / 3;
    case "cube":
    default:
      return 1;
  }
}

function makeShape(type: PhysicsShapeType, scale: number): CANNON.Shape {
  switch (type) {
    case "sphere":
      return new CANNON.Sphere(0.6 * scale);
    case "cylinder":
      return new CANNON.Cylinder(0.5 * scale, 0.5 * scale, 1.2 * scale, 12);
    case "cone":
    case "pyramid":
      // CANNON nu are un con nativ: un cilindru cu raza de sus foarte mica se comporta
      // la fel pentru scopul jocului (cade, se ciocneste, se rostogoleste usor pe varf).
      return new CANNON.Cylinder(0.001 * scale, 0.6 * scale, 1.2 * scale, 12);
    case "tree":
      return new CANNON.Cylinder(0.001 * scale, 0.7 * scale, 1.6 * scale, 8);
    case "cube":
    default:
      return new CANNON.Box(new CANNON.Vec3(0.5 * scale, 0.5 * scale, 0.5 * scale));
  }
}

type BodyEntry = {
  body: CANNON.Body;
  type: PhysicsShapeType;
  scale: number;
  anchored: boolean;
  materialId: string;
  explicitCollide: boolean | null; // null = "nesetat de script", foloseste regula impicita din anchored
};

export class PhysicsWorld {
  readonly world: CANNON.World;
  private bodies = new Map<string, BodyEntry>();
  private materials = new Map<string, { def: MaterialDef; cannon: CANNON.Material }>();
  private contactCache = new Map<string, CANNON.ContactMaterial>();
  private groundMaterial: CANNON.Material;
  private airDensity = DEFAULT_AIR_DENSITY;

  constructor() {
    this.world = new CANNON.World({ gravity: new CANNON.Vec3(0, -DEFAULT_GRAVITY, 0) });
    // SAPBroadphase e suficient de rapid pentru cateva sute de corpuri (limita din
    // backend, MAX_PARTS = 500) si nu are nevoie de o cutie de limite ca AABB-ul global.
    this.world.broadphase = new CANNON.SAPBroadphase(this.world);
    this.world.allowSleep = true; // corpurile care nu se misca nu mai consuma calcul

    this.groundMaterial = new CANNON.Material("ground");
    this.setMaterialDef("__default", DEFAULT_MATERIAL);
  }

  // ---------- lumea (workspace.Gravity / workspace.AirDensity din script) ----------

  setGravity(g: number) {
    this.world.gravity.set(0, -g, 0);
  }

  setAirDensity(air: number) {
    this.airDensity = air;
    // Rezistenta aerului: un corp mai putin dens decat aerul (ex: un balon intr-un
    // joc cu AirDensity mare) trebuie sa franeze si sa pluteasca mai mult. Simulam
    // asta simplu, prin damping proportional cu raportul aer/densitate-corp pe
    // fiecare corp, recalculat cand se schimba fie AirDensity, fie materialul.
    for (const entry of this.bodies.values()) this.applyDampingFor(entry);
  }

  // ---------- materiale (Material.new(...) din script) ----------

  setMaterialDef(id: string, def: MaterialDef) {
    let entry = this.materials.get(id);
    if (!entry) {
      const cannon = new CANNON.Material(id);
      entry = { def, cannon };
      this.materials.set(id, entry);
    } else {
      entry.def = def;
    }
    entry.cannon.friction = def.friction;
    entry.cannon.restitution = def.bounce;

    // Un script poate apela Material.new(...) de mai multe ori pe parcursul UNUI singur
    // joc (ex: schimba proprietatile unui material deja folosit) - fiecare apel ajunge
    // aici. Perechea de contact (sol <-> acest material) deja adaugata in `this.world`
    // trebuie SCOASA explicit inainte sa adaugam una noua (vezi ensureGroundContact mai
    // jos): cannon-es nu deduplica singur `world.contactmaterials`, deci fara asta,
    // fiecare apel repetat lasa in urma o perche "moarta" care ramane in lume pentru
    // totdeauna. O lume cu mii de ContactMaterial-uri moarte devine tot mai lenta la
    // fiecare pas de fizica (fiecare pas le parcurge pe toate), pana cand jocul
    // incepe sa se blocheze si, in cele din urma, sa pice - exact tipul de "memory
    // leak lent" care nu tine de randare, ci de simularea fizica in sine.
    const key = "ground:" + id;
    const old = this.contactCache.get(key);
    if (old) {
      this.world.removeContactMaterial(old);
      this.contactCache.delete(key);
    }

    for (const [pid, e] of this.bodies) {
      if (e.materialId === id) {
        this.applyMassFor(pid, e);
        this.applyDampingFor(e);
      }
    }
  }

  private getMaterialDef(id: string | undefined): MaterialDef {
    return this.materials.get(id ?? "__default")?.def ?? DEFAULT_MATERIAL;
  }

  private getCannonMaterial(id: string | undefined): CANNON.Material {
    return this.materials.get(id ?? "__default")?.cannon ?? this.materials.get("__default")!.cannon;
  }

  private ensureGroundContact(mat: CANNON.Material) {
    const key = "ground:" + mat.name;
    if (this.contactCache.has(key)) return;
    const cm = new CANNON.ContactMaterial(this.groundMaterial, mat, {
      friction: mat.friction,
      restitution: mat.restitution,
    });
    this.world.addContactMaterial(cm);
    this.contactCache.set(key, cm);
  }

  // ---------- corpuri (Part-urile din script si obiectele din Studio) ----------

  addGroundPlane() {
    const body = new CANNON.Body({ mass: 0, material: this.groundMaterial, type: CANNON.Body.STATIC });
    body.addShape(new CANNON.Plane());
    body.quaternion.setFromEuler(-Math.PI / 2, 0, 0);
    this.world.addBody(body);
  }

  // Obiectele facute in Studio (game.scene.objects) intra in fizica ca si corpuri FIXE
  // implicit (Anchored = true), exact comportamentul dinainte de aceasta functionalitate:
  // stau pe loc si opresc jucatorul. Devin mobile doar daca scriptul le schimba explicit
  // Anchored-ul prin workspace.NumeObiect.Anchored = false.
  addStudioObject(o: SceneObj) {
    if (o.type === SPAWN_TYPE || o.visible === false) return;
    this.upsert({
      id: o.id,
      type: (o.type as PhysicsShapeType) || "cube",
      x: o.x, y: o.y + 0.5 * o.scale, z: o.z, // Studio pozitioneaza obiectele cu baza la "y" (vezi applyTransform)
      scale: o.scale,
      anchored: true,
      collide: o.solid !== false,
    });
  }

  upsert(opts: PhysicsCreateOptions) {
    this.remove(opts.id);

    const materialId = opts.materialId && this.materials.has(opts.materialId) ? opts.materialId : "__default";
    const def = this.getMaterialDef(materialId);
    const shape = makeShape(opts.type, Math.max(0.01, opts.scale));
    const collide = opts.collide ?? !opts.anchored;

    const body = new CANNON.Body({
      mass: opts.anchored ? 0 : this.massFor(opts.type, opts.scale, def.density),
      type: opts.anchored ? CANNON.Body.STATIC : CANNON.Body.DYNAMIC,
      position: new CANNON.Vec3(opts.x, opts.y, opts.z),
      material: this.getCannonMaterial(materialId),
      collisionResponse: collide,
    });
    body.addShape(shape);
    if (opts.velocity) body.velocity.set(opts.velocity.x, opts.velocity.y, opts.velocity.z);

    this.world.addBody(body);
    this.ensureGroundContact(body.material as CANNON.Material);

    const entry: BodyEntry = {
      body,
      type: opts.type,
      scale: opts.scale,
      anchored: opts.anchored,
      materialId,
      explicitCollide: opts.collide ?? null,
    };
    this.bodies.set(opts.id, entry);
    this.applyDampingFor(entry);
  }

  remove(id: string) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    this.world.removeBody(entry.body);
    this.bodies.delete(id);
  }

  has(id: string): boolean {
    return this.bodies.has(id);
  }

  setPosition(id: string, x?: number, y?: number, z?: number) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    const p = entry.body.position;
    entry.body.position.set(x ?? p.x, y ?? p.y, z ?? p.z);
    entry.body.wakeUp();
  }

  setScale(id: string, scale: number) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    // cannon-es nu suporta redimensionarea unui shape existent: recream corpul,
    // pastrand pozitia, viteza si restul proprietatilor curente.
    const b = entry.body;
    this.upsert({
      id,
      type: entry.type,
      x: b.position.x, y: b.position.y, z: b.position.z,
      scale,
      anchored: entry.anchored,
      collide: entry.explicitCollide ?? undefined,
      materialId: entry.materialId,
      velocity: { x: b.velocity.x, y: b.velocity.y, z: b.velocity.z },
    });
  }

  setShapeType(id: string, type: PhysicsShapeType) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    const b = entry.body;
    this.upsert({
      id,
      type,
      x: b.position.x, y: b.position.y, z: b.position.z,
      scale: entry.scale,
      anchored: entry.anchored,
      collide: entry.explicitCollide ?? undefined,
      materialId: entry.materialId,
      velocity: { x: b.velocity.x, y: b.velocity.y, z: b.velocity.z },
    });
  }

  setMaterial(id: string, materialId: string) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    const resolved = materialId && this.materials.has(materialId) ? materialId : "__default";
    entry.materialId = resolved;
    entry.body.material = this.getCannonMaterial(resolved);
    this.ensureGroundContact(entry.body.material as CANNON.Material);
    this.applyMassFor(id, entry);
    this.applyDampingFor(entry);
  }

  setAnchored(id: string, anchored: boolean) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    entry.anchored = anchored;
    entry.body.type = anchored ? CANNON.Body.STATIC : CANNON.Body.DYNAMIC;
    entry.body.mass = anchored ? 0 : this.massFor(entry.type, entry.scale, this.getMaterialDef(entry.materialId).density);
    if (entry.explicitCollide === null) {
      entry.body.collisionResponse = !anchored;
    }
    entry.body.velocity.set(0, 0, 0);
    entry.body.angularVelocity.set(0, 0, 0);
    entry.body.updateMassProperties();
    entry.body.wakeUp();
  }

  setCollide(id: string, collide: boolean) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    entry.explicitCollide = collide;
    entry.body.collisionResponse = collide;
  }

  setVelocity(id: string, vx?: number, vy?: number, vz?: number) {
    const entry = this.bodies.get(id);
    if (!entry) return;
    const v = entry.body.velocity;
    entry.body.velocity.set(vx ?? v.x, vy ?? v.y, vz ?? v.z);
    entry.body.wakeUp();
  }

  private massFor(type: PhysicsShapeType, scale: number, density: number): number {
    // masa = densitate * volum; scalare uniforma => volumul creste cu scale^3
    return Math.max(0.001, density * unitVolume(type) * Math.max(0.01, scale) ** 3);
  }

  private applyMassFor(id: string, entry: BodyEntry) {
    if (entry.anchored) return; // corpurile fixe raman mereu cu masa 0
    const density = this.getMaterialDef(entry.materialId).density;
    entry.body.mass = this.massFor(entry.type, entry.scale, density);
    entry.body.updateMassProperties();
  }

  // Aproximare simpla a rezistentei aerului / plutirii in lichid: un damping mai
  // mare cand corpul e mult mai putin dens decat mediul (AirDensity), sau cand
  // materialul e marcat Liquid (se comporta ca vascozitatea unui fluid).
  private applyDampingFor(entry: BodyEntry) {
    const def = this.getMaterialDef(entry.materialId);
    let damping = Math.min(0.3, this.airDensity / Math.max(0.05, def.density) * 0.2);
    if (def.liquid) damping = Math.max(damping, 0.6);
    entry.body.linearDamping = damping;
    entry.body.angularDamping = damping;
  }

  // ---------- pas de simulare ----------

  step(dt: number) {
    // pas fix, cateva sub-iteratii pentru stabilitate la dt mare (schimbare de ecran, etc)
    this.world.step(1 / 60, dt, 5);
  }

  getTransform(id: string): { position: THREE.Vector3; quaternion: THREE.Quaternion } | null {
    const entry = this.bodies.get(id);
    if (!entry) return null;
    const p = entry.body.position;
    const q = entry.body.quaternion;
    return {
      position: new THREE.Vector3(p.x, p.y, p.z),
      quaternion: new THREE.Quaternion(q.x, q.y, q.z, q.w),
    };
  }

  isAnchored(id: string): boolean {
    return this.bodies.get(id)?.anchored ?? true;
  }

  dispose() {
    for (const entry of this.bodies.values()) this.world.removeBody(entry.body);
    this.bodies.clear();
    this.materials.clear();
    this.contactCache.clear();
  }
}