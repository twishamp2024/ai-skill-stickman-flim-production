// Tessellation: polygon fill triangulation (concave + holes), polyline stroking,
// and circle/ellipse discretisation. Output is a flat list of colored triangles.
#pragma once
#include <string>
#include <vector>

#include "kage/math.hpp"

namespace kage {

enum class Cap { Butt, Round, Square };
enum class Join { Miter, Round, Bevel };

bool parseCap(const std::string& s, Cap& out);
bool parseJoin(const std::string& s, Join& out);

struct Vertex {
    float x, y;        // pixel coordinates, origin top-left, y down
    float r, g, b, a;  // straight-alpha sRGB
};

struct DrawList {
    std::vector<Vertex> verts;  // every 3 vertices form one triangle, drawn in order
    void addTriangles(const std::vector<Vec2>& tris, const Color& c);
    size_t triangleCount() const { return verts.size() / 3; }
};

// Signed area (positive = counter-clockwise in a y-up frame).
double signedArea(const std::vector<Vec2>& poly);

// Ear-clipping triangulation of a simple polygon with optional simple holes.
// Holes are bridged into the outer contour. Returns false (with message) for
// degenerate or self-intersecting input; triangles are appended to `out` as
// consecutive vertex triples.
bool triangulate(const std::vector<Vec2>& outer, const std::vector<std::vector<Vec2>>& holes,
                 std::vector<Vec2>& out, std::string* error = nullptr);

// True if any two non-adjacent edges of the closed polygon intersect.
bool selfIntersects(const std::vector<Vec2>& poly);

// Expands a polyline into triangles of constant `width`. Opaque output may
// overlap at joins; strokes must therefore be opaque (enforced by validators).
void strokePolyline(const std::vector<Vec2>& pts, bool closed, double width, Cap cap, Join join,
                    std::vector<Vec2>& out, double miterLimit = 4.0);

// Points of an ellipse (or circle when rx == ry) in counter-clockwise order.
std::vector<Vec2> ellipsePoints(Vec2 center, double rx, double ry, double rotationRad, int segments);

// Segment count for a curve of the given on-screen radius; deterministic.
int segmentsForRadius(double radiusPx);

}  // namespace kage
