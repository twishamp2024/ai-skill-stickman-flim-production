// Engine unit tests (dependency-free). Each test exercises real engine code;
// nothing is mocked. Skipped tests are reported explicitly with a reason.
#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

#include "kage/frame.hpp"
#include "kage/raster.hpp"

using namespace kage;

namespace {

int g_failures = 0, g_checks = 0;
std::vector<std::string> g_skipped;

#define CHECK(cond)                                                                    \
    do {                                                                               \
        ++g_checks;                                                                    \
        if (!(cond)) { ++g_failures; std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)
#define CHECK_NEAR(a, b, tol) CHECK(std::fabs((a) - (b)) <= (tol))

double triArea(const std::vector<Vec2>& tris) {
    double a = 0;
    for (size_t i = 0; i + 2 < tris.size(); i += 3) a += std::fabs(cross(tris[i + 1] - tris[i], tris[i + 2] - tris[i])) / 2;
    return a;
}

const char* kSceneTemplate = R"({
  "kage_scene_version": "1.0", "shot_id": "TEST",
  "output": {"width": 160, "height": 90, "fps": {"num": 24, "den": 1}, "frame_count": 96, "background": "#ffffff"},
  "layers": [{"id": "bg", "order": 0, "parallax": 0.5}, {"id": "actors", "order": 1}],
  "camera": {"keys": [{"t": 0, "x": 0, "y": 1, "height": 3}]},
  "props": [
    {"id": "box", "layer": "actors", "position": [1.0, 0], "grip": [0, 0.1],
     "primitives": [{"type": "rect", "min": [-0.1, 0], "max": [0.1, 0.2], "fill": "#cc3333"}]},
    {"id": "hill", "layer": "bg",
     "primitives": [{"type": "polygon", "points": [[-6,0],[-2,1.2],[0,0.4],[3,1.5],[6,0],[6,-1],[-6,-1]], "fill": "#88aa66"}]}
  ],
  "characters": [
    {"id": "adult", "layer": "actors", "rig": {"head_radius": 0.12, "torso": 0.52, "thigh": 0.43, "shin": 0.42},
     "start": {"x": -1.5, "facing": "right"}, "line_color": "#222222", "head_fill": "#ffffff",
     "components": [{"type": "polygon", "bind": "head", "points": [[-0.13,0],[0,0.15],[0.13,0]], "fill": "#553311"}],
     "actions": [
       {"type": "walk", "start": 0.5, "duration": 2.0, "to_x": 0.8},
       {"type": "pick_up", "start": 2.6, "duration": 0.8, "prop": "box", "side": "r"}
     ],
     "mouth": {"mode": "simple", "track": [[0, "closed"], [1.0, "open"], [1.5, "closed"]]}},
    {"id": "child", "layer": "actors", "rig": {"head_radius": 0.1, "torso": 0.3, "thigh": 0.24, "shin": 0.22,
     "upper_arm": 0.17, "forearm": 0.15, "stride": 0.8},
     "start": {"x": 2.0, "facing": "left", "posture": "sit", "seat_y": 0.45},
     "actions": [{"type": "turn", "start": 1.0, "duration": 0.5, "facing": "right"}]}
  ]
})";

Scene testScene() { return loadScene(json::parse(kSceneTemplate)); }

void expectSceneError(const std::string& text, const std::string& needle) {
    bool threw = false;
    try {
        loadScene(json::parse(text));
    } catch (const SceneError& e) {
        threw = std::string(e.what()).find(needle) != std::string::npos;
        if (!threw) std::printf("  unexpected message: %s\n", e.what());
    }
    CHECK(threw);
}

std::string replaced(std::string s, const std::string& from, const std::string& to) {
    size_t p = s.find(from);
    if (p != std::string::npos) s.replace(p, from.size(), to);
    return s;
}

void testJson() {
    auto v = json::parse(R"({"a": [1, 2.5e1, -3], "b": {"c": "x\"yé"}, "d": true, "e": null})");
    CHECK(v.get("a")->a.size() == 3);
    CHECK_NEAR(v.get("a")->a[1].n, 25.0, 1e-12);
    CHECK(v.get("b")->get("c")->s == "x\"y\xc3\xa9");
    CHECK(v.get("d")->b);
    CHECK(v.get("e")->isNull());
    bool threw = false;
    try { json::parse("{\"a\": 1,\n \"a\": 2}"); } catch (const json::ParseError& e) {
        threw = std::string(e.what()).find("line 2") != std::string::npos;
    }
    CHECK(threw);
    threw = false;
    try { json::parse("[1, 2,]"); } catch (const json::ParseError&) { threw = true; }
    CHECK(threw);
}

