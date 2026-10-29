#include "kage/rig.hpp"

#include <cmath>

namespace kage {

static const char* kJointNames[J_COUNT] = {
    "pelvis", "chest", "neck", "head",
    "shoulder_l", "elbow_l", "hand_l",
    "shoulder_r", "elbow_r", "hand_r",
    "hip_l", "knee_l", "ankle_l", "toe_l",
    "hip_r", "knee_r", "ankle_r", "toe_r"};

const char* jointName(int j) { return (j >= 0 && j < J_COUNT) ? kJointNames[j] : "?"; }

int jointFromName(const std::string& name) {
    for (int i = 0; i < J_COUNT; ++i)
        if (name == kJointNames[i]) return i;
    return -1;
}

Pose lerpPose(const Pose& a, const Pose& b, double t) { return blendMasked(a, b, t, CH_ALL); }

Pose blendMasked(const Pose& base, const Pose& tg, double w, unsigned mask) {
    Pose r = base;
    if (w <= 0) return r;
    auto L = [w](double x, double y) { return lerp(x, y, w); };
    if (mask & CH_SPINE) r.spine = L(base.spine, tg.spine);
    if (mask & CH_NECK) r.neck = L(base.neck, tg.neck);
    for (int s = 0; s < 2; ++s) {
        if (mask & (s == 0 ? CH_ARM_L : CH_ARM_R)) {
            r.shoulder[s] = L(base.shoulder[s], tg.shoulder[s]);
            r.elbow[s] = L(base.elbow[s], tg.elbow[s]);
        }
        if (mask & (s == 0 ? CH_LEG_L : CH_LEG_R)) {
            r.hip[s] = L(base.hip[s], tg.hip[s]);
            r.knee[s] = L(base.knee[s], tg.knee[s]);
            r.ankle[s] = L(base.ankle[s], tg.ankle[s]);
        }
    }
    if (mask & CH_ROOT) {
        r.tilt = L(base.tilt, tg.tilt);
        r.pelvisY = L(base.pelvisY, tg.pelvisY);
        r.pelvisX = L(base.pelvisX, tg.pelvisX);
        r.groundLock = L(base.groundLock, tg.groundLock);
    }
    return r;
}

namespace {
// Direction of a limb whose frame rotation is `rot` (rest direction points down).
Vec2 limbDir(double rot) { return rotate({0, -1}, rot); }
}  // namespace

double Skeleton::soleY(int side) const {
    const JointFrame& ankle = j[side == 0 ? J_ANKLE_L : J_ANKLE_R];
    const JointFrame& toe = j[side == 0 ? J_TOE_L : J_TOE_R];
    return std::min(ankle.pos.y, toe.pos.y);
}

Skeleton solveFK(const RigSpec& rig, const Pose& p) {
    Skeleton s;
    double rootRot = -deg2rad(p.tilt);
    double torsoRot = rootRot - deg2rad(p.spine);
    double headRot = torsoRot - deg2rad(p.neck);

    Vec2 pelvis{p.pelvisX, p.pelvisY};
    s.j[J_PELVIS] = {pelvis, rootRot};
    Vec2 up = rotate({0, 1}, torsoRot);
    Vec2 chest = pelvis + up * rig.torso;
    s.j[J_CHEST] = {chest, torsoRot};
    s.j[J_NECK] = {chest, torsoRot};
    Vec2 head = chest + rotate({0, 1}, headRot) * (rig.neck + rig.headRadius);
    s.j[J_HEAD] = {head, headRot};

    Vec2 shoulder = chest - up * rig.shoulderDrop;
    for (int side = 0; side < 2; ++side) {
        int base = side == 0 ? J_SHOULDER_L : J_SHOULDER_R;
        double upper = torsoRot + deg2rad(p.shoulder[side]);
        double fore = upper + deg2rad(p.elbow[side]);
        Vec2 elbow = shoulder + limbDir(upper) * rig.upperArm;
        Vec2 hand = elbow + limbDir(fore) * rig.forearm;
        s.j[base] = {shoulder, upper};
        s.j[base + 1] = {elbow, fore};
        s.j[base + 2] = {hand, fore};
    }
    // Ankle height is the vertical gap between the ankle joint and the sole when
    // the foot is flat; the toe frame sits at the front of the sole.
    for (int side = 0; side < 2; ++side) {
        int base = side == 0 ? J_HIP_L : J_HIP_R;
        double thigh = rootRot + deg2rad(p.hip[side]);
        double shin = thigh - deg2rad(p.knee[side]);
        double footRot = deg2rad(p.ankle[side]);  // relative to world horizontal
        Vec2 knee = pelvis + limbDir(thigh) * rig.thigh;
        Vec2 ankle = knee + limbDir(shin) * rig.shin;
        Vec2 heel = ankle + Vec2{0, -rig.ankleHeight};
        Vec2 toe = heel + rotate({rig.foot, 0}, footRot);
        s.j[base] = {pelvis, thigh};
        s.j[base + 1] = {knee, shin};
        s.j[base + 2] = {heel, footRot};
        s.j[base + 3] = {toe, footRot};
    }
    if (p.groundLock > 0) {
        double lowest = std::min(s.soleY(0), s.soleY(1));
        double shift = -lowest * p.groundLock;
        for (auto& jf : s.j) jf.pos.y += shift;
    }
    return s;
}

void solveArmIK(const RigSpec& rig, Vec2 shoulder, double torsoRot, Vec2 target, double& shoulderDeg,
                double& elbowDeg) {
    double a = rig.upperArm, b = rig.forearm;
    Vec2 d = target - shoulder;
    double dist = length(d);
    double reach = a + b - 1e-6;
    double minReach = std::fabs(a - b) + 1e-6;
    double dd = std::min(reach, std::max(minReach, dist));
    // Angle at the shoulder between the target direction and the upper arm.
    double cosA = (a * a + dd * dd - b * b) / (2 * a * dd);
    double alpha = std::acos(std::min(1.0, std::max(-1.0, cosA)));
    double cosE = (a * a + b * b - dd * dd) / (2 * a * b);
    double elbowInterior = std::acos(std::min(1.0, std::max(-1.0, cosE)));
    // World rotation that maps the rest direction (0,-1) to the target direction.
    double targetRot = std::atan2(d.x, -d.y);  // CCW rotation of (0,-1) to d
    // Elbows bend "forward" (positive flexion), so the upper arm rotates back.
    double upper = targetRot - alpha;
    shoulderDeg = rad2deg(upper - torsoRot);
    elbowDeg = rad2deg(kPi - elbowInterior);
    // Normalise into (-180, 180].
    while (shoulderDeg > 180) shoulderDeg -= 360;
    while (shoulderDeg <= -180) shoulderDeg += 360;
}

}  // namespace kage
