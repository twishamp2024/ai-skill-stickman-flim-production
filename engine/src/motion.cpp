#include "kage/motion.hpp"

#include <algorithm>
#include <cmath>

namespace kage {

namespace {

double sgn(double v) { return v < 0 ? -1.0 : 1.0; }

double walkRamp(const Action& a) { return std::min(0.4, std::max(0.05, 0.35 / a.duration)); }

double turnDuration(const Action& a) { return std::min(0.3, a.duration * 0.25); }

// Facing scale while turning from f0 to f1 (u in [0,1]); never exactly zero.
double turnScale(int f0, int f1, double u) {
    if (f0 == f1) return f1;
    double s = smoothstep(u);
    double v = f0 * std::cos(kPi * s);
    if (std::fabs(v) < 0.08) v = 0.08 * (s < 0.5 ? f0 : f1);
    return v;
}

// Overlay weight with blend in/out; `hold` keeps full weight after the end.
double overlayWeight(const Action& a, double t) {
    if (t < a.start) return 0;
    double tau = t - a.start;
    double end = a.start + a.duration;
    double w = a.blendIn > 0 ? smoothstep(tau / a.blendIn) : 1.0;
    if (t <= end || a.hold) return w;
    if (a.blendOut <= 0) return 0;
    return w * (1.0 - smoothstep((t - end) / a.blendOut));
}

int priorityClass(ActionType t) {
    switch (t) {
        case ActionType::Walk: case ActionType::Run: return 0;
        case ActionType::PickUp: case ActionType::PutDown: case ActionType::Lean: return 1;
        case ActionType::Reach: return 3;
        default: return 2;
    }
}

bool isStateAction(ActionType t) {
    return t == ActionType::Walk || t == ActionType::Run || t == ActionType::Turn || t == ActionType::Sit ||
           t == ActionType::Stand || t == ActionType::Fall || t == ActionType::Recover;
}

}  // namespace

Pose standPose(const RigSpec& r) {
    Pose p;
    p.spine = r.postureSpine;
    p.neck = r.postureNeck;
    p.shoulder = {-5, 5};
    p.elbow = {6, 10};
    p.hip = {-3, 3};
    p.knee = {2, 2};
    p.pelvisY = r.standPelvisHeight();
    p.groundLock = 1;
    return p;
}

Pose sitPose(const RigSpec& r, double seatHeight) {
    Pose p;
    p.spine = r.postureSpine * 0.6 + 2;
    p.neck = r.postureNeck;
    p.shoulder = {22, 28};
    p.elbow = {50, 58};
    p.pelvisY = seatHeight + r.lineWidth * 0.5;
    p.groundLock = 0;
    for (int s = 0; s < 2; ++s) {
        double hip = s == 0 ? 80 : 86;
        p.hip[s] = hip;
        double kneeY = p.pelvisY - r.thigh * std::cos(deg2rad(hip));
        // Shins hang vertically unless the feet would sink below the floor,
        // in which case they angle forward so the soles rest on the ground.
        double reach = kneeY - r.ankleHeight;
        double theta = reach < r.shin ? std::acos(std::max(-1.0, std::min(1.0, reach / r.shin))) : 0.0;
        p.knee[s] = hip - rad2deg(theta);
        p.ankle[s] = 0;
    }
    return p;
}

Pose liePose(const RigSpec& r) {
    Pose p;
    p.tilt = -88;
    p.spine = 0;
    p.neck = -5;
    p.shoulder = {-40, 50};
    p.elbow = {20, 30};
    p.hip = {-4, 6};
    p.knee = {10, 4};
    p.ankle = {-80, -80};
    p.pelvisY = r.lineWidth * 0.6;
    p.groundLock = 0;
    return p;
}

void walkLegs(const RigSpec& r, double phase, bool run, Pose& p) {
    double S = run ? r.runStride : r.stride;
    double L = r.legLength();
    for (int side = 0; side < 2; ++side) {
        double ph = phase + (side == 1 ? 0.0 : 0.5);
        ph -= std::floor(ph);
        double rel, hip, knee, ankle;
        if (ph < 0.5) {  // stance: foot planted, pelvis travels over it
            rel = S / 4 - (ph / 0.5) * (S / 2);
            hip = rad2deg(std::asin(std::max(-0.95, std::min(0.95, rel / L))));
            knee = run ? 18 * std::sin(kPi * ph / 0.5) : 0;
            ankle = 0;
        } else {  // swing
            double u = (ph - 0.5) / 0.5;
            rel = -S / 4 + (S / 2) * smoothstep(u);
            double flex = (run ? 100.0 : 55.0) * std::sin(kPi * u) * r.energy;
            hip = rad2deg(std::asin(std::max(-0.95, std::min(0.95, rel / L)))) + flex * 0.45;
            knee = flex;
            ankle = -12 * std::sin(kPi * u);
        }
        p.hip[side] = hip;
        p.knee[side] = knee;
        p.ankle[side] = ankle;
        double swing = (run ? 40.0 : r.armSwing) * r.energy;
        p.shoulder[side] = -swing * (rel / (S / 4));
        p.elbow[side] = run ? 85 : 12 + 8 * std::max(0.0, rel / (S / 4));
    }
    p.spine = r.postureSpine + (run ? 14 : 4);
}

double evalChannel(const std::vector<ChannelKey>& keys, double t) {
    if (keys.empty()) return 0;
    if (t <= keys.front().t) return keys.front().deg;
    for (size_t i = 1; i < keys.size(); ++i) {
        if (t <= keys[i].t) {
            double span = keys[i].t - keys[i - 1].t;
            double u = span > 0 ? (t - keys[i - 1].t) / span : 1.0;
            return lerp(keys[i - 1].deg, keys[i].deg, applyEase(keys[i].ease, u));
        }
    }
    return keys.back().deg;
}

MotionEvaluator::MotionEvaluator(const Scene& scene) : scene_(scene) {
    // Resolve prop attachment timelines: explicit events + pick_up/put_down actions.
    for (const auto& prop : scene_.props) {
        std::vector<ResolvedAttach> ev;
        for (const auto& e : prop.attach) ev.push_back({e.t, e.character, e.joint, e.offset, false});
        for (const auto& ch : scene_.characters) {
            for (const auto& a : ch.actions) {
                if (a.prop != prop.id) continue;
                double mid = a.start + a.duration * 0.5;
                if (a.type == ActionType::PickUp) {
                    ev.push_back({mid, ch.id, a.side == 0 ? J_HAND_L : J_HAND_R, {0, 0}, true});
                } else if (a.type == ActionType::PutDown) {
                    ResolvedAttach r{mid, "", -1, a.placeAt, true};
                    if (!a.hasPlaceAt) r.joint = -2;  // place under the hand at event time
                    r.character = "";
                    r.offset = a.placeAt;
                    // remember who put it down for the "under the hand" placement
                    if (!a.hasPlaceAt) r.character = "@" + ch.id + ":" + (a.side == 0 ? "l" : "r");
                    ev.push_back(r);
                }
            }
        }
        std::stable_sort(ev.begin(), ev.end(), [](const ResolvedAttach& a, const ResolvedAttach& b) { return a.t < b.t; });
        attach_.emplace_back(prop.id, ev);
    }
}

const std::vector<ResolvedAttach>& MotionEvaluator::attachments(const std::string& propId) const {
    static const std::vector<ResolvedAttach> empty;
    for (const auto& kv : attach_)
        if (kv.first == propId) return kv.second;
    return empty;
}

CharacterState MotionEvaluator::evaluate(const Character& ch, double t, bool withIK) const {
    const RigSpec& r = ch.rig;
    CharacterState st;
    double x = ch.start.x;
    double groundY = ch.start.y;
    int facing = ch.startFacing;
    double facingScale = facing;
    std::string posture = ch.startPosture;
    double seatY = ch.startSeatY;

    auto postureBase = [&](const std::string& p) {
        if (p == "sit") return sitPose(r, seatY);
        if (p == "lie") return liePose(r);
        return standPose(r);
    };
    Pose trans;
    bool inTransition = false;
    const char* transitionName = "";
    double walkPhase = 0;
    bool walking = false, running = false;
    double walkWeight = 0;

    // Pass 1: persistent state (position, facing, posture).
    for (const auto& a : ch.actions) {
        if (!isStateAction(a.type) || a.start > t) continue;
        double u = clamp01((t - a.start) / a.duration);
        switch (a.type) {
            case ActionType::Walk:
            case ActionType::Run: {
                double x0 = x, x1 = a.toX;
                int dir = x1 > x0 ? 1 : (x1 < x0 ? -1 : facing);
                if (dir != facing) {
                    double td = turnDuration(a);
                    facingScale = turnScale(facing, dir, (t - a.start) / td);
                    facing = dir;
                    if (t - a.start >= td) facingScale = facing;
                } else {
                    facingScale = facing;
                }
                double frac = trapezoid(u, walkRamp(a));
                x = x0 + (x1 - x0) * frac;
                if (t <= a.start + a.duration) {
                    bool run = a.type == ActionType::Run;
                    double S = run ? r.runStride : r.stride;
                    walkPhase = std::fabs(x - x0) / S;
                    walking = true;
                    running = run;
                    double tau = t - a.start;
                    double rin = std::min(0.2, a.duration / 4), rout = std::min(0.25, a.duration / 4);
                    walkWeight = smoothstep(tau / rin) * (1.0 - smoothstep((tau - (a.duration - rout)) / rout));
                }
                break;
            }
            case ActionType::Turn: {
                facingScale = turnScale(facing, a.facing, u);
                if (u >= 1.0) facingScale = a.facing;
                facing = a.facing;
                break;
            }
            case ActionType::Sit: {
                Pose from = postureBase(posture);
                double x0 = x;
                seatY = a.seatY;
                Pose to = sitPose(r, seatY);
                double e = smoothstep(u);
                trans = lerpPose(from, to, e);
                trans.spine += 22 * std::sin(kPi * u);
                if (a.hasToX) x = lerp(x0, a.toX, e);
                inTransition = u < 1.0;
                if (inTransition) transitionName = "to_sit";
                if (u >= 1.0) posture = "sit";
                break;
            }
            case ActionType::Stand: {
                Pose from = postureBase(posture);
                Pose to = standPose(r);
                double e = smoothstep(u);
                trans = lerpPose(from, to, e);
                trans.spine += 28 * std::sin(kPi * u);
                if (a.hasToX) x = lerp(x, a.toX, e);
                inTransition = u < 1.0;
                if (inTransition) transitionName = "to_stand";
                if (u >= 1.0) posture = "stand";
                break;
            }
            case ActionType::Fall: {
                Pose from = postureBase(posture);
                Pose to = liePose(r);
                trans = lerpPose(from, to, applyEase(Ease::In, u));
                trans.shoulder[0] += 70 * std::sin(kPi * u);
                trans.shoulder[1] += 90 * std::sin(kPi * u);
                inTransition = u < 1.0;
                if (inTransition) transitionName = "falling";
                if (u >= 1.0) posture = "lie";
                break;
            }
            case ActionType::Recover: {
                Pose from = postureBase(posture);
                Pose to = standPose(r);
                double e = smoothstep(u);
                trans = lerpPose(from, to, e);
                trans.knee[0] += 70 * std::sin(kPi * u);
                trans.knee[1] += 70 * std::sin(kPi * u);
                trans.hip[0] += 50 * std::sin(kPi * u);
                trans.hip[1] += 50 * std::sin(kPi * u);
                inTransition = u < 1.0;
                if (inTransition) transitionName = "recovering";
                if (u >= 1.0) posture = "stand";
                break;
            }
            default: break;
        }
    }
    Pose pose = inTransition ? trans : postureBase(posture);
    // Idle breathing: a subtle, deterministic sway on top of any posture.
    double breath = std::sin(2 * kPi * t / 3.6 + (ch.seed % 628) / 100.0);
    if (posture != "lie") {
        pose.spine += 1.2 * breath * r.energy;
        pose.shoulder[0] += 1.5 * breath;
        pose.shoulder[1] -= 1.5 * breath;
    }
    if (walking && walkWeight > 0) {
        Pose w = pose;
        walkLegs(r, walkPhase, running, w);
        pose = blendMasked(pose, w, walkWeight, CH_LEGS | CH_ARMS | CH_SPINE);
    }

    // Pass 2: overlays by priority class, then start time.
    std::vector<const Action*> overlays;
    for (const auto& a : ch.actions)
        if (!isStateAction(a.type)) overlays.push_back(&a);
    std::stable_sort(overlays.begin(), overlays.end(), [](const Action* a, const Action* b) {
        int pa = priorityClass(a->type), pb = priorityClass(b->type);
        return pa != pb ? pa < pb : a->start < b->start;
    });

    Skeleton fk;
    bool fkValid = false;
    for (const Action* ap : overlays) {
        const Action& a = *ap;
        double w = overlayWeight(a, t);
        if (w <= 0) continue;
        double tau = std::min(a.duration, std::max(0.0, t - a.start));
        double u = tau / a.duration;
        Pose tg = pose;
        unsigned mask = 0;
        int s = a.side;
        unsigned armMask = s == 0 ? CH_ARM_L : CH_ARM_R;
        double k = a.intensity * r.energy;
        switch (a.type) {
            case ActionType::Gesture: {
                const std::string& g = a.gesture;
                if (g == "wave") {
                    tg.shoulder[s] = 155; tg.elbow[s] = 25 + 28 * std::sin(2 * kPi * 2.2 * tau) * k; mask = armMask;
                } else if (g == "point") {
                    tg.shoulder[s] = 88; tg.elbow[s] = 4; mask = armMask;
                } else if (g == "shrug") {
                    double e = std::sin(kPi * u);
                    tg.shoulder = {25 * e, 25 * e}; tg.elbow = {95 * e, 95 * e}; tg.neck = pose.neck - 10 * e;
                    mask = CH_ARMS | CH_NECK;
                } else if (g == "nod") {
                    tg.neck = pose.neck + 14 * k * std::fabs(std::sin(2 * kPi * 1.4 * tau)); mask = CH_NECK;
                } else if (g == "head_shake") {
                    tg.neck = pose.neck + 6 * k * std::sin(2 * kPi * 2.5 * tau); mask = CH_NECK;
                } else if (g == "hands_on_hips") {
                    tg.shoulder = {-28, -28}; tg.elbow = {105, 105}; mask = CH_ARMS;
                } else if (g == "arms_crossed") {
                    tg.shoulder = {35, 40}; tg.elbow = {110, 115}; mask = CH_ARMS;
                } else if (g == "clap") {
                    tg.shoulder = {60, 60};
                    double c = 18 * std::fabs(std::sin(2 * kPi * 2.0 * tau));
                    tg.elbow = {40 + c, 40 + c}; mask = CH_ARMS;
                }
                break;
            }
            case ActionType::Talk: {
                uint32_t seed = ch.seed ^ hash32(static_cast<uint32_t>(a.index + 1));
                double n1 = valueNoise(seed, tau * 2.2), n2 = valueNoise(seed + 7, tau * 3.1);
                tg.shoulder[s] = pose.shoulder[s] + (22 + 14 * n1) * k;
                tg.elbow[s] = pose.elbow[s] + (45 + 25 * n2) * k;
                tg.neck = pose.neck + 4 * valueNoise(seed + 13, tau * 2.6) * k;
                mask = armMask | CH_NECK;
                break;
            }
            case ActionType::Look: {
                Vec2 target = a.targetPoint;
                if (!a.targetCharacter.empty()) {
                    if (const Character* o = scene_.findCharacter(a.targetCharacter))
                        target = evaluate(*o, t, false).jointWorld(a.targetJoint);
                }
                if (!fkValid) { fk = solveFK(r, pose); fkValid = true; }
                Vec2 headW = Vec2{x, groundY} + Vec2{fk.j[J_HEAD].pos.x * facing, fk.j[J_HEAD].pos.y};
                Vec2 d = target - headW;
                double elev = rad2deg(std::atan2(d.y, std::fabs(d.x) + 1e-6));
                tg.neck = pose.neck - std::max(-35.0, std::min(35.0, elev * 0.8));
                mask = CH_NECK;
                break;
            }
            case ActionType::Lean:
                tg.spine = a.spine; tg.neck = a.neck; tg.pelvisX = pose.pelvisX + a.shiftX;
                mask = CH_SPINE | CH_NECK | CH_ROOT;
                break;
            case ActionType::PickUp:
            case ActionType::PutDown: {
                double b = std::sin(kPi * u);
                tg.spine = pose.spine + 62 * b;
                tg.neck = pose.neck - 10 * b;
                tg.hip = {pose.hip[0] + 30 * b, pose.hip[1] + 30 * b};
                tg.knee = {pose.knee[0] + 45 * b, pose.knee[1] + 45 * b};
                tg.shoulder[s] = pose.shoulder[s] + 25 * b;
                tg.elbow[s] = pose.elbow[s] * (1 - b);
                mask = CH_SPINE | CH_NECK | CH_LEGS | armMask;
                break;
            }
            case ActionType::Keyframes:
            case ActionType::Pose: {
                double lt = a.type == ActionType::Pose ? 0.0 : tau;
                for (const auto& kv : a.channels) {
                    double v = evalChannel(kv.second, lt);
                    const std::string& n = kv.first;
                    if (n == "spine") { tg.spine = v; mask |= CH_SPINE; }
                    else if (n == "neck") { tg.neck = v; mask |= CH_NECK; }
                    else if (n == "tilt") { tg.tilt = v; mask |= CH_ROOT; }
                    else if (n == "pelvis_y") { tg.pelvisY = v; mask |= CH_ROOT; }
                    else if (n == "pelvis_x") { tg.pelvisX = v; mask |= CH_ROOT; }
                    else {
                        int side = n.back() == 'l' ? 0 : 1;
                        std::string j = n.substr(0, n.size() - 2);
                        if (j == "shoulder") { tg.shoulder[side] = v; mask |= side ? CH_ARM_R : CH_ARM_L; }
                        else if (j == "elbow") { tg.elbow[side] = v; mask |= side ? CH_ARM_R : CH_ARM_L; }
                        else if (j == "hip") { tg.hip[side] = v; mask |= side ? CH_LEG_R : CH_LEG_L; }
                        else if (j == "knee") { tg.knee[side] = v; mask |= side ? CH_LEG_R : CH_LEG_L; }
                        else if (j == "ankle") { tg.ankle[side] = v; mask |= side ? CH_LEG_R : CH_LEG_L; }
                    }
                }
                break;
            }
            case ActionType::Reach: {
                if (!withIK) break;
                Vec2 target = a.targetPoint;
                if (!a.targetCharacter.empty()) {
                    if (const Character* o = scene_.findCharacter(a.targetCharacter))
                        target = evaluate(*o, t, false).jointWorld(a.targetJoint);
                }
                Skeleton cur = solveFK(r, pose);
                Vec2 local{(target.x - x) * facing, target.y - groundY};
                double sh, el;
                solveArmIK(r, cur.j[s == 0 ? J_SHOULDER_L : J_SHOULDER_R].pos, cur.j[J_CHEST].rot, local, sh, el);
                tg.shoulder[s] = sh;
                tg.elbow[s] = el;
                mask = armMask;
                break;
            }
            default: break;
        }
        if (mask) {
            pose = blendMasked(pose, tg, w, mask);
            fkValid = false;
        }
    }

    st.root = {x, groundY};
    st.facingSign = facing;
    st.facingScale = facingScale;
    // Report in-progress posture changes explicitly so QA does not judge a
    // half-seated character by standing rules.
    st.posture = inTransition ? transitionName : posture;
    st.pose = pose;
    st.skel = solveFK(r, pose);
    st.toWorld = Affine::translate(st.root) * Affine::scale(facingScale, 1.0);
    st.walking = walking;

    // Mouth: step function over the track.
    st.mouth = ch.mouthMode == "viseme" ? "rest" : "closed";
    for (const auto& m : ch.mouth) {
        if (m.t <= t) st.mouth = m.state;
        else break;
    }
    // Deterministic blinking roughly every four seconds.
    double period = 4.0;
    double k = std::floor(t / period);
    double blinkAt = k * period + 0.4 + hashUnit(ch.seed, static_cast<int32_t>(k)) * 3.0;
    st.eyesClosed = t >= blinkAt && t < blinkAt + 0.12;
    return st;
}

std::vector<FootEvent> MotionEvaluator::footEvents(const Character& ch) const {
    std::vector<FootEvent> out;
    double x = ch.start.x;
    for (const auto& a : ch.actions) {
        if (a.type == ActionType::Sit || a.type == ActionType::Stand) {
            if (a.hasToX) x = a.toX;
            continue;
        }
        if (a.type != ActionType::Walk && a.type != ActionType::Run) continue;
        bool run = a.type == ActionType::Run;
        double S = run ? ch.rig.runStride : ch.rig.stride;
        double D = std::fabs(a.toX - x);
        double ramp = walkRamp(a);
        int steps = static_cast<int>(std::floor(D / (S / 2) + 1e-9));
        for (int k = 1; k <= steps; ++k) {
            double target = k * (S / 2) / D;
            double lo = 0, hi = 1;
            for (int it = 0; it < 60; ++it) {
                double mid = 0.5 * (lo + hi);
                (trapezoid(mid, ramp) < target ? lo : hi) = mid;
            }
            double t = a.start + a.duration * 0.5 * (lo + hi);
            // Contact alternates: right leg plants at phase 0, left at 0.5.
            int foot = (k % 2 == 0) ? 1 : 0;
            out.push_back({t, ch.id, foot, x + sgn(a.toX - x) * k * (S / 2), run ? "run_step" : "step"});
        }
        double rem = D - steps * (S / 2);
        if (rem > S / 8) out.push_back({a.start + a.duration, ch.id, steps % 2 == 0 ? 0 : 1, a.toX, "settle"});
        x = a.toX;
    }
    return out;
}

Affine MotionEvaluator::propTransform(const Prop& prop, double t, std::string* attachedTo) const {
    if (attachedTo) attachedTo->clear();
    Affine flip = prop.flip ? Affine::scale(-1, 1) : Affine();
    const auto& ev = attachments(prop.id);
    const ResolvedAttach* active = nullptr;
    for (const auto& e : ev)
        if (e.t <= t) active = &e;
    if (active) {
        if (!active->character.empty() && active->character[0] != '@') {
            const Character* ch = scene_.findCharacter(active->character);
            if (ch) {
                CharacterState cs = evaluate(*ch, t);
                Vec2 jp = cs.jointWorld(active->joint);
                Vec2 off = cs.toWorld.applyLinear(active->offset);
                if (attachedTo) *attachedTo = active->character + ":" + jointName(active->joint);
                Affine face = Affine::scale(cs.facingScale < 0 ? -1 : 1, 1);
                return Affine::translate(jp + off) * face * flip * Affine::translate(prop.grip * -1.0);
            }
        } else if (!active->character.empty()) {
            // Put down under the releasing hand: "@<character>:<side>".
            std::string spec = active->character.substr(1);
            std::string cid = spec.substr(0, spec.size() - 2);
            int joint = spec.back() == 'l' ? J_HAND_L : J_HAND_R;
            const Character* ch = scene_.findCharacter(cid);
            if (ch) {
                CharacterState cs = evaluate(*ch, active->t);
                Vec2 hand = cs.jointWorld(joint);
                return Affine::translate({hand.x - prop.grip.x, ch->start.y}) * flip;
            }
        } else {
            return Affine::translate(active->offset) * flip;
        }
    }
    // Keyframed transform plus constant drift.
    const auto& keys = prop.keys;
    TransformKey k = keys.front();
    if (t > keys.front().t) {
        k = keys.back();
        for (size_t i = 1; i < keys.size(); ++i) {
            if (t <= keys[i].t) {
                double span = keys[i].t - keys[i - 1].t;
                double u = applyEase(keys[i].ease, span > 0 ? (t - keys[i - 1].t) / span : 1.0);
                k.pos = lerp(keys[i - 1].pos, keys[i].pos, u);
                k.rotation = lerp(keys[i - 1].rotation, keys[i].rotation, u);
                k.scale = lerp(keys[i - 1].scale, keys[i].scale, u);
                break;
            }
        }
    }
    Vec2 drift = prop.velocity * t;
    if (prop.tilePeriod > 0) drift.x = std::fmod(drift.x, prop.tilePeriod);
    return Affine::translate(k.pos + drift) * Affine::rotate(deg2rad(k.rotation)) * Affine::scale(k.scale, k.scale) * flip;
}

}  // namespace kage
