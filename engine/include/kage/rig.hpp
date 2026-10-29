// Shared character skeleton used by every character regardless of gender
// presentation or age. Age/body differences are expressed only through RigSpec
// proportions and posture; presentation is expressed through bound components.
#pragma once
#include <array>
#include <string>

#include "kage/math.hpp"

namespace kage {

enum Joint : int {
    J_PELVIS, J_CHEST, J_NECK, J_HEAD,
    J_SHOULDER_L, J_ELBOW_L, J_HAND_L,
    J_SHOULDER_R, J_ELBOW_R, J_HAND_R,
    J_HIP_L, J_KNEE_L, J_ANKLE_L, J_TOE_L,
    J_HIP_R, J_KNEE_R, J_ANKLE_R, J_TOE_R,
    J_COUNT
};

const char* jointName(int j);
int jointFromName(const std::string& name);  // -1 if unknown

// Lengths in world units (metres by convention). Measured from the compiled
// character profile; never derived from gender.
struct RigSpec {
    double headRadius = 0.12;
    double neck = 0.05;
    double torso = 0.52;
    double shoulderDrop = 0.06;  // shoulder pivot distance below the neck base
    double upperArm = 0.30;
    double forearm = 0.27;
    double handRadius = 0.035;
    double thigh = 0.43;
    double shin = 0.42;
    double foot = 0.12;
    double ankleHeight = 0.05;
    double lineWidth = 0.05;     // body stroke width
    double postureSpine = 0.0;   // degrees, positive = forward lean
    double postureNeck = 0.0;    // degrees, positive = chin down
    double stride = 1.3;         // full walk cycle length (two steps), metres
    double runStride = 2.2;
    double armSwing = 22.0;      // degrees
    double energy = 1.0;         // motion amplitude multiplier (age/personality)

    double legLength() const { return thigh + shin; }
    double standPelvisHeight() const { return thigh + shin + ankleHeight; }
    double height() const { return standPelvisHeight() + torso + neck + 2.0 * headRadius; }
};

// Joint angles in degrees. Sign conventions (character facing +x):
//   spine/neck/tilt: positive = lean forward
//   shoulder/hip:    positive = limb swings forward
//   elbow:           positive = forearm folds forward/up (flexion)
//   knee:            positive = shin folds backward (flexion)
//   ankle:           positive = toes up
struct Pose {
    double spine = 0, neck = 0, tilt = 0;
    std::array<double, 2> shoulder{0, 0}, elbow{0, 0}, hip{0, 0}, knee{0, 0}, ankle{0, 0};
    double pelvisY = 0;     // pelvis height above the root (ground) point
    double pelvisX = 0;     // horizontal pelvis offset from the root point
    double groundLock = 1;  // 1 = shift vertically so the lowest sole touches y=0
};

Pose lerpPose(const Pose& a, const Pose& b, double t);

// Channel masks let actions override only the body parts they drive.
enum Channel : unsigned {
    CH_SPINE = 1u << 0, CH_NECK = 1u << 1, CH_ARM_L = 1u << 2, CH_ARM_R = 1u << 3,
    CH_LEG_L = 1u << 4, CH_LEG_R = 1u << 5, CH_ROOT = 1u << 6,
    CH_ARMS = CH_ARM_L | CH_ARM_R, CH_LEGS = CH_LEG_L | CH_LEG_R,
    CH_ALL = 0x7Fu
};
Pose blendMasked(const Pose& base, const Pose& target, double w, unsigned mask);

struct JointFrame {
    Vec2 pos;          // character-local position (facing +x, y up, root at origin)
    double rot = 0;    // counter-clockwise rotation (radians) of the joint's frame
};

struct Skeleton {
    std::array<JointFrame, J_COUNT> j;
    double soleY(int side) const;  // lowest point of the foot
};

// Forward kinematics; applies the ground lock.
Skeleton solveFK(const RigSpec& rig, const Pose& pose);

// Analytic two-bone IK: returns shoulder/elbow angles (degrees, pose convention)
// placing the hand at `target` (character-local). Unreachable targets are
// approached along the straight line. `torsoRot` is the chest frame rotation.
void solveArmIK(const RigSpec& rig, Vec2 shoulder, double torsoRot, Vec2 target, double& shoulderDeg,
                double& elbowDeg);

}  // namespace kage
