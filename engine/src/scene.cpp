#include "kage/scene.hpp"

#include <set>

namespace kage {

namespace {

using json::Value;

struct Ctx {
    std::string path;
    [[noreturn]] void fail(const std::string& msg) const { throw SceneError(path + ": " + msg); }
    Ctx sub(const std::string& k) const { return {path + "." + k}; }
    Ctx idx(size_t i) const { return {path + "[" + std::to_string(i) + "]"}; }
};

const Value& req(const Value& v, const std::string& key, const Ctx& c) {
    const Value* p = v.get(key);
    if (!p) c.fail("missing required field '" + key + "'");
    return *p;
}

double num(const Value& v, const std::string& key, const Ctx& c, double def, bool required = false) {
    const Value* p = v.get(key);
    if (!p) { if (required) c.fail("missing required number '" + key + "'"); return def; }
    if (!p->isNumber()) c.sub(key).fail("expected number");
    return p->n;
}

std::string str(const Value& v, const std::string& key, const Ctx& c, const std::string& def, bool required = false) {
    const Value* p = v.get(key);
    if (!p) { if (required) c.fail("missing required string '" + key + "'"); return def; }
    if (!p->isString()) c.sub(key).fail("expected string");
    return p->s;
}

bool boolean(const Value& v, const std::string& key, const Ctx& c, bool def) {
    const Value* p = v.get(key);
    if (!p) return def;
    if (!p->isBool()) c.sub(key).fail("expected boolean");
    return p->b;
}

Vec2 vec2(const Value& v, const Ctx& c) {
    if (!v.isArray() || v.a.size() != 2 || !v.a[0].isNumber() || !v.a[1].isNumber())
        c.fail("expected [x, y] number pair");
    return {v.a[0].n, v.a[1].n};
}

Vec2 vec2Field(const Value& v, const std::string& key, const Ctx& c, Vec2 def, bool required = false) {
    const Value* p = v.get(key);
    if (!p) { if (required) c.fail("missing required point '" + key + "'"); return def; }
    return vec2(*p, c.sub(key));
}

Color color(const Value& v, const std::string& key, const Ctx& c, const Color& def, bool* present = nullptr) {
    const Value* p = v.get(key);
    if (present) *present = p != nullptr && !p->isNull();
    if (!p || p->isNull()) return def;
    Color out;
    if (!p->isString() || !parseColor(p->s, out)) c.sub(key).fail("expected color '#RRGGBB' or '#RRGGBBAA'");
    return out;
}

Ease ease(const Value& v, const std::string& key, const Ctx& c, Ease def) {
    const Value* p = v.get(key);
    if (!p) return def;
    Ease e;
    if (!p->isString() || !parseEase(p->s, e)) c.sub(key).fail("unknown easing (linear|in|out|in_out|step)");
    return e;
}

int jointRef(const std::string& name, const Ctx& c) {
    int j = jointFromName(name);
    if (j < 0) c.fail("unknown joint '" + name + "'");
    return j;
}

std::vector<PointRef> pointList(const Value& v, const Ctx& c, bool allowBind) {
    if (!v.isArray()) c.fail("expected array of points");
    std::vector<PointRef> out;
    for (size_t i = 0; i < v.a.size(); ++i) {
        const Value& e = v.a[i];
        PointRef pr;
        if (e.isArray()) {
            pr.p = vec2(e, c.idx(i));
        } else if (e.isObject() && allowBind) {
            pr.p = vec2Field(e, "p", c.idx(i), {}, true);
            pr.joint = jointRef(str(e, "bind", c.idx(i), "", true), c.idx(i).sub("bind"));
        } else {
            c.idx(i).fail(allowBind ? "expected [x,y] or {bind, p}" : "expected [x,y]");
        }
        out.push_back(pr);
    }
    return out;
}

Primitive primitive(const Value& v, const Ctx& c, bool character) {
    if (!v.isObject()) c.fail("expected primitive object");
    Primitive p;
    std::string type = str(v, "type", c, "", true);
    p.z = num(v, "z", c, 0);
    p.tag = str(v, "tag", c, "");
    p.fill = color(v, "fill", c, {}, &p.hasFill);
    p.stroke = color(v, "stroke", c, {}, &p.hasStroke);
    p.strokeWidth = num(v, "stroke_width", c, 0);
    if (p.hasStroke && p.strokeWidth <= 0) c.fail("stroke requires positive stroke_width");
    if (v.has("cap") && !parseCap(str(v, "cap", c, ""), p.cap)) c.sub("cap").fail("unknown cap");
    if (v.has("join") && !parseJoin(str(v, "join", c, ""), p.join)) c.sub("join").fail("unknown join");
    if (character) {
        std::string bind = str(v, "bind", c, "");
        if (!bind.empty()) p.bind = jointRef(bind, c.sub("bind"));
    }
    if (type == "polygon" || type == "polyline") {
        p.kind = type == "polygon" ? Primitive::Kind::Polygon : Primitive::Kind::Polyline;
        p.points = pointList(req(v, "points", c), c.sub("points"), character);
        p.closed = type == "polygon" || boolean(v, "closed", c, false);
        if (p.kind == Primitive::Kind::Polygon && p.points.size() < 3) c.fail("polygon needs at least 3 points");
        if (p.kind == Primitive::Kind::Polyline && p.points.size() < 1) c.fail("polyline needs points");
        if (const Value* h = v.get("holes")) {
            if (!h->isArray()) c.sub("holes").fail("expected array");
            for (size_t i = 0; i < h->a.size(); ++i)
                p.holes.push_back(pointList(h->a[i], c.sub("holes").idx(i), character));
        }
        if (p.kind == Primitive::Kind::Polyline && !p.hasStroke) c.fail("polyline requires stroke");
    } else if (type == "circle" || type == "ellipse") {
        p.kind = type == "circle" ? Primitive::Kind::Circle : Primitive::Kind::Ellipse;
        p.center = vec2Field(v, "center", c, {0, 0});
        if (p.kind == Primitive::Kind::Circle) {
            p.rx = p.ry = num(v, "radius", c, 0, true);
        } else {
            p.rx = num(v, "rx", c, 0, true);
            p.ry = num(v, "ry", c, 0, true);
            p.rotation = deg2rad(num(v, "rotation", c, 0));
        }
        if (p.rx <= 0 || p.ry <= 0) c.fail("radius must be positive");
    } else if (type == "rect") {
        p.kind = Primitive::Kind::Polygon;
        Vec2 mn = vec2Field(v, "min", c, {}, true), mx = vec2Field(v, "max", c, {}, true);
        if (mx.x <= mn.x || mx.y <= mn.y) c.fail("rect max must exceed min");
        p.points = {{mn, -1}, {{mx.x, mn.y}, -1}, {mx, -1}, {{mn.x, mx.y}, -1}};
        p.closed = true;
    } else if (type == "bone" && character) {
        p.kind = Primitive::Kind::Bone;
        p.boneFrom = jointRef(str(v, "from", c, "", true), c.sub("from"));
        p.boneTo = jointRef(str(v, "to", c, "", true), c.sub("to"));
        p.t0 = num(v, "t0", c, 0);
        p.t1 = num(v, "t1", c, 1);
        if (!p.hasStroke) c.fail("bone primitive requires stroke and stroke_width");
    } else {
        c.sub("type").fail("unknown primitive type '" + type + "'");
    }
    if (!p.hasFill && !p.hasStroke) c.fail("primitive has neither fill nor stroke");
    return p;
}

std::vector<Primitive> primitives(const Value* v, const Ctx& c, bool character) {
    std::vector<Primitive> out;
    if (!v) return out;
    if (!v->isArray()) c.fail("expected array of primitives");
    for (size_t i = 0; i < v->a.size(); ++i) out.push_back(primitive(v->a[i], c.idx(i), character));
    return out;
}

RigSpec rigSpec(const Value& v, const Ctx& c) {
    RigSpec r;
    auto pos = [&](const char* k, double& dst) {
        dst = num(v, k, c, dst);
        if (dst <= 0) c.sub(k).fail("must be positive");
    };
    pos("head_radius", r.headRadius); pos("neck", r.neck); pos("torso", r.torso);
    pos("upper_arm", r.upperArm); pos("forearm", r.forearm); pos("hand_radius", r.handRadius);
    pos("thigh", r.thigh); pos("shin", r.shin); pos("foot", r.foot); pos("ankle_height", r.ankleHeight);
    pos("line_width", r.lineWidth); pos("stride", r.stride); pos("run_stride", r.runStride);
    r.shoulderDrop = num(v, "shoulder_drop", c, r.shoulderDrop);
    r.postureSpine = num(v, "posture_spine", c, 0);
    r.postureNeck = num(v, "posture_neck", c, 0);
    r.armSwing = num(v, "arm_swing", c, r.armSwing);
    r.energy = num(v, "energy", c, 1.0);
    return r;
}

Action action(const Value& v, const Ctx& c, int index) {
    Action a;
    a.index = index;
    std::string type = str(v, "type", c, "", true);
    if (!parseActionType(type, a.type)) c.sub("type").fail("unknown action '" + type + "'");
    a.start = num(v, "start", c, 0, true);
    a.duration = num(v, "duration", c, 0, true);
    if (a.start < 0) c.sub("start").fail("must be >= 0");
    if (a.duration <= 0) c.sub("duration").fail("must be > 0");
    a.blendIn = num(v, "blend_in", c, std::min(0.25, a.duration / 2));
    a.blendOut = num(v, "blend_out", c, 0.25);
    a.hold = boolean(v, "hold", c, false);
    a.intensity = num(v, "intensity", c, 1.0);
    std::string side = str(v, "side", c, "r");
    if (side != "l" && side != "r") c.sub("side").fail("expected 'l' or 'r'");
    a.side = side == "l" ? 0 : 1;
    if (v.has("to_x")) { a.toX = num(v, "to_x", c, 0); a.hasToX = true; }
    a.gesture = str(v, "gesture", c, "");
    a.seatY = num(v, "seat_y", c, 0.45);
    a.prop = str(v, "prop", c, "");
    a.spine = num(v, "spine", c, 0);
    a.neck = num(v, "neck", c, 0);
    a.shiftX = num(v, "shift_x", c, 0);
    if (v.has("place_at")) { a.placeAt = vec2Field(v, "place_at", c, {}); a.hasPlaceAt = true; }
    if (const Value* f = v.get("facing")) {
        if (!f->isString() || (f->s != "left" && f->s != "right")) c.sub("facing").fail("expected 'left' or 'right'");
        a.facing = f->s == "left" ? -1 : 1;
    }
    if (const Value* t = v.get("target")) {
        Ctx tc = c.sub("target");
        if (t->isArray()) a.targetPoint = vec2(*t, tc);
        else if (t->isObject()) {
            a.targetCharacter = str(*t, "character", tc, "", true);
            a.targetJoint = jointRef(str(*t, "joint", tc, "head"), tc.sub("joint"));
        } else tc.fail("expected [x,y] or {character, joint}");
    }
    if (const Value* ch = v.get("channels")) {
        Ctx cc = c.sub("channels");
        if (!ch->isObject()) cc.fail("expected object");
        static const std::set<std::string> known = {
            "spine", "neck", "tilt", "shoulder_l", "shoulder_r", "elbow_l", "elbow_r", "hip_l", "hip_r",
            "knee_l", "knee_r", "ankle_l", "ankle_r", "pelvis_y", "pelvis_x"};
        for (const auto& kv : ch->o) {
            if (!known.count(kv.first)) cc.fail("unknown channel '" + kv.first + "'");
            const Value& arr = kv.second;
            if (!arr.isArray() || arr.a.empty()) cc.sub(kv.first).fail("expected non-empty key list");
            std::vector<ChannelKey> keys;
            double last = -1;
            for (size_t i = 0; i < arr.a.size(); ++i) {
                const Value& k = arr.a[i];
                Ctx kc = cc.sub(kv.first).idx(i);
                if (!k.isArray() || k.a.size() < 2 || !k.a[0].isNumber() || !k.a[1].isNumber())
                    kc.fail("expected [t, value, ease?]");
                ChannelKey key{k.a[0].n, k.a[1].n, Ease::InOut};
                if (k.a.size() > 2 && (!k.a[2].isString() || !parseEase(k.a[2].s, key.ease))) kc.fail("bad ease");
                if (key.t < last) kc.fail("channel keys must be time-ordered");
                last = key.t;
                keys.push_back(key);
            }
            a.channels[kv.first] = keys;
        }
    }
    if ((a.type == ActionType::Walk || a.type == ActionType::Run) && !a.hasToX) c.fail("walk/run requires to_x");
    if (a.type == ActionType::Gesture) {
        static const std::set<std::string> g = {"wave", "point", "shrug", "nod", "head_shake",
                                                "hands_on_hips", "arms_crossed", "clap"};
        if (!g.count(a.gesture)) c.sub("gesture").fail("unknown gesture '" + a.gesture + "'");
    }
    if ((a.type == ActionType::PickUp || a.type == ActionType::PutDown) && a.prop.empty()) c.fail("requires 'prop'");
    if (a.type == ActionType::Reach && a.targetCharacter.empty() && !v.has("target")) c.fail("reach requires target");
    if ((a.type == ActionType::Keyframes || a.type == ActionType::Pose) && a.channels.empty())
        c.fail("keyframes/pose require channels");
    return a;
}

}  // namespace

bool parseActionType(const std::string& s, ActionType& out) {
    static const std::pair<const char*, ActionType> table[] = {
        {"walk", ActionType::Walk}, {"run", ActionType::Run}, {"turn", ActionType::Turn},
        {"sit", ActionType::Sit}, {"stand", ActionType::Stand}, {"fall", ActionType::Fall},
        {"recover", ActionType::Recover}, {"gesture", ActionType::Gesture}, {"talk", ActionType::Talk},
        {"look", ActionType::Look}, {"lean", ActionType::Lean}, {"reach", ActionType::Reach},
        {"pick_up", ActionType::PickUp}, {"put_down", ActionType::PutDown},
        {"keyframes", ActionType::Keyframes}, {"pose", ActionType::Pose}};
    for (const auto& e : table)
        if (s == e.first) { out = e.second; return true; }
    return false;
}

const char* actionTypeName(ActionType t) {
    switch (t) {
        case ActionType::Walk: return "walk"; case ActionType::Run: return "run";
        case ActionType::Turn: return "turn"; case ActionType::Sit: return "sit";
        case ActionType::Stand: return "stand"; case ActionType::Fall: return "fall";
        case ActionType::Recover: return "recover"; case ActionType::Gesture: return "gesture";
        case ActionType::Talk: return "talk"; case ActionType::Look: return "look";
        case ActionType::Lean: return "lean"; case ActionType::Reach: return "reach";
        case ActionType::PickUp: return "pick_up"; case ActionType::PutDown: return "put_down";
        case ActionType::Keyframes: return "keyframes"; case ActionType::Pose: return "pose";
    }
    return "?";
}

const Character* Scene::findCharacter(const std::string& id) const {
    for (const auto& c : characters)
        if (c.id == id) return &c;
    return nullptr;
}

const Layer* Scene::findLayer(const std::string& id) const {
    for (const auto& l : layers)
        if (l.id == id) return &l;
    return nullptr;
}

Scene loadScene(const json::Value& root) {
    Ctx c{"scene"};
    if (!root.isObject()) c.fail("root must be an object");
    Scene s;
    s.version = str(root, "kage_scene_version", c, "", true);
    if (s.version.rfind("1.", 0) != 0) c.sub("kage_scene_version").fail("unsupported version '" + s.version + "' (engine supports 1.x)");
    s.shotId = str(root, "shot_id", c, "", true);

    const Value& out = req(root, "output", c);
    Ctx oc = c.sub("output");
    s.width = static_cast<int>(num(out, "width", oc, 0, true));
    s.height = static_cast<int>(num(out, "height", oc, 0, true));
    if (s.width < 16 || s.height < 16 || s.width > 8192 || s.height > 8192) oc.fail("resolution out of range");
    if (s.width % 2 || s.height % 2) oc.fail("width and height must be even (yuv420p delivery)");
    const Value& fps = req(out, "fps", oc);
    s.fps.num = static_cast<int>(num(fps, "num", oc.sub("fps"), 0, true));
    s.fps.den = static_cast<int>(num(fps, "den", oc.sub("fps"), 1));
    if (s.fps.num <= 0 || s.fps.den <= 0) oc.sub("fps").fail("must be positive rational");
    s.frameCount = static_cast<int>(num(out, "frame_count", oc, 0, true));
    if (s.frameCount <= 0) oc.sub("frame_count").fail("must be positive");
    s.background = color(out, "background", oc, {1, 1, 1, 1});
    s.safeArea = num(out, "safe_area", oc, 0.9);
    s.timeOffset = num(out, "time_offset", oc, 0);

    if (const Value* layers = root.get("layers")) {
        std::set<std::string> ids;
        for (size_t i = 0; i < layers->a.size(); ++i) {
            const Value& lv = layers->a[i];
            Ctx lc = c.sub("layers").idx(i);
            Layer l;
            l.id = str(lv, "id", lc, "", true);
            if (!ids.insert(l.id).second) lc.fail("duplicate layer id '" + l.id + "'");
            l.order = num(lv, "order", lc, static_cast<double>(i));
            l.parallax = num(lv, "parallax", lc, 1.0);
            l.parallaxY = num(lv, "parallax_y", lc, l.parallax);
            l.depth = num(lv, "depth", lc, 0.0);
            s.layers.push_back(l);
        }
    }
    if (s.layers.empty()) s.layers.push_back({"main", 0, 1, 1, 0});

    const Value& cam = req(root, "camera", c);
    Ctx cc = c.sub("camera");
    s.camera.projection = str(cam, "projection", cc, "orthographic");
    if (s.camera.projection != "orthographic" && s.camera.projection != "perspective")
        cc.sub("projection").fail("expected orthographic or perspective");
    s.camera.focalDepth = num(cam, "focal_depth", cc, 10.0);
    const Value& keys = req(cam, "keys", cc);
    if (!keys.isArray() || keys.a.empty()) cc.sub("keys").fail("need at least one camera key");
    double last = -1;
    for (size_t i = 0; i < keys.a.size(); ++i) {
        Ctx kc = cc.sub("keys").idx(i);
        CameraKey k;
        k.t = num(keys.a[i], "t", kc, 0, true);
        if (k.t < last) kc.fail("camera keys must be time-ordered");
        last = k.t;
        k.pos = {num(keys.a[i], "x", kc, 0, true), num(keys.a[i], "y", kc, 0, true)};
        k.height = num(keys.a[i], "height", kc, 0, true);
        if (k.height <= 0) kc.fail("height must be positive");
        k.roll = num(keys.a[i], "roll", kc, 0);
        k.ease = ease(keys.a[i], "ease", kc, Ease::InOut);
        s.camera.keys.push_back(k);
    }
    if (const Value* f = cam.get("follow")) {
        if (!f->isNull()) {
            Ctx fc = cc.sub("follow");
            s.camera.followTarget = str(*f, "character", fc, "", true);
            s.camera.followOffset = vec2Field(*f, "offset", fc, {0, 0});
            s.camera.followSmoothing = num(*f, "smoothing", fc, 0.5);
            std::string axes = str(*f, "axes", fc, "x");
            s.camera.followX = axes.find('x') != std::string::npos;
            s.camera.followY = axes.find('y') != std::string::npos;
        }
    }

    auto checkLayer = [&](const std::string& id, const Ctx& ctx) {
        if (!s.findLayer(id)) ctx.fail("unknown layer '" + id + "'");
    };

    std::set<std::string> entityIds;
    if (const Value* props = root.get("props")) {
        for (size_t i = 0; i < props->a.size(); ++i) {
            const Value& pv = props->a[i];
            Ctx pc = c.sub("props").idx(i);
            Prop p;
            p.id = str(pv, "id", pc, "", true);
            if (!entityIds.insert(p.id).second) pc.fail("duplicate entity id '" + p.id + "'");
            p.asset = str(pv, "asset", pc, "");
            p.layer = str(pv, "layer", pc, s.layers.front().id);
            checkLayer(p.layer, pc.sub("layer"));
            p.z = num(pv, "z", pc, 0);
            p.flip = boolean(pv, "flip", pc, false);
            p.important = boolean(pv, "important", pc, false);
            p.velocity = vec2Field(pv, "velocity", pc, {0, 0});
            p.grip = vec2Field(pv, "grip", pc, {0, 0});
            if (const Value* tile = pv.get("tile")) {
                p.tilePeriod = num(*tile, "period", pc.sub("tile"), 0, true);
                p.tileCount = static_cast<int>(num(*tile, "count", pc.sub("tile"), 1));
                if (p.tilePeriod <= 0 || p.tileCount < 0 || p.tileCount > 64) pc.sub("tile").fail("invalid tile");
            }
            if (const Value* tk = pv.get("keys")) {
                double lt = -1;
                for (size_t k = 0; k < tk->a.size(); ++k) {
                    Ctx kc = pc.sub("keys").idx(k);
                    TransformKey key;
                    key.t = num(tk->a[k], "t", kc, 0);
                    if (key.t < lt) kc.fail("keys must be time-ordered");
                    lt = key.t;
                    key.pos = {num(tk->a[k], "x", kc, 0), num(tk->a[k], "y", kc, 0)};
                    key.rotation = num(tk->a[k], "rotation", kc, 0);
                    key.scale = num(tk->a[k], "scale", kc, 1);
                    key.ease = ease(tk->a[k], "ease", kc, Ease::InOut);
                    p.keys.push_back(key);
                }
            }
            if (p.keys.empty()) {
                TransformKey key;
                key.pos = vec2Field(pv, "position", pc, {0, 0});
                key.rotation = num(pv, "rotation", pc, 0);
                key.scale = num(pv, "scale", pc, 1);
                p.keys.push_back(key);
            }
            p.prims = primitives(pv.get("primitives"), pc.sub("primitives"), false);
            if (const Value* parts = pv.get("parts")) {
                for (size_t k = 0; k < parts->a.size(); ++k) {
                    const Value& qv = parts->a[k];
                    Ctx qc = pc.sub("parts").idx(k);
                    Part part;
                    part.id = str(qv, "id", qc, "", true);
                    part.pivot = vec2Field(qv, "pivot", qc, {0, 0});
                    if (const Value* an = qv.get("anim")) {
                        std::string at = str(*an, "type", qc.sub("anim"), "", true);
                        Ctx ac = qc.sub("anim");
                        if (at == "oscillate") part.anim.type = PartAnim::Type::Oscillate;
                        else if (at == "spin") part.anim.type = PartAnim::Type::Spin;
                        else if (at == "bob") part.anim.type = PartAnim::Type::Bob;
                        else ac.fail("unknown part animation '" + at + "'");
                        part.anim.amplitude = num(*an, "amplitude", ac, 0);
                        part.anim.frequency = num(*an, "frequency", ac, 0);
                        part.anim.phase = num(*an, "phase", ac, 0);
                        part.anim.degPerSec = num(*an, "deg_per_sec", ac, 0);
                        part.anim.axis = vec2Field(*an, "axis", ac, {0, 1});
                    }
                    part.prims = primitives(qv.get("primitives"), qc.sub("primitives"), false);
                    p.parts.push_back(part);
                }
            }
            if (p.prims.empty() && p.parts.empty()) pc.fail("prop has no primitives");
            if (const Value* at = pv.get("attach")) {
                for (size_t k = 0; k < at->a.size(); ++k) {
                    Ctx ac = pc.sub("attach").idx(k);
                    AttachEvent e;
                    e.t = num(at->a[k], "t", ac, 0, true);
                    e.character = str(at->a[k], "character", ac, "");
                    if (!e.character.empty()) e.joint = jointRef(str(at->a[k], "joint", ac, "hand_r"), ac.sub("joint"));
                    e.offset = vec2Field(at->a[k], e.character.empty() ? "position" : "offset", ac, {0, 0});
                    p.attach.push_back(e);
                }
            }
            s.props.push_back(p);
        }
    }

    if (const Value* chars = root.get("characters")) {
        for (size_t i = 0; i < chars->a.size(); ++i) {
            const Value& cv = chars->a[i];
            Ctx chc = c.sub("characters").idx(i);
            Character ch;
            ch.id = str(cv, "id", chc, "", true);
            if (!entityIds.insert(ch.id).second) chc.fail("duplicate entity id '" + ch.id + "'");
            ch.layer = str(cv, "layer", chc, s.layers.front().id);
            checkLayer(ch.layer, chc.sub("layer"));
            ch.rig = rigSpec(req(cv, "rig", chc), chc.sub("rig"));
            ch.line = color(cv, "line_color", chc, {0.1f, 0.1f, 0.1f, 1});
            ch.headFill = color(cv, "head_fill", chc, {1, 1, 1, 1});
            ch.accent = color(cv, "accent_color", chc, ch.line);
            ch.face = boolean(cv, "face", chc, true);
            ch.faceStyle = str(cv, "face_style", chc, "profile");
            if (ch.faceStyle != "profile" && ch.faceStyle != "front") chc.sub("face_style").fail("expected profile|front");
            ch.z = num(cv, "z", chc, 0);
            ch.important = boolean(cv, "important", chc, true);
            ch.seed = hashString(ch.id);
            const Value& st = req(cv, "start", chc);
            Ctx sc = chc.sub("start");
            ch.start = {num(st, "x", sc, 0, true), num(st, "y", sc, 0)};
            std::string facing = str(st, "facing", sc, "right");
            if (facing != "left" && facing != "right") sc.sub("facing").fail("expected left|right");
            ch.startFacing = facing == "left" ? -1 : 1;
            ch.startPosture = str(st, "posture", sc, "stand");
            if (ch.startPosture != "stand" && ch.startPosture != "sit" && ch.startPosture != "lie")
                sc.sub("posture").fail("expected stand|sit|lie");
            ch.startSeatY = num(st, "seat_y", sc, 0.45);
            ch.prims = primitives(cv.get("components"), chc.sub("components"), true);
            if (const Value* acts = cv.get("actions")) {
                for (size_t k = 0; k < acts->a.size(); ++k)
                    ch.actions.push_back(action(acts->a[k], chc.sub("actions").idx(k), static_cast<int>(k)));
                std::stable_sort(ch.actions.begin(), ch.actions.end(),
                                 [](const Action& a, const Action& b) { return a.start < b.start; });
            }
            if (const Value* m = cv.get("mouth")) {
                Ctx mc = chc.sub("mouth");
                ch.mouthMode = str(*m, "mode", mc, "none");
                if (ch.mouthMode != "none" && ch.mouthMode != "simple" && ch.mouthMode != "viseme")
                    mc.sub("mode").fail("expected none|simple|viseme");
                static const std::set<std::string> simple = {"closed", "open", "wide"};
                static const std::set<std::string> vis = {"rest", "A", "E", "O", "U", "MBP", "FV", "L"};
                if (const Value* tr = m->get("track")) {
                    double lt = -1;
                    for (size_t k = 0; k < tr->a.size(); ++k) {
                        Ctx kc = mc.sub("track").idx(k);
                        const Value& e = tr->a[k];
                        if (!e.isArray() || e.a.size() != 2 || !e.a[0].isNumber() || !e.a[1].isString())
                            kc.fail("expected [t, state]");
                        if (e.a[0].n < lt) kc.fail("mouth keys must be time-ordered");
                        lt = e.a[0].n;
                        const auto& allowed = ch.mouthMode == "viseme" ? vis : simple;
                        if (!allowed.count(e.a[1].s)) kc.fail("mouth state '" + e.a[1].s + "' invalid for mode " + ch.mouthMode);
                        ch.mouth.push_back({e.a[0].n, e.a[1].s});
                    }
                }
            }
            s.characters.push_back(ch);
        }
    }

    // Cross-reference checks.
    for (const auto& ch : s.characters) {
        for (const auto& a : ch.actions) {
            if (!a.targetCharacter.empty() && !s.findCharacter(a.targetCharacter))
                c.fail("character '" + ch.id + "' action targets unknown character '" + a.targetCharacter + "'");
            if (a.targetCharacter == ch.id && a.type == ActionType::Reach)
                c.fail("character '" + ch.id + "' cannot reach for itself");
            if (!a.prop.empty()) {
                bool found = false;
                for (const auto& p : s.props) found |= p.id == a.prop;
                if (!found) c.fail("character '" + ch.id + "' action references unknown prop '" + a.prop + "'");
            }
        }
    }
    for (const auto& p : s.props)
        for (const auto& e : p.attach)
            if (!e.character.empty() && !s.findCharacter(e.character))
                c.fail("prop '" + p.id + "' attaches to unknown character '" + e.character + "'");
    if (!s.camera.followTarget.empty() && !s.findCharacter(s.camera.followTarget))
        c.fail("camera follows unknown character '" + s.camera.followTarget + "'");
    return s;
}

Scene loadSceneFile(const std::string& path) { return loadScene(json::parseFile(path)); }

}  // namespace kage
