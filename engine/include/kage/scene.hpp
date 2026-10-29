// Engine scene description (contract "kage.engine_scene" v1). This is the fully
// resolved, story-agnostic input compiled by the planning tools: every color is
// explicit, every reference resolved, every measurement numeric.
#pragma once
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "kage/geometry.hpp"
#include "kage/json.hpp"
#include "kage/rig.hpp"

namespace kage {

struct SceneError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct PointRef {
    Vec2 p;
    int joint = -1;  // character primitives: joint whose frame `p` is expressed in (-1 = primitive bind)
};

struct Primitive {
    enum class Kind { Polygon, Polyline, Circle, Ellipse, Bone } kind = Kind::Polygon;
    std::vector<PointRef> points;
    std::vector<std::vector<PointRef>> holes;
    bool closed = false;
    Vec2 center;
    double rx = 0, ry = 0, rotation = 0;  // rotation in radians
    int bind = -1;                        // character primitives: default joint frame
    int boneFrom = -1, boneTo = -1;       // Bone primitives
    double t0 = 0, t1 = 1;
    bool hasFill = false, hasStroke = false;
    Color fill, stroke;
    double strokeWidth = 0;
    Cap cap = Cap::Round;
    Join join = Join::Round;
    double z = 0;
    std::string tag;  // "mouth:<state>", "eye_open", "eye_closed", "part:<id>"
};

struct PartAnim {
    enum class Type { None, Oscillate, Spin, Bob } type = Type::None;
    double amplitude = 0;  // degrees (oscillate) or world units (bob)
    double frequency = 0;  // Hz
    double phase = 0;      // cycles
    double degPerSec = 0;  // spin
    Vec2 axis{0, 1};       // bob direction
};

struct Part {
    std::string id;
    Vec2 pivot;
    PartAnim anim;
    std::vector<Primitive> prims;
};

struct TransformKey {
    double t = 0;
    Vec2 pos;
    double rotation = 0;  // degrees
    double scale = 1;
    Ease ease = Ease::InOut;
};

struct AttachEvent {
    double t = 0;
    std::string character;  // empty = detach to world
    int joint = -1;
    Vec2 offset;            // in joint frame when attached; world position when detached
};

struct Prop {
    std::string id, asset, layer;
    std::vector<TransformKey> keys;  // at least one
    bool flip = false;
    Vec2 velocity;                   // constant drift (world units / s)
    double tilePeriod = 0;           // > 0: repeat along x every period
    int tileCount = 0;               // copies on each side
    std::vector<Primitive> prims;
    std::vector<Part> parts;
    std::vector<AttachEvent> attach;  // explicit; pick_up/put_down add more
    double z = 0;
    Vec2 grip;                        // local point held when attached
    bool important = false;          // framing QA should verify visibility
};

enum class ActionType {
    Walk, Run, Turn, Sit, Stand, Fall, Recover, Gesture, Talk, Look, Lean, Reach, PickUp, PutDown, Keyframes, Pose
};
bool parseActionType(const std::string& s, ActionType& out);
const char* actionTypeName(ActionType t);

struct ChannelKey { double t, deg; Ease ease; };

struct Action {
    ActionType type = ActionType::Gesture;
    double start = 0, duration = 0, blendIn = 0.25, blendOut = 0.25;
    bool hold = false;
    std::string gesture;  // wave, point, shrug, nod, head_shake, hands_on_hips, arms_crossed, clap
    int side = 1;         // 0 = left, 1 = right
    double toX = 0;       // walk/run/sit target x
    bool hasToX = false;
    int facing = 1;       // turn
    double seatY = 0.45;  // sit
    double intensity = 1;
    Vec2 targetPoint;     // look/reach world target
    std::string targetCharacter;
    int targetJoint = -1;
    std::string prop;     // pick_up / put_down
    Vec2 placeAt;         // put_down world position
    bool hasPlaceAt = false;
    double spine = 0, neck = 0, shiftX = 0;  // lean
    std::map<std::string, std::vector<ChannelKey>> channels;  // keyframes / pose
    int index = 0;        // order within the character (seed for noise)
};

struct MouthKey { double t; std::string state; };

struct Character {
    std::string id, layer;
    RigSpec rig;
    Color line, headFill, accent;
    bool face = true;
    std::string faceStyle = "profile";  // profile | front
    std::vector<Primitive> prims;       // appearance components
    Vec2 start;
    int startFacing = 1;
    std::string startPosture = "stand";
    double startSeatY = 0.45;
    std::vector<Action> actions;
    std::string mouthMode = "none";     // none | simple | viseme
    std::vector<MouthKey> mouth;
    double z = 0;
    uint32_t seed = 1;
    bool important = true;
};

struct Layer {
    std::string id;
    double order = 0;
    double parallax = 1.0;  // orthographic: fraction of camera motion applied
    double depth = 0.0;     // perspective: distance behind the subject plane
};

struct CameraKey {
    double t = 0;
    Vec2 pos;
    double height = 4.0;  // visible world height
    double roll = 0;      // degrees
    Ease ease = Ease::InOut;
};

struct Camera {
    std::string projection = "orthographic";
    double focalDepth = 10.0;  // perspective: distance from camera to subject plane
    std::vector<CameraKey> keys;
    std::string followTarget;
    Vec2 followOffset;
    double followSmoothing = 0.0;
    bool followX = true, followY = false;
};

struct Rational { int num = 24, den = 1; double value() const { return static_cast<double>(num) / den; } };

struct Scene {
    std::string version, shotId;
    int width = 1280, height = 720;
    Rational fps;
    int frameCount = 0;
    Color background;
    double safeArea = 0.9;
    Camera camera;
    std::vector<Layer> layers;
    std::vector<Prop> props;
    std::vector<Character> characters;
    double timeOffset = 0;  // seconds added to frame time (handles for transitions)

    double frameTime(int frame) const { return timeOffset + frame / fps.value(); }
    const Character* findCharacter(const std::string& id) const;
    const Layer* findLayer(const std::string& id) const;
};

Scene loadScene(const json::Value& root);
Scene loadSceneFile(const std::string& path);

}  // namespace kage