void testTriangulation() {
    std::vector<Vec2> tris;
    std::vector<Vec2> square = {{0, 0}, {2, 0}, {2, 2}, {0, 2}};
    CHECK(triangulate(square, {}, tris));
    CHECK_NEAR(triArea(tris), 4.0, 1e-9);

    // Concave "L" given clockwise (orientation must not matter).
    std::vector<Vec2> L = {{0, 0}, {0, 3}, {1, 3}, {1, 1}, {3, 1}, {3, 0}};
    tris.clear();
    CHECK(triangulate(L, {}, tris));
    CHECK_NEAR(triArea(tris), 5.0, 1e-9);
    CHECK(tris.size() == 12);

    // Star (highly concave) and collinear vertices.
    std::vector<Vec2> star;
    for (int i = 0; i < 10; ++i) {
        double r = i % 2 ? 0.4 : 1.0, a = kPi / 2 + i * kPi / 5;
        star.push_back({r * std::cos(a), r * std::sin(a)});
    }
    tris.clear();
    CHECK(triangulate(star, {}, tris));
    CHECK_NEAR(triArea(tris), std::fabs(signedArea(star)), 1e-9);
    std::vector<Vec2> collinear = {{0, 0}, {1, 0}, {2, 0}, {2, 1}, {0, 1}};
    tris.clear();
    CHECK(triangulate(collinear, {}, tris));
    CHECK_NEAR(triArea(tris), 2.0, 1e-9);

    // Polygon with two holes (window frame).
    std::vector<Vec2> frame = {{0, 0}, {10, 0}, {10, 4}, {0, 4}};
    std::vector<std::vector<Vec2>> holes = {{{1, 1}, {4, 1}, {4, 3}, {1, 3}}, {{6, 1}, {9, 1}, {9, 3}, {6, 3}}};
    tris.clear();
    CHECK(triangulate(frame, holes, tris));
    CHECK_NEAR(triArea(tris), 40.0 - 12.0, 1e-9);
    // No triangle centroid may fall inside a hole.
    bool leak = false;
    for (size_t i = 0; i + 2 < tris.size(); i += 3) {
        Vec2 c = (tris[i] + tris[i + 1] + tris[i + 2]) * (1.0 / 3);
        for (const auto& h : holes)
            if (c.x > h[0].x && c.x < h[1].x && c.y > h[0].y && c.y < h[2].y) leak = true;
    }
    CHECK(!leak);

    // Self-intersecting bow-tie must be rejected with a message.
    std::string err;
    tris.clear();
    CHECK(!triangulate({{0, 0}, {2, 2}, {2, 0}, {0, 2}}, {}, tris, &err));
    CHECK(err.find("self-intersects") != std::string::npos);
}

void testStroke() {
    std::vector<Vec2> tris;
    strokePolyline({{0, 0}, {10, 0}}, false, 2.0, Cap::Butt, Join::Miter, tris);
    CHECK_NEAR(triArea(tris), 20.0, 1e-9);
    tris.clear();
    strokePolyline({{0, 0}, {10, 0}}, false, 2.0, Cap::Square, Join::Miter, tris);
    CHECK_NEAR(triArea(tris), 24.0, 1e-9);
    tris.clear();
    strokePolyline({{0, 0}}, false, 20.0, Cap::Round, Join::Round, tris);
    CHECK(std::fabs(triArea(tris) - kPi * 100) / (kPi * 100) < 0.03);  // 0.2 px chord tolerance
    CHECK(segmentsForRadius(1.0) >= 8);
    CHECK(segmentsForRadius(200.0) > segmentsForRadius(20.0));
}

void testEasing() {
    for (double r : {0.05, 0.2, 0.5}) {
        CHECK_NEAR(trapezoid(0, r), 0.0, 1e-12);
        CHECK_NEAR(trapezoid(1, r), 1.0, 1e-12);
        double prev = 0;
        bool mono = true;
        for (int i = 1; i <= 1000; ++i) {
            double v = trapezoid(i / 1000.0, r);
            if (v < prev - 1e-12 || v - prev > 0.003) mono = false;  // monotonic + continuous
            prev = v;
        }
        CHECK(mono);
    }
    CHECK_NEAR(applyEase(Ease::InOut, 0.5), 0.5, 1e-12);
    CHECK_NEAR(applyEase(Ease::In, 0.5), 0.25, 1e-12);
    std::vector<ChannelKey> keys = {{0, 0, Ease::Linear}, {1, 10, Ease::Linear}, {2, 10, Ease::Step}};
    CHECK_NEAR(evalChannel(keys, 0.5), 5.0, 1e-12);
    CHECK_NEAR(evalChannel(keys, 5.0), 10.0, 1e-12);
}

