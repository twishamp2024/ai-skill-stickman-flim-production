#include "kage/frame.hpp"

#include <algorithm>
#include <array>
#include <functional>
#include <cmath>
#include <map>

namespace kage {

bool parseRenderStyle(const std::string& s, RenderStyle& out) {
    if (s == "final") out = RenderStyle::Final;
    else if (s == "flat") out = RenderStyle::Flat;
    else if (s == "silhouette") out = RenderStyle::Silhouette;
    else return false;
    return true;
}

CameraState evaluateCamera(const Scene& scene, const MotionEvaluator& motion, double t) {
    const auto& keys = scene.camera.keys;
    CameraState cs;
    CameraKey k = keys.front();
    if (t > keys.front().t) {
        k = keys.back();
        for (size_t i = 1; i < keys.size(); ++i) {
            if (t <= keys[i].t) {
                double span = keys[i].t - keys[i - 1].t;
                double u = applyEase(keys[i].ease, span > 0 ? (t - keys[i - 1].t) / span : 1.0);
                k.pos = lerp(keys[i - 1].pos, keys[i].pos, u);
                k.height = lerp(keys[i - 1].height, keys[i].height, u);
                k.roll = lerp(keys[i - 1].roll, keys[i].roll, u);
                break;
            }
        }
    }
    cs.pos = k.pos;
    cs.height = k.height;
    cs.roll = k.roll;
    if (!scene.camera.followTarget.empty()) {
        const Character* ch = scene.findCharacter(scene.camera.followTarget);
        // Smoothed follow: average of the target over a trailing window. Pure
        // function of t, so random frame access stays deterministic.
        const int samples = 9;
        Vec2 acc;
        for (int i = 0; i < samples; ++i) {
            double ts = t - scene.camera.followSmoothing * i / (samples - 1);
            acc += motion.evaluate(*ch, ts, false).root;
        }
        acc = acc * (1.0 / samples);
        if (scene.camera.followX) cs.pos.x = acc.x + scene.camera.followOffset.x;
        if (scene.camera.followY) cs.pos.y = acc.y + scene.camera.followOffset.y;
    }
    return cs;
}

namespace {

struct WorldPrim {
    double layerOrder, entityZ, primZ;
    int entityIdx, seq;
    const Layer* layer;
    std::vector<Vec2> outer;
    std::vector<std::vector<Vec2>> holes;
    bool closed = true, convex = false;
    bool hasFill = false, hasStroke = false;
    Color fill, stroke;
    double strokeWidth = 0;  // world units
    Cap cap = Cap::Round;
    Join join = Join::Round;
};

Affine layerTransform(const Scene& scene, const Layer& layer, const CameraState& cam, double* pxPerUnit) {
    double s = 1.0, p = layer.parallax, py = layer.parallaxY;
    if (scene.camera.projection == "perspective") {
        s = scene.camera.focalDepth / std::max(1e-3, scene.camera.focalDepth + layer.depth);
        p = py = 1.0;
    }
    double ppu = scene.height / cam.height * s;
    if (pxPerUnit) *pxPerUnit = ppu;
    return Affine::translate({scene.width / 2.0, scene.height / 2.0}) * Affine::scale(1, -1) *
           Affine::rotate(-deg2rad(cam.roll)) * Affine::scale(ppu, ppu) * Affine::translate({-cam.pos.x * p, -cam.pos.y * py});
}

Color styleColor(RenderStyle style, const Color& c) {
    if (style == RenderStyle::Silhouette) return {0, 0, 0, c.a};
    return c;
}

}  // namespace

void FrameBuilder::build(int frame, DrawList& out, FrameProbe* probe) const {
    double t = scene_.frameTime(frame);
    CameraState cam = evaluateCamera(scene_, motion_, t);
    std::map<std::string, Affine> layerXf;
    std::map<std::string, double> layerPpu;
    for (const auto& l : scene_.layers) {
        double ppu;
        layerXf[l.id] = layerTransform(scene_, l, cam, &ppu);
        layerPpu[l.id] = ppu;
    }

    std::vector<WorldPrim> prims;
    std::vector<EntityProbe> ents;
    int seq = 0;

    auto addPrim = [&](WorldPrim wp) { wp.seq = seq++; prims.push_back(std::move(wp)); };

    // Converts an authored primitive (already resolved to world space via the
    // provided point mapper) into a WorldPrim.
    auto emitAuthored = [&](const Primitive& p, const Layer* layer, double entityZ, int entityIdx,
                            const std::function<Vec2(const PointRef&)>& mapPoint, const Affine& shapeXf,
                            double strokeScale) {
        WorldPrim wp;
        wp.layerOrder = layer->order;
        wp.entityZ = entityZ;
        wp.primZ = p.z;
        wp.entityIdx = entityIdx;
        wp.layer = layer;
        wp.hasFill = p.hasFill;
        wp.hasStroke = p.hasStroke;
        wp.fill = p.fill;
        wp.stroke = p.stroke;
        wp.strokeWidth = p.strokeWidth * strokeScale;
        wp.cap = p.cap;
        wp.join = p.join;
        switch (p.kind) {
            case Primitive::Kind::Polygon:
            case Primitive::Kind::Polyline:
                for (const auto& pr : p.points) wp.outer.push_back(mapPoint(pr));
                for (const auto& h : p.holes) {
                    std::vector<Vec2> hole;
                    for (const auto& pr : h) hole.push_back(mapPoint(pr));
                    wp.holes.push_back(hole);
                }
                wp.closed = p.closed;
                break;
            case Primitive::Kind::Circle:
            case Primitive::Kind::Ellipse: {
                double scale = std::sqrt(std::fabs(shapeXf.det())) * layerPpu[layer->id];
                int seg = segmentsForRadius(std::max(p.rx, p.ry) * scale);
                for (const Vec2& q : ellipsePoints(p.center, p.rx, p.ry, p.rotation, seg)) wp.outer.push_back(shapeXf.apply(q));
                wp.closed = true;
                wp.convex = true;
                break;
            }
            case Primitive::Kind::Bone:
                break;  // handled by caller
        }
        addPrim(std::move(wp));
    };

    // ---- Props --------------------------------------------------------------
    for (size_t pi = 0; pi < scene_.props.size(); ++pi) {
        const Prop& prop = scene_.props[pi];
        const Layer* layer = scene_.findLayer(prop.layer);
        EntityProbe ep;
        ep.id = prop.id;
        ep.kind = "prop";
        ep.important = prop.important;
        Affine base = motion_.propTransform(prop, t, &ep.attachedTo);
        int entityIdx = static_cast<int>(ents.size());
        ents.push_back(ep);
        int copies = prop.tilePeriod > 0 ? prop.tileCount : 0;
        for (int c = -copies; c <= copies; ++c) {
            Affine xf = Affine::translate({c * prop.tilePeriod, 0}) * base;
            double sscale = std::sqrt(std::fabs(xf.det()));
            for (const auto& p : prop.prims)
                emitAuthored(p, layer, prop.z, entityIdx, [&](const PointRef& r) { return xf.apply(r.p); }, xf, sscale);
            for (const auto& part : prop.parts) {
                Affine local;
                const PartAnim& an = part.anim;
                double ang = 0;
                Vec2 off;
                if (an.type == PartAnim::Type::Oscillate)
                    ang = deg2rad(an.amplitude * std::sin(2 * kPi * (an.frequency * t + an.phase)));
                else if (an.type == PartAnim::Type::Spin)
                    ang = deg2rad(an.degPerSec * t);
                else if (an.type == PartAnim::Type::Bob)
                    off = an.axis * (an.amplitude * std::sin(2 * kPi * (an.frequency * t + an.phase)));
                local = Affine::translate(part.pivot + off) * Affine::rotate(ang) * Affine::translate(part.pivot * -1.0);
                Affine pxf = xf * local;
                for (const auto& p : part.prims)
                    emitAuthored(p, layer, prop.z, entityIdx, [&](const PointRef& r) { return pxf.apply(r.p); }, pxf, sscale);
            }
        }
    }

    // ---- Characters -----------------------------------------------------------
    for (const auto& ch : scene_.characters) {
        const Layer* layer = scene_.findLayer(ch.layer);
        CharacterState cs = motion_.evaluate(ch, t);
        const RigSpec& r = ch.rig;
        int entityIdx = static_cast<int>(ents.size());
        EntityProbe ep;
        ep.id = ch.id;
        ep.kind = "character";
        ep.important = ch.important;
        ep.root = cs.root;
        ep.head = cs.jointWorld(J_HEAD);
        ep.handL = cs.jointWorld(J_HAND_L);
        ep.handR = cs.jointWorld(J_HAND_R);
        ep.facing = cs.facingSign;
        ep.posture = cs.posture;
        ep.mouth = cs.mouth;
        ep.soleY[0] = cs.toWorld.apply({0, cs.skel.soleY(0)}).y;
        ep.soleY[1] = cs.toWorld.apply({0, cs.skel.soleY(1)}).y;
        {
            Vec2 hp = layerXf[layer->id].apply(ep.head);
            ep.headPx[0] = hp.x;
            ep.headPx[1] = hp.y;
            ep.headPx[2] = r.headRadius * layerPpu[layer->id];
        }
        ents.push_back(ep);

        auto limb = [&](std::initializer_list<int> joints, double z) {
            WorldPrim wp;
            wp.layerOrder = layer->order; wp.entityZ = ch.z; wp.primZ = z; wp.entityIdx = entityIdx; wp.layer = layer;
            for (int j : joints) wp.outer.push_back(cs.jointWorld(j));
            wp.closed = false;
            wp.hasStroke = true;
            wp.stroke = ch.line;
            wp.strokeWidth = r.lineWidth;
            addPrim(std::move(wp));
        };
        limb({J_SHOULDER_L, J_ELBOW_L, J_HAND_L}, -3);
        limb({J_HIP_L, J_KNEE_L, J_ANKLE_L, J_TOE_L}, -2);
        {
            // Torso + neck up to the bottom of the head circle.
            Vec2 head = cs.skel.j[J_HEAD].pos, chest = cs.skel.j[J_CHEST].pos;
            Vec2 neckTop = head + normalize(chest - head) * r.headRadius;
            WorldPrim wp;
            wp.layerOrder = layer->order; wp.entityZ = ch.z; wp.primZ = 0; wp.entityIdx = entityIdx; wp.layer = layer;
            wp.outer = {cs.jointWorld(J_PELVIS), cs.jointWorld(J_CHEST), cs.toWorld.apply(neckTop)};
            wp.closed = false;
            wp.hasStroke = true;
            wp.stroke = ch.line;
            wp.strokeWidth = r.lineWidth;
            addPrim(std::move(wp));
        }
        limb({J_HIP_R, J_KNEE_R, J_ANKLE_R, J_TOE_R}, 1);
        limb({J_SHOULDER_R, J_ELBOW_R, J_HAND_R}, 3);
        {
            WorldPrim wp;
            wp.layerOrder = layer->order; wp.entityZ = ch.z; wp.primZ = 2; wp.entityIdx = entityIdx; wp.layer = layer;
            Affine hf = cs.jointFrame(J_HEAD);
            int seg = segmentsForRadius(r.headRadius * layerPpu[layer->id]);
            for (const Vec2& q : ellipsePoints({0, 0}, r.headRadius, r.headRadius, 0, seg)) wp.outer.push_back(hf.apply(q));
            wp.convex = true;
            wp.hasFill = true;
            wp.fill = ch.headFill;
            wp.hasStroke = true;
            wp.stroke = ch.line;
            wp.strokeWidth = r.lineWidth * 0.8;
            addPrim(std::move(wp));
        }
        if (ch.face && style_ == RenderStyle::Final) {
            Affine hf = cs.jointFrame(J_HEAD);
            double R = r.headRadius;
            bool front = ch.faceStyle == "front";
            auto dot = [&](Vec2 c, double rx, double ry, double z) {
                Primitive p;
                p.kind = Primitive::Kind::Ellipse;
                p.center = c; p.rx = rx; p.ry = ry;
                p.hasFill = true; p.fill = ch.line; p.z = z;
                emitAuthored(p, layer, ch.z, entityIdx, [&](const PointRef& q) { return hf.apply(q.p); }, hf, 1.0);
            };
            auto line = [&](Vec2 a, Vec2 b, double w, double z) {
                WorldPrim wp;
                wp.layerOrder = layer->order; wp.entityZ = ch.z; wp.primZ = z; wp.entityIdx = entityIdx; wp.layer = layer;
                wp.outer = {hf.apply(a), hf.apply(b)};
                wp.closed = false; wp.hasStroke = true; wp.stroke = ch.line; wp.strokeWidth = w;
                addPrim(std::move(wp));
            };
            std::vector<double> eyeX = front ? std::vector<double>{-0.33 * R, 0.33 * R} : std::vector<double>{0.42 * R};
            for (double ex : eyeX) {
                if (cs.eyesClosed) line({ex - 0.1 * R, 0.16 * R}, {ex + 0.1 * R, 0.16 * R}, 0.06 * R, 2.5);
                else dot({ex, 0.18 * R}, 0.1 * R, 0.12 * R, 2.5);
            }
            double mx = front ? 0.0 : 0.5 * R, my = -0.4 * R;
            const std::string& m = cs.mouth;
            if (m == "closed" || m == "rest" || m == "MBP") line({mx - 0.17 * R, my}, {mx + 0.17 * R, my + 0.02 * R}, 0.07 * R, 2.5);
            else if (m == "FV") { line({mx - 0.15 * R, my}, {mx + 0.15 * R, my}, 0.07 * R, 2.5); dot({mx, my - 0.06 * R}, 0.08 * R, 0.03 * R, 2.5); }
            else {
                double rx = 0.14 * R, ry = 0.09 * R;
                if (m == "wide" || m == "A") { rx = 0.16 * R; ry = 0.16 * R; }
                else if (m == "E") { rx = 0.2 * R; ry = 0.07 * R; }
                else if (m == "O") { rx = 0.1 * R; ry = 0.14 * R; }
                else if (m == "U") { rx = 0.07 * R; ry = 0.08 * R; }
                else if (m == "L") { rx = 0.14 * R; ry = 0.1 * R; }
                dot({mx, my}, rx, ry, 2.5);
            }
        }
        // Appearance components bound to joints.
        for (const auto& p : ch.prims) {
            if (p.kind == Primitive::Kind::Bone) {
                Vec2 a = cs.jointWorld(p.boneFrom), b = cs.jointWorld(p.boneTo);
                WorldPrim wp;
                wp.layerOrder = layer->order; wp.entityZ = ch.z; wp.primZ = p.z; wp.entityIdx = entityIdx; wp.layer = layer;
                wp.outer = {lerp(a, b, p.t0), lerp(a, b, p.t1)};
                wp.closed = false;
                wp.hasStroke = true;
                wp.stroke = p.stroke;
                wp.strokeWidth = p.strokeWidth;
                wp.cap = p.cap;
                addPrim(std::move(wp));
                continue;
            }
            Affine shapeXf = p.bind >= 0 ? cs.jointFrame(p.bind) : cs.toWorld;
            emitAuthored(p, layer, ch.z, entityIdx, [&](const PointRef& q) {
                int j = q.joint >= 0 ? q.joint : p.bind;
                return j >= 0 ? cs.jointFrame(j).apply(q.p) : cs.toWorld.apply(q.p);
            }, shapeXf, 1.0);
        }
    }

    std::stable_sort(prims.begin(), prims.end(), [](const WorldPrim& a, const WorldPrim& b) {
        if (a.layerOrder != b.layerOrder) return a.layerOrder < b.layerOrder;
        if (a.entityZ != b.entityZ) return a.entityZ < b.entityZ;
        if (a.entityIdx != b.entityIdx) return a.entityIdx < b.entityIdx;
        if (a.primZ != b.primZ) return a.primZ < b.primZ;
        return a.seq < b.seq;
    });

    std::vector<std::array<double, 4>> bbox(ents.size(), {1e300, 1e300, -1e300, -1e300});
    std::vector<Vec2> tris;
    for (const auto& wp : prims) {
        const Affine& xf = layerXf[wp.layer->id];
        double ppu = layerPpu[wp.layer->id];
        size_t before = out.verts.size();
        std::vector<Vec2> px;
        px.reserve(wp.outer.size());
        for (const Vec2& v : wp.outer) px.push_back(xf.apply(v));
        bool dropOutline = style_ == RenderStyle::Flat && wp.hasFill;
        if (wp.hasFill) {
            tris.clear();
            if (wp.convex) {
                for (size_t i = 1; i + 1 < px.size(); ++i) tris.insert(tris.end(), {px[0], px[i], px[i + 1]});
            } else {
                std::vector<std::vector<Vec2>> holes;
                for (const auto& h : wp.holes) {
                    std::vector<Vec2> hp;
                    for (const Vec2& v : h) hp.push_back(xf.apply(v));
                    holes.push_back(hp);
                }
                std::string err;
                if (!triangulate(px, holes, tris, &err))
                    throw SceneError("frame " + std::to_string(frame) + ": entity '" + ents[static_cast<size_t>(wp.entityIdx)].id +
                                     "' polygon fill failed: " + err);
            }
            out.addTriangles(tris, styleColor(style_, wp.fill));
        }
        if (wp.hasStroke && !dropOutline) {
            tris.clear();
            strokePolyline(px, wp.closed, wp.strokeWidth * ppu, wp.cap, wp.join, tris);
            out.addTriangles(tris, styleColor(style_, wp.stroke));
        }
        auto& bb = bbox[static_cast<size_t>(wp.entityIdx)];
        for (size_t i = before; i < out.verts.size(); ++i) {
            bb[0] = std::min(bb[0], static_cast<double>(out.verts[i].x));
            bb[1] = std::min(bb[1], static_cast<double>(out.verts[i].y));
            bb[2] = std::max(bb[2], static_cast<double>(out.verts[i].x));
            bb[3] = std::max(bb[3], static_cast<double>(out.verts[i].y));
        }
    }

    if (probe) {
        probe->frame = frame;
        probe->t = t;
        probe->camera = cam;
        probe->triangles = out.triangleCount();
        double W = scene_.width, H = scene_.height;
        double mxs = W * (1 - scene_.safeArea) / 2, mys = H * (1 - scene_.safeArea) / 2;
        for (size_t i = 0; i < ents.size(); ++i) {
            EntityProbe& e = ents[i];
            auto& bb = bbox[i];
            if (bb[0] > bb[2]) { e.drawn = false; continue; }
            e.drawn = true;
            std::copy(bb.begin(), bb.end(), e.bbox);
            double area = std::max(1e-9, (bb[2] - bb[0]) * (bb[3] - bb[1]));
            double ix = std::max(0.0, std::min(W, bb[2]) - std::max(0.0, bb[0]));
            double iy = std::max(0.0, std::min(H, bb[3]) - std::max(0.0, bb[1]));
            e.visibleFraction = std::min(1.0, ix * iy / area);
            e.inSafeArea = bb[0] >= mxs && bb[1] >= mys && bb[2] <= W - mxs && bb[3] <= H - mys;
        }
        probe->entities = ents;
    }
}

}  // namespace kage
