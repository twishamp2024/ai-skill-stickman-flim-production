// Frame composition: evaluates camera, layers, props and characters at one
// absolute frame index and emits an ordered triangle list plus probe data.
#pragma once
#include <string>
#include <vector>

#include "kage/geometry.hpp"
#include "kage/motion.hpp"
#include "kage/scene.hpp"

namespace kage {

enum class RenderStyle { Final, Flat, Silhouette };
bool parseRenderStyle(const std::string& s, RenderStyle& out);

struct CameraState {
    Vec2 pos;
    double height = 4;
    double roll = 0;
};
CameraState evaluateCamera(const Scene& scene, const MotionEvaluator& motion, double t);

struct EntityProbe {
    std::string id, kind;  // kind: character | prop
    bool drawn = false;
    double bbox[4] = {0, 0, 0, 0};  // pixel x0, y0, x1, y1
    double visibleFraction = 0;     // bbox area inside the frame / bbox area
    bool inSafeArea = false;
    bool important = false;
    // characters only
    Vec2 root, head, handL, handR;
    int facing = 1;
    std::string posture, mouth, attachedTo;
    double soleY[2] = {0, 0};
};

struct FrameProbe {
    int frame = 0;
    double t = 0;
    CameraState camera;
    std::vector<EntityProbe> entities;
    size_t triangles = 0;
};

class FrameBuilder {
public:
    FrameBuilder(const Scene& scene, const MotionEvaluator& motion, RenderStyle style = RenderStyle::Final)
        : scene_(scene), motion_(motion), style_(style) {}
    // Throws SceneError on invalid geometry (e.g. self-intersecting polygons).
    void build(int frame, DrawList& out, FrameProbe* probe = nullptr) const;

private:
    const Scene& scene_;
    const MotionEvaluator& motion_;
    RenderStyle style_;
};

}  // namespace kage