void testRig() {
    RigSpec r;
    Pose p = standPose(r);
    p.shoulder = {40, -70};
    p.elbow = {30, 90};
    p.hip = {25, -10};
    p.knee = {40, 5};
    p.spine = 15;
    Skeleton s = solveFK(r, p);
    CHECK_NEAR(length(s.j[J_ELBOW_R].pos - s.j[J_SHOULDER_R].pos), r.upperArm, 1e-9);
    CHECK_NEAR(length(s.j[J_HAND_R].pos - s.j[J_ELBOW_R].pos), r.forearm, 1e-9);
    CHECK_NEAR(length(s.j[J_KNEE_L].pos - s.j[J_HIP_L].pos), r.thigh, 1e-9);
    CHECK_NEAR(std::min(s.soleY(0), s.soleY(1)), 0.0, 1e-9);  // ground lock
    // Positive spine lean moves the chest forward (+x).
    CHECK(s.j[J_CHEST].pos.x > s.j[J_PELVIS].pos.x);
    // Positive shoulder swings the arm forward.
    CHECK(s.j[J_ELBOW_L].pos.x > s.j[J_SHOULDER_L].pos.x - 1e-9 + 0.0);

    // Two-bone IK reaches a reachable target.
    Skeleton st = solveFK(r, standPose(r));
    Vec2 sh = st.j[J_SHOULDER_R].pos;
    for (Vec2 target : {sh + Vec2{0.4, -0.2}, sh + Vec2{0.2, 0.3}, sh + Vec2{-0.3, -0.25}}) {
        Pose q = standPose(r);
        solveArmIK(r, sh, st.j[J_CHEST].rot, target, q.shoulder[1], q.elbow[1]);
        Skeleton sq = solveFK(r, q);
        CHECK(length(sq.j[J_HAND_R].pos - target) < 1e-6);
    }
}

void testAgePostures() {
    Scene sc = testScene();
    const Character& adult = sc.characters[0];
    const Character& child = sc.characters[1];
    CHECK(child.rig.height() < adult.rig.height());
    // Head-to-height ratio differs: a child is not a uniformly scaled adult.
    double adultRatio = 2 * adult.rig.headRadius / adult.rig.height();
    double childRatio = 2 * child.rig.headRadius / child.rig.height();
    CHECK(childRatio > adultRatio * 1.2);

    MotionEvaluator m(sc);
    CharacterState cs = m.evaluate(child, 0.0);
    CHECK(cs.posture == "sit");
    CHECK_NEAR(cs.jointWorld(J_PELVIS).y, 0.45 + child.rig.lineWidth * 0.5, 1e-9);
    // The child's short legs dangle above the floor; an adult's would reach it.
    CHECK(std::min(cs.skel.soleY(0), cs.skel.soleY(1)) > 0.05);
    Pose adultSeat = sitPose(adult.rig, 0.45);
    Skeleton as = solveFK(adult.rig, adultSeat);
    CHECK(std::fabs(std::min(as.soleY(0), as.soleY(1))) < 0.02);
}

void testWalkNoSliding() {
    Scene sc = testScene();
    MotionEvaluator m(sc);
    const Character& adult = sc.characters[0];
    // Walk runs t in [0.5, 2.5]; cruise phase avoids ramps and blends.
    double worstSlide = 0;
    int stanceSamples = 0;
    for (double t = 1.0; t < 2.0; t += 1.0 / 240) {
        CharacterState a = m.evaluate(adult, t), b = m.evaluate(adult, t + 1.0 / 240);
        for (int side = 0; side < 2; ++side) {
            int j = side == 0 ? J_ANKLE_L : J_ANKLE_R;
            // A foot is in stance when it touches the ground in both samples.
            double ya = a.jointWorld(j).y, yb = b.jointWorld(j).y;
            if (ya < 1e-6 && yb < 1e-6) {
                worstSlide = std::max(worstSlide, std::fabs(a.jointWorld(j).x - b.jointWorld(j).x));
                ++stanceSamples;
            }
        }
    }
    CHECK(stanceSamples > 100);
    // Sub-millimetre per 1/240 s (trapezoid ramp is excluded from the window).
    CHECK(worstSlide < 1e-3);
    if (worstSlide >= 1e-3) std::printf("  worst slide %.6f\n", worstSlide);

    auto steps = m.footEvents(adult);
    CHECK(steps.size() >= 3);
    for (const auto& e : steps) CHECK(e.t >= 0.5 && e.t <= 2.5 + 1e-9);
    CharacterState end = m.evaluate(adult, 2.55);
    CHECK_NEAR(end.root.x, 0.8, 1e-9);
}

