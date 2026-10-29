// Deterministic motion runtime. Every quantity is a pure function of the scene
// and an absolute time value, so frames can be evaluated in any order.
#pragma once
#include <string>
#include <vector>

#include "kage/scene.hpp"

namespace kage {

struct CharacterState {
    Vec2 root;               // world position of the ground point under the pelvis
    int facingSign = 1;      // logical facing after completed turns
    double facingScale = 1;  // animated horizontal scale (passes near 0 mid-turn)
    std::string posture = "stand";
    Pose pose;
    Skeleton skel;           // character-local joint frames
    Affine toWorld;          // character-local -> world
    std::string mouth = "closed";
    bool eyesClosed = false;
    bool walking = false;

    Vec2 jointWorld(int j) const { return toWorld.apply(skel.j[j].pos); }
    // World transform of a joint frame (for bound components). Joint positions
    // follow the animated facing scale, but shapes are only mirrored, never
    // squashed, so heads and accessories stay rigid while a character turns.
    Affine jointFrame(int j) const {
        double mirror = facingScale < 0 ? -1.0 : 1.0;
        return Affine::translate(jointWorld(j)) * Affine::scale(mirror, 1) * Affine::rotate(skel.j[j].rot);
    }
};

struct FootEvent {
    double t;
    std::string character;
    int foot;  // 0 = left, 1 = right
    double x;  // world x of the contact
    std::string kind;  // "step" | "run_step" | "settle"
};

struct ResolvedAttach {
    double t;
    std::string character;  // empty = world
    int joint = -1;
    Vec2 offset;            // joint offset or world position
    bool fromAction = false;
};

class MotionEvaluator {
public:
    explicit MotionEvaluator(const Scene& scene);

    CharacterState evaluate(const Character& ch, double t, bool withIK = true) const;
    std::vector<FootEvent> footEvents(const Character& ch) const;

    // World transform of a prop (local -> world) at time t, including attachment.
    Affine propTransform(const Prop& prop, double t, std::string* attachedTo = nullptr) const;
    const std::vector<ResolvedAttach>& attachments(const std::string& propId) const;

private:
    const Scene& scene_;
    std::vector<std::pair<std::string, std::vector<ResolvedAttach>>> attach_;
};

// Building blocks exposed for tests.
Pose standPose(const RigSpec& r);
Pose sitPose(const RigSpec& r, double seatHeight);
Pose liePose(const RigSpec& r);
void walkLegs(const RigSpec& r, double phase, bool run, Pose& p);
double evalChannel(const std::vector<ChannelKey>& keys, double t);

}  // namespace kage
