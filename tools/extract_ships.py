"""
extract_ships.py - pull real ship stats out of a Star Trek Bridge Commander install.

STBC hardpoint files are plain Python that build the ship by calling into the
engine (`App.PhaserProperty_Create(...)` and a few hundred `Set*` calls).  Rather
than regex them, we *execute* each one against a fake `App` module that records
every call.  That means we get exactly the numbers the engine gets, including
anything a mod changed.

Output: data/ships.json  -  one entry per hull, with hull/shields/engines/
weapons/subsystems, plus data/torpedoes.json for projectile stats.

Usage:  python extract_ships.py --install "<path to Star Trek Bridge Commander>"

Output goes to a data/ folder next to this script.

!! This EXECUTES every hardpoint file it finds (against a fake App module).
!! Only point it at an install whose scripts you trust.
"""

import json
import math
import os
import sys
import types
import warnings

# Old hardpoints are full of Windows paths in plain strings ("scripts\Custom\..."),
# which Python 3.12+ flags as invalid escapes.  The engine never cared, so neither do we.
warnings.filterwarnings("ignore", category=SyntaxWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INSTALL = None      # pass --install "<your install>\Star Trek Bridge Commander"


# --------------------------------------------------------------------------
# The fake engine
# --------------------------------------------------------------------------

class Sym(object):
    """Stands in for an engine constant (App.TGModelPropertyManager.LOCAL_TEMPLATES,
    ShieldGenerator.FRONT_SHIELDS, ...).  Also callable, because hardpoints reach
    for methods we do not model and we would rather record than crash."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return Sym(self._name + "." + item)

    def __call__(self, *args, **kw):
        return Sym(self._name + "()")

    def __repr__(self):
        return self._name

    def __eq__(self, other):
        return isinstance(other, Sym) and other._name == self._name

    def __hash__(self):
        return hash(self._name)


class Vec(object):
    """App.TGPoint3()"""

    def __init__(self):
        self.xyz = (0.0, 0.0, 0.0)

    def SetXYZ(self, x, y, z):
        self.xyz = (float(x), float(y), float(z))

    def GetX(self):
        return self.xyz[0]

    def GetY(self):
        return self.xyz[1]

    def GetZ(self):
        return self.xyz[2]

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return Sym("TGPoint3." + item)


class Colour(object):
    def __init__(self):
        self.rgba = (1.0, 1.0, 1.0, 1.0)

    def SetRGBA(self, r, g, b, a=1.0):
        self.rgba = (float(r), float(g), float(b), float(a))

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return Sym("TGColorA." + item)


class Str(object):
    def __init__(self):
        self.s = ""

    def SetString(self, s):
        self.s = str(s)

    def GetString(self):
        return self.s

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return Sym("TGString." + item)


def plain(v):
    """Convert a recorded argument into something JSON can hold."""
    if isinstance(v, Vec):
        return list(v.xyz)
    if isinstance(v, Colour):
        return list(v.rgba)
    if isinstance(v, Str):
        return v.s
    if isinstance(v, Sym):
        return repr(v)
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return repr(v)


class Prop(object):
    """A recorded App.*Property_Create() object."""

    def __init__(self, kind, name):
        self.kind = kind
        self.name = str(name)
        self.calls = {}

    def _record(self, meth, args):
        self.calls.setdefault(meth, []).append([plain(a) for a in args])

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        if item.startswith("Set") or item.startswith("Add") or item.startswith("Register"):
            def sink(*args, **kw):
                self._record(item, args)
                return None
            return sink
        # Anything else read off a property is an engine constant
        # (FRONT_SHIELDS, WST_TORPEDO, EP_IMPULSE, ...).
        return Sym(item)

    # convenience readers used by the builder ------------------------------
    def one(self, meth, index=0, default=None):
        rows = self.calls.get(meth)
        if not rows:
            return default
        v = rows[0]
        if index is None:
            return v
        if index < len(v):
            return v[index]
        return default

    def num(self, meth, default=0.0):
        v = self.one(meth, 0, None)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


class PropertyManager(object):
    def __init__(self):
        self.templates = []          # in creation order
        self.by_name = {}

    def RegisterLocalTemplate(self, prop):
        if isinstance(prop, Prop):
            self.templates.append(prop)
            self.by_name[prop.name] = prop

    def FindByName(self, name, which=None):
        return self.by_name.get(str(name))

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return Sym("g_kModelPropertyManager." + item)


class SetBuilder(object):
    """The `pObj` handed to LoadPropertySet - records which properties are
    actually attached to the ship (created-but-never-attached is a real and
    load-bearing distinction in this engine)."""

    def __init__(self):
        self.attached = []

    def AddToSet(self, where, prop):
        if isinstance(prop, Prop):
            self.attached.append(prop)

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return lambda *a, **k: None


class AppStub(object):
    def __init__(self):
        self.mgr = PropertyManager()
        self.created = []

    # App.g_kModelPropertyManager
    @property
    def g_kModelPropertyManager(self):
        return self.mgr

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        if item.endswith("Property_Create"):
            kind = item[:-len("Property_Create")]

            def maker(name="", *rest):
                p = Prop(kind, name)
                self.created.append(p)
                return p
            return maker
        if item == "TGPoint3":
            return Vec
        if item == "TGColorA":
            return Colour
        if item == "TGString":
            return Str
        if item.startswith("g_k") or item.startswith("g_p"):
            return Sym(item)
        return Sym(item)


class AnyModule(types.ModuleType):
    """Stands in for GlobalPropertyTemplates, Multiplayer.SpeciesToShip, ..."""

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return 0


def install_stub_modules(app):
    sys.modules["App"] = app
    for name in ("GlobalPropertyTemplates", "Multiplayer", "Multiplayer.SpeciesToShip",
                 "Multiplayer.SpeciesToTorp", "Multiplayer.MultiplayerTypes",
                 "Custom", "Custom.Bedrock", "Custom.Bedrock.MultiplayerTypes",
                 "loadspacehelper", "MissionLib", "ships"):
        sys.modules[name] = AnyModule(name)


def run_hardpoint(path):
    """Execute one hardpoint and return (app, attached_props) or raise."""
    src = open(path, "r", encoding="latin-1").read()
    # Two files in the corpus mix tabs and spaces; python 3 refuses those.
    try:
        compile(src, path, "exec")
    except (SyntaxError, TabError):
        src = "\n".join(line.expandtabs(8) for line in src.split("\n"))

    app = AppStub()
    install_stub_modules(app)
    ns = {"__name__": "hardpoint", "__builtins__": __builtins__}
    code = compile(src, path, "exec")
    exec(code, ns)

    builder = SetBuilder()
    loader = ns.get("LoadPropertySet")
    if loader is not None:
        try:
            loader(builder)
        except Exception:
            pass
    attached = builder.attached if builder.attached else app.mgr.templates
    return app, attached, ns


# --------------------------------------------------------------------------
# Turning recorded calls into a ship
# --------------------------------------------------------------------------

SHIELD_FACINGS = {
    "FRONT_SHIELDS": "front",
    "REAR_SHIELDS": "rear",
    "TOP_SHIELDS": "top",
    "BOTTOM_SHIELDS": "bottom",
    "LEFT_SHIELDS": "left",
    "RIGHT_SHIELDS": "right",
}


def facing_key(sym):
    s = str(sym).split(".")[-1]
    return SHIELD_FACINGS.get(s)


def common(p):
    return {
        "name": p.name,
        "hp": p.num("SetMaxCondition"),
        "critical": int(p.num("SetCritical")),
        "targetable": int(p.num("SetTargetable")),
        "disabled_pct": p.num("SetDisabledPercentage"),
        "radius": p.num("SetRadius"),
        "power": p.num("SetNormalPowerPerSecond"),
        "repair_complexity": p.num("SetRepairComplexity", 1.0),
    }


def orientation(p):
    rows = p.calls.get("SetOrientation")
    fwd, up = [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
    if rows:
        r = rows[0]
        if len(r) > 0 and isinstance(r[0], list):
            fwd = r[0]
        if len(r) > 1 and isinstance(r[1], list):
            up = r[1]
    return fwd, up


def build_ship(cls, app, attached, ns):
    ship = {
        "class": cls,
        "hull": 0.0,
        "shields": {},
        "shield_regen": {},
        "subsystems": [],
        "phasers": [],
        "pulses": [],
        "tubes": [],
        "ammo": [],
    }

    for p in attached:
        k = p.kind
        c = common(p)

        if k == "Hull":
            # The primary hull property is the ship's actual hull pool; the
            # others ("Bridge", nacelles on some hulls) are just subsystems.
            if p.num("SetPrimary") and c["hp"] > ship["hull"]:
                ship["hull"] = c["hp"]
                ship["hull_name"] = p.name
            else:
                ship["subsystems"].append(dict(c, kind="hull"))

        elif k == "Shield":
            for row in p.calls.get("SetMaxShields", []):
                f = facing_key(row[0])
                if f:
                    ship["shields"][f] = float(row[1])
            for row in p.calls.get("SetShieldChargePerSecond", []):
                f = facing_key(row[0])
                if f:
                    ship["shield_regen"][f] = float(row[1])
            ship["subsystems"].append(dict(c, kind="shields"))

        elif k == "Sensor":
            ship["sensor_range"] = p.num("SetBaseSensorRange")
            ship["subsystems"].append(dict(c, kind="sensors"))

        elif k == "ImpulseEngine":
            ship["accel"] = p.num("SetMaxAccel")
            ship["ang_accel"] = p.num("SetMaxAngularAccel")
            ship["ang_vel"] = p.num("SetMaxAngularVelocity")
            ship["max_speed"] = p.num("SetMaxSpeed")
            ship["subsystems"].append(dict(c, kind="impulse"))

        elif k == "WarpEngine":
            ship["subsystems"].append(dict(c, kind="warp"))

        elif k == "Power":
            ship["power_output"] = p.num("SetMainBatteryChargePerSecond") or p.num("SetPowerOutput")
            ship["battery"] = p.num("SetMainBatteryLimit")
            ship["subsystems"].append(dict(c, kind="power"))

        elif k == "RepairSubsystem":
            ship["repair_points"] = p.num("SetMaxRepairPoints")
            ship["repair_teams"] = p.num("SetNumRepairTeams")
            ship["subsystems"].append(dict(c, kind="repair"))

        elif k == "CloakingSubsystem":
            ship["cloak"] = 1
            ship["cloak_power"] = c["power"]
            ship["subsystems"].append(dict(c, kind="cloak"))

        elif k == "Phaser":
            fwd, up = orientation(p)
            aw = p.one("SetArcWidthAngles", None) or [-math.pi, math.pi]
            ah = p.one("SetArcHeightAngles", None) or [-math.pi / 2, math.pi / 2]
            ship["phasers"].append(dict(
                c, kind="phaser",
                damage=p.num("SetMaxDamage"),
                range=p.num("SetMaxDamageDistance"),
                max_charge=p.num("SetMaxCharge", 1.0),
                min_charge=p.num("SetMinFiringCharge", 1.0),
                recharge=p.num("SetRechargeRate", 0.5),
                discharge=p.num("SetNormalDischargeRate", 200.0),
                fwd=fwd, up=up,
                arc_w=[float(aw[0]), float(aw[1])],
                arc_h=[float(ah[0]), float(ah[1])],
                pos=p.one("SetPosition", None) or [0, 0, 0],
            ))

        elif k == "PulseWeapon":
            fwd, up = orientation(p)
            aw = p.one("SetArcWidthAngles", None) or [-0.5, 0.5]
            ah = p.one("SetArcHeightAngles", None) or [-0.5, 0.5]
            ship["pulses"].append(dict(
                c, kind="pulse",
                damage=p.num("SetMaxDamage"),
                range=p.num("SetMaxDamageDistance"),
                max_charge=p.num("SetMaxCharge", 1.0),
                min_charge=p.num("SetMinFiringCharge", 1.0),
                recharge=p.num("SetRechargeRate", 0.3),
                cooldown=p.num("SetCooldownTime", 0.3),
                module=p.one("SetModuleName"),
                fwd=fwd, up=up,
                arc_w=[float(aw[0]), float(aw[1])],
                arc_h=[float(ah[0]), float(ah[1])],
            ))

        elif k == "TorpedoSystem":
            ammo = {}
            for row in p.calls.get("SetMaxTorpedoes", []):
                try:
                    ammo.setdefault(int(row[0]), {})["max"] = float(row[1])
                except (TypeError, ValueError):
                    pass
            for row in p.calls.get("SetTorpedoScript", []):
                try:
                    ammo.setdefault(int(row[0]), {})["script"] = str(row[1])
                except (TypeError, ValueError):
                    pass
            ship["ammo"] = [dict(ammo[i], index=i) for i in sorted(ammo)]
            ship["subsystems"].append(dict(c, kind="torpedo_system"))

        elif k == "TorpedoTube":
            d = p.one("SetDirection", 0, None)
            ship["tubes"].append(dict(
                c, kind="tube",
                reload=p.num("SetReloadDelay", 10.0),
                ready=p.num("SetMaxReady", 1.0),
                immediate=p.num("SetImmediateDelay", 0.25),
                dir=d if isinstance(d, list) else [0.0, 1.0, 0.0],
            ))

        elif k == "Ship":
            ship["mass"] = p.num("SetMass")
            ship["inertia"] = p.num("SetRotationalInertia")
            ship["model"] = p.one("SetModelFilename")
            ship["ai"] = p.one("SetAIString")
            ship["ship_name"] = p.one("SetShipName")

    # A hull with no attached ShipProperty still carries one in the namespace.
    if "mass" not in ship:
        for p in app.mgr.templates:
            if p.kind == "Ship":
                ship["mass"] = p.num("SetMass")
                ship["model"] = p.one("SetModelFilename")
                ship["ai"] = p.one("SetAIString")
                break

    ship.setdefault("sensor_range", 2000.0)
    ship.setdefault("accel", 1.0)
    ship.setdefault("ang_accel", 0.2)
    ship.setdefault("ang_vel", 0.2)
    ship.setdefault("max_speed", 5.0)
    ship.setdefault("battery", 0.0)
    ship.setdefault("repair_points", 0.0)
    ship.setdefault("repair_teams", 0.0)
    ship.setdefault("cloak", 0)
    ship.setdefault("mass", 1000.0)
    return ship


# --------------------------------------------------------------------------
# Torpedoes and Bedrock menu metadata
# --------------------------------------------------------------------------

def extract_torpedoes(root):
    out = {}
    for base in (os.path.join(root, "py", "Orion", "Tactical", "Projectiles"),
                 os.path.join(root, "scripts", "Tactical", "Projectiles")):
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".py"):
                continue
            name = fn[:-3]
            path = os.path.join(base, fn)
            try:
                src = open(path, "r", encoding="latin-1").read()
                app = AppStub()
                install_stub_modules(app)
                ns = {"__name__": "torp", "__builtins__": __builtins__}
                exec(compile(src, path, "exec"), ns)
            except Exception:
                continue

            def get(fn_name, default):
                f = ns.get(fn_name)
                if not callable(f):
                    return default
                try:
                    return float(f())
                except Exception:
                    return default

            out[name] = {
                "damage": get("GetDamage", 200.0),
                "speed": get("GetLaunchSpeed", 100.0),
                "guidance": get("GetGuidanceLifetime", 0.1),
                "turn": get("GetMaxAngularAccel", 5.0),
                "power": get("GetPowerCost", 10.0),
                "label": (ns.get("GetName")() if callable(ns.get("GetName")) else name),
            }
    return out


def extract_bedrock(root):
    """Race / label / era / carrier complement, straight off the Bedrock stubs."""
    base = os.path.join(root, "scripts", "Custom", "Bedrock", "Ships")
    out = {}
    if not os.path.isdir(base):
        return out
    import re
    pat = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', re.M)
    npat = re.compile(r'^\s*(FighterQuantity\d)\s*=\s*(\d+)\s*$', re.M)
    for fn in sorted(os.listdir(base)):
        if not fn.endswith(".py"):
            continue
        try:
            src = open(os.path.join(base, fn), "r", encoding="latin-1").read()
        except Exception:
            continue
        d = dict(pat.findall(src))
        for k, v in npat.findall(src):
            d[k] = v
        name = d.get("ShipName")
        if not name:
            continue
        rec = {
            "label": d.get("Label", name),
            "race": d.get("Race", "Other"),
            "era": d.get("Era", ""),
            "hclass": d.get("Class", ""),
            "submenu": d.get("SubMenu", ""),
        }
        wing = []
        for i in range(1, 9):       # FighterType1..FighterType8
            t = d.get("FighterType%d" % i)
            q = d.get("FighterQuantity%d" % i)
            if t and q:
                wing.append({"type": t, "count": int(q)})
        if wing:
            rec["wing"] = wing
        out[name] = rec
        # Bedrock ShipName need not equal the hardpoint filename (new ships use
        # ZZ-prefixed plugin/ShipName for species-ID stability), so also key the
        # record by whatever hardpoint the ship script actually points at.
        hp = _hardpoint_for(root, name)
        if hp and hp not in out:
            out[hp] = rec
    return out


def _hardpoint_for(root, shipname):
    """scripts/ships/<ShipName>.py -> its declared "HardpointFile"."""
    import re
    d = os.path.join(root, "scripts", "ships")
    path = os.path.join(d, shipname + ".py")
    if not os.path.exists(path):
        try:
            low = (shipname + ".py").lower()
            match = None
            for x in os.listdir(d):
                if x.lower() == low:
                    match = os.path.join(d, x)
                    break
            if not match:
                return None
            path = match
        except Exception:
            return None
    try:
        src = open(path, "r", encoding="latin-1").read()
    except Exception:
        return None
    m = re.search(r'"HardpointFile"\s*:\s*"([^"]+)"', src)
    return m.group(1) if m else None


# --------------------------------------------------------------------------

def main():
    root = DEFAULT_INSTALL
    argv = sys.argv[1:]
    if "--install" in argv:
        root = argv[argv.index("--install") + 1]
    if not root:
        print(__doc__)
        return 2
    if not os.path.isdir(root):
        print("install not found: %s" % root)
        return 1

    sources = []
    corpus = os.path.join(root, "py", "Orion", "Ships", "Hardpoints")
    custom = os.path.join(root, "scripts", "ships", "Hardpoints")
    for base in (corpus, custom):          # custom overrides corpus
        if os.path.isdir(base):
            for fn in sorted(os.listdir(base)):
                if fn.endswith(".py") and not fn.startswith("__"):
                    sources.append((fn[:-3], os.path.join(base, fn)))

    ships = {}
    failed = []
    for cls, path in sources:
        try:
            app, attached, ns = run_hardpoint(path)
            ships[cls] = build_ship(cls, app, attached, ns)
        except Exception as exc:
            failed.append((cls, "%s: %s" % (type(exc).__name__, exc)))

    meta = extract_bedrock(root)
    # The engine spawns a ship by its scripts/ships/<name>.py module, which
    # since the ZZ save-compat scheme no longer always equals the hardpoint
    # class id (ships/ZZVoyagerJ.py -> Hardpoints/Janeway.py).  Map every
    # readable ship script's declared HardpointFile back to the script name
    # so consumers know what string loadspacehelper.CreateShip() accepts.
    spawn_by_hp = {}
    shipdir = os.path.join(root, "scripts", "ships")
    import re as _re
    for fn in sorted(os.listdir(shipdir)):
        if not fn.endswith(".py") or fn.startswith("__"):
            continue
        try:
            src = open(os.path.join(shipdir, fn), "r",
                       encoding="latin-1").read()
        except Exception:
            continue
        m = _re.search(r'"HardpointFile"\s*:\s*"([^"]+)"', src)
        if m:
            spawn_by_hp[m.group(1)] = fn[:-3]
    for cls, s in ships.items():
        s["spawn"] = spawn_by_hp.get(cls, cls)
        m = meta.get(cls)
        if m:
            s.update(m)
        else:
            s.setdefault("label", cls)
            s.setdefault("race", "Other")

    torps = extract_torpedoes(root)

    outdir = os.path.join(HERE, "data")
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    with open(os.path.join(outdir, "ships.json"), "w") as fh:
        json.dump(ships, fh, indent=1, sort_keys=True)
    with open(os.path.join(outdir, "torpedoes.json"), "w") as fh:
        json.dump(torps, fh, indent=1, sort_keys=True)

    armed = [s for s in ships.values() if s["phasers"] or s["tubes"] or s["pulses"]]
    print("hulls extracted : %d  (%d armed)" % (len(ships), len(armed)))
    print("torpedo types   : %d" % len(torps))
    print("failed          : %d" % len(failed))
    for cls, err in failed[:10]:
        print("   %-20s %s" % (cls, err))
    return 0


if __name__ == "__main__":
    sys.exit(main())