void testTurnAndMouth() {
    Scene sc = testScene();
    MotionEvaluator m(sc);
    const Character& child = sc.characters[1];
    CHECK(m.evaluate(child, 0.9).facingSign == -1);
    for (double t = 1.0; t <= 1.5; t += 0.01) CHECK(std::fabs(m.evaluate(child, t).facingScale) >= 0.08 - 1e-12);
    CHECK_NEAR(m.evaluate(child, 1.6).facingScale, 1.0, 1e-12);
    // Mid-turn, bound shapes (head, hair, accessories) are mirrored, not squashed.
    CharacterState mid = m.evaluate(child, 1.25);
    CHECK(std::fabs(mid.facingScale) < 0.2);
    CHECK_NEAR(std::fabs(mid.jointFrame(J_HEAD).det()), 1.0, 1e-9);
    const Character& adult = sc.characters[0];
    CHECK(m.evaluate(adult, 0.5).mouth == "closed");
    CHECK(m.evaluate(adult, 1.2).mouth == "open");
    CHECK(m.evaluate(adult, 2.0).mouth == "closed");
}

void testAttachment() {
    Scene sc = testScene();
    MotionEvaluator m(sc);
    const Prop& box = sc.props[0];
    std::string to;
    Affine before = m.propTransform(box, 2.9, &to);
    CHECK(to.empty());
    CHECK_NEAR(before.apply({0, 0}).x, 1.0, 1e-9);
    Affine after = m.propTransform(box, 3.2, &to);
    CHECK(to == "adult:hand_r");
    CharacterState cs = m.evaluate(sc.characters[0], 3.2);
    // The grip point coincides with the hand.
    CHECK(length(after.apply(box.grip) - cs.jointWorld(J_HAND_R)) < 1e-9);
}

void testSceneValidation() {
    expectSceneError(replaced(kSceneTemplate, "\"bind\": \"head\"", "\"bind\": \"tail\""), "unknown joint 'tail'");
    expectSceneError(replaced(kSceneTemplate, "\"layer\": \"bg\"", "\"layer\": \"sky\""), "unknown layer 'sky'");
    expectSceneError(replaced(kSceneTemplate, "#cc3333", "red"), "expected color");
    expectSceneError(replaced(kSceneTemplate, "\"prop\": \"box\"", "\"prop\": \"crate\""), "unknown prop 'crate'");
    expectSceneError(replaced(kSceneTemplate, "\"to_x\": 0.8", "\"speed\": 1"), "walk/run requires to_x");
    expectSceneError(replaced(kSceneTemplate, "\"width\": 160", "\"width\": 161"), "must be even");
    expectSceneError(replaced(kSceneTemplate, "[1.5, \"closed\"]", "[1.5, \"O\"]"), "invalid for mode simple");
    expectSceneError(replaced(kSceneTemplate, "\"1.0\", \"shot_id\"", "\"2.0\", \"shot_id\""), "unsupported version");
    expectSceneError(replaced(kSceneTemplate, "\"id\": \"child\"", "\"id\": \"adult\""), "duplicate entity id");
}

uint64_t hashBytes(const std::vector<uint8_t>& v) {
    uint64_t h = 1469598103934665603ULL;
    for (uint8_t b : v) { h ^= b; h *= 1099511628211ULL; }
    return h;
}

void testDeterministicRendering() {
    Scene sc = testScene();
    MotionEvaluator m(sc);
    FrameBuilder fb(sc, m);
    auto r = makeSoftwareRenderer(sc.width, sc.height, 3);
    auto render = [&](int f) {
        DrawList dl;
        fb.build(f, dl);
        std::vector<uint8_t> px;
        r->render(dl, sc.background, px);
        return px;
    };
    std::vector<uint8_t> a = render(40);
    for (int f = 0; f < 40; ++f) render(f);  // sequential pass
    std::vector<uint8_t> b = render(40);
    std::vector<uint8_t> c = render(41);
    CHECK(hashBytes(a) == hashBytes(b));  // out-of-order == in-order
    CHECK(hashBytes(a) != hashBytes(c));  // animation actually changes frames
    // Non-blank: something other than background was drawn.
    size_t nonWhite = 0;
    for (size_t i = 0; i < a.size(); i += 3) nonWhite += (a[i] != 255 || a[i + 1] != 255 || a[i + 2] != 255);
    CHECK(nonWhite > a.size() / 3 / 20);

    // Probe reports both characters fully visible.
    DrawList dl;
    FrameProbe p;
    fb.build(40, dl, &p);
    int chars = 0;
    for (const auto& e : p.entities)
        if (e.kind == "character") { ++chars; CHECK(e.drawn && e.visibleFraction > 0.99); }
    CHECK(chars == 2);
}

void testSoftwareCoverage() {
    auto r = makeSoftwareRenderer(4, 4, 4);
    DrawList dl;
    // Rectangle covering exactly x in [0, 2) and half of column 2.
    std::vector<Vec2> tris = {{0, 0}, {2.5, 0}, {2.5, 4}, {0, 0}, {2.5, 4}, {0, 4}};
    dl.addTriangles(tris, {0, 0, 0, 1});
    std::vector<uint8_t> px;
    r->render(dl, {1, 1, 1, 1}, px);
    CHECK(px[0] == 0);                         // fully covered
    CHECK(px[2 * 3] >= 126 && px[2 * 3] <= 129);  // half covered -> mid grey
    CHECK(px[3 * 3] == 255);                   // uncovered
    // Shared edges are filled exactly once (no double blending seams).
    DrawList half;
    half.addTriangles(tris, {0, 0, 0, 0.5f});
    r->render(half, {1, 1, 1, 1}, px);
    CHECK(px[0] >= 127 && px[0] <= 128);
    CHECK(px[3 * 4 + 3] >= 127 && px[3 * 4 + 3] <= 128);
}

void testStyles() {
    Scene sc = testScene();
    MotionEvaluator m(sc);
    FrameBuilder sil(sc, m, RenderStyle::Silhouette);
    DrawList dl;
    sil.build(10, dl);
    bool allBlack = true;
    for (const auto& v : dl.verts) allBlack &= v.r == 0 && v.g == 0 && v.b == 0;
    CHECK(allBlack);
}

void testGLAgreement() {
    if (!glBackendCompiled()) { g_skipped.push_back("gl_vs_soft: engine built without OpenGL"); return; }
    Scene sc = testScene();
    std::string reason;
    auto gl = makeGLRenderer(sc.width, sc.height, 8, &reason);
    if (!gl) { g_skipped.push_back("gl_vs_soft: no OpenGL context (" + reason + ")"); return; }
    auto soft = makeSoftwareRenderer(sc.width, sc.height, 4);
    MotionEvaluator m(sc);
    FrameBuilder fb(sc, m);
    DrawList dl;
    fb.build(30, dl);
    std::vector<uint8_t> a, b;
    gl->render(dl, sc.background, a);
    soft->render(dl, sc.background, b);
    double diff = 0;
    for (size_t i = 0; i < a.size(); ++i) diff += std::abs(static_cast<int>(a[i]) - static_cast<int>(b[i]));
    double mean = diff / a.size();
    std::printf("  gl vs soft mean abs diff: %.3f (%s)\n", mean, gl->describe().c_str());
    CHECK(mean < 3.0);  // backends differ only in antialiasing sample patterns
}

}  // namespace

int main() {
    struct T { const char* name; std::function<void()> fn; };
    std::vector<T> tests = {
        {"json", testJson}, {"triangulation", testTriangulation}, {"stroke", testStroke},
        {"easing", testEasing}, {"rig_fk_ik", testRig}, {"age_postures", testAgePostures},
        {"walk_no_foot_sliding", testWalkNoSliding}, {"turn_and_mouth", testTurnAndMouth},
        {"prop_attachment", testAttachment}, {"scene_validation", testSceneValidation},
        {"deterministic_rendering", testDeterministicRendering}, {"software_coverage", testSoftwareCoverage},
        {"render_styles", testStyles}, {"gl_vs_soft", testGLAgreement}};
    for (const auto& t : tests) {
        int before = g_failures;
        std::printf("[ RUN  ] %s\n", t.name);
        try {
            t.fn();
        } catch (const std::exception& e) {
            ++g_failures;
            std::printf("  EXCEPTION %s\n", e.what());
        }
        std::printf("[ %s ] %s\n", g_failures == before ? " OK " : "FAIL", t.name);
    }
    for (const auto& s : g_skipped) std::printf("[ SKIP ] %s\n", s.c_str());
    std::printf("%d checks, %d failures, %zu skipped\n", g_checks, g_failures, g_skipped.size());
    return g_failures == 0 ? 0 : 1;
}
