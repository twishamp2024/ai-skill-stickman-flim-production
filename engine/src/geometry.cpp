#include "kage/geometry.hpp"

#include <algorithm>
#include <cmath>
#include <list>

namespace kage {

bool parseCap(const std::string& s, Cap& out) {
    if (s == "butt") out = Cap::Butt;
    else if (s == "round") out = Cap::Round;
    else if (s == "square") out = Cap::Square;
    else return false;
    return true;
}

bool parseJoin(const std::string& s, Join& out) {
    if (s == "miter") out = Join::Miter;
    else if (s == "round") out = Join::Round;
    else if (s == "bevel") out = Join::Bevel;
    else return false;
    return true;
}

void DrawList::addTriangles(const std::vector<Vec2>& tris, const Color& c) {
    verts.reserve(verts.size() + tris.size());
    for (const Vec2& p : tris)
        verts.push_back({static_cast<float>(p.x), static_cast<float>(p.y), c.r, c.g, c.b, c.a});
}

double signedArea(const std::vector<Vec2>& poly) {
    double a = 0;
    for (size_t i = 0, n = poly.size(); i < n; ++i) a += cross(poly[i], poly[(i + 1) % n]);
    return a * 0.5;
}

namespace {

constexpr double kEps = 1e-9;

int orient(Vec2 a, Vec2 b, Vec2 c) {
    double v = cross(b - a, c - a);
    double scale = std::max({std::fabs(b.x - a.x), std::fabs(b.y - a.y), std::fabs(c.x - a.x), std::fabs(c.y - a.y), 1.0});
    if (v > kEps * scale * scale) return 1;
    if (v < -kEps * scale * scale) return -1;
    return 0;
}

bool onSegment(Vec2 a, Vec2 b, Vec2 p) {
    return std::min(a.x, b.x) - kEps <= p.x && p.x <= std::max(a.x, b.x) + kEps &&
           std::min(a.y, b.y) - kEps <= p.y && p.y <= std::max(a.y, b.y) + kEps;
}

bool segmentsIntersect(Vec2 p1, Vec2 p2, Vec2 q1, Vec2 q2) {
    int o1 = orient(p1, p2, q1), o2 = orient(p1, p2, q2), o3 = orient(q1, q2, p1), o4 = orient(q1, q2, p2);
    if (o1 != o2 && o3 != o4 && o1 != 0 && o2 != 0 && o3 != 0 && o4 != 0) return true;
    if (o1 == 0 && onSegment(p1, p2, q1)) return true;
    if (o2 == 0 && onSegment(p1, p2, q2)) return true;
    if (o3 == 0 && onSegment(q1, q2, p1)) return true;
    if (o4 == 0 && onSegment(q1, q2, p2)) return true;
    return false;
}

// Point strictly inside or on the boundary of CCW triangle abc.
bool inTriangle(Vec2 p, Vec2 a, Vec2 b, Vec2 c) {
    return orient(a, b, p) >= 0 && orient(b, c, p) >= 0 && orient(c, a, p) >= 0;
}

std::vector<Vec2> dedupe(const std::vector<Vec2>& in) {
    std::vector<Vec2> out;
    for (const Vec2& p : in)
        if (out.empty() || length(p - out.back()) > 1e-9) out.push_back(p);
    while (out.size() > 1 && length(out.front() - out.back()) <= 1e-9) out.pop_back();
    return out;
}

// Bridge `hole` (clockwise) into `outer` (counter-clockwise) using the
// rightmost-vertex visibility method.
bool bridgeHole(std::vector<Vec2>& outer, const std::vector<Vec2>& hole) {
    size_t mi = 0;
    for (size_t i = 1; i < hole.size(); ++i)
        if (hole[i].x > hole[mi].x || (hole[i].x == hole[mi].x && hole[i].y < hole[mi].y)) mi = i;
    Vec2 M = hole[mi];

    // Cast a ray from M toward +x and find the closest outer edge it hits.
    double bestX = 1e300;
    int bestEdge = -1;
    Vec2 I;
    size_t n = outer.size();
    for (size_t i = 0; i < n; ++i) {
        Vec2 a = outer[i], b = outer[(i + 1) % n];
        if ((a.y > M.y) == (b.y > M.y) && !(a.y == M.y || b.y == M.y)) continue;
        if (a.y == b.y) {
            if (a.y != M.y) continue;
            double x = std::min(a.x, b.x);
            if (x >= M.x && x < bestX) { bestX = x; bestEdge = static_cast<int>(i); I = {x, M.y}; }
            continue;
        }
        double t = (M.y - a.y) / (b.y - a.y);
        if (t < 0 || t > 1) continue;
        double x = a.x + t * (b.x - a.x);
        if (x >= M.x && x < bestX) { bestX = x; bestEdge = static_cast<int>(i); I = {x, M.y}; }
    }
    if (bestEdge < 0) return false;

    size_t ia = static_cast<size_t>(bestEdge), ib = (ia + 1) % n;
    size_t pi = outer[ia].x > outer[ib].x ? ia : ib;
    if (length(outer[ia] - I) < 1e-12) pi = ia;
    else if (length(outer[ib] - I) < 1e-12) pi = ib;
    else {
        // Any reflex vertex inside triangle (M, I, P) blocks visibility; pick the one
        // with the smallest angle to the ray.
        Vec2 P = outer[pi];
        Vec2 t0 = M, t1 = I, t2 = P;
        if (orient(t0, t1, t2) < 0) std::swap(t1, t2);
        double bestAngle = 1e300;
        for (size_t i = 0; i < n; ++i) {
            if (i == pi) continue;
            Vec2 prev = outer[(i + n - 1) % n], cur = outer[i], next = outer[(i + 1) % n];
            if (orient(prev, cur, next) > 0) continue;  // convex: cannot block
            if (!inTriangle(cur, t0, t1, t2)) continue;
            Vec2 d = cur - M;
            double ang = std::atan2(std::fabs(d.y), d.x);
            if (ang < bestAngle || (ang == bestAngle && length(d) < length(outer[pi] - M))) {
                bestAngle = ang;
                pi = i;
            }
        }
    }

    std::vector<Vec2> merged;
    merged.reserve(n + hole.size() + 2);
    for (size_t i = 0; i <= pi; ++i) merged.push_back(outer[i]);
    for (size_t k = 0; k <= hole.size(); ++k) merged.push_back(hole[(mi + k) % hole.size()]);
    merged.push_back(outer[pi]);
    for (size_t i = pi + 1; i < n; ++i) merged.push_back(outer[i]);
    outer.swap(merged);
    return true;
}

}  // namespace

bool selfIntersects(const std::vector<Vec2>& poly) {
    size_t n = poly.size();
    if (n < 4) return false;
    for (size_t i = 0; i < n; ++i) {
        for (size_t j = i + 1; j < n; ++j) {
            if (j == i + 1 || (i == 0 && j == n - 1)) continue;  // adjacent edges
            if (segmentsIntersect(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return true;
        }
    }
    return false;
}

bool triangulate(const std::vector<Vec2>& outerIn, const std::vector<std::vector<Vec2>>& holesIn,
                 std::vector<Vec2>& out, std::string* error) {
    auto fail = [&](const std::string& m) { if (error) *error = m; return false; };
    std::vector<Vec2> outer = dedupe(outerIn);
    if (outer.size() < 3) return fail("polygon has fewer than 3 distinct points");
    if (selfIntersects(outer)) return fail("polygon outline self-intersects");
    if (std::fabs(signedArea(outer)) < 1e-12) return fail("polygon has zero area");
    if (signedArea(outer) < 0) std::reverse(outer.begin(), outer.end());

    std::vector<std::vector<Vec2>> holes;
    for (const auto& h : holesIn) {
        auto hole = dedupe(h);
        if (hole.size() < 3 || std::fabs(signedArea(hole)) < 1e-12) return fail("hole is degenerate");
        if (selfIntersects(hole)) return fail("hole outline self-intersects");
        for (size_t i = 0; i < hole.size(); ++i)
            for (size_t j = 0; j < outer.size(); ++j)
                if (segmentsIntersect(hole[i], hole[(i + 1) % hole.size()], outer[j], outer[(j + 1) % outer.size()]))
                    return fail("hole intersects polygon outline");
        if (signedArea(hole) > 0) std::reverse(hole.begin(), hole.end());
        holes.push_back(hole);
    }
    std::sort(holes.begin(), holes.end(), [](const std::vector<Vec2>& a, const std::vector<Vec2>& b) {
        auto mx = [](const std::vector<Vec2>& h) {
            double m = -1e300;
            for (auto& p : h) m = std::max(m, p.x);
            return m;
        };
        return mx(a) > mx(b);
    });
    for (const auto& h : holes)
        if (!bridgeHole(outer, h)) return fail("hole lies outside polygon");

    // Ear clipping over a circular index list.
    std::vector<size_t> idx(outer.size());
    for (size_t i = 0; i < idx.size(); ++i) idx[i] = i;
    size_t guard = 0, cursor = 0;
    while (idx.size() > 3) {
        size_t n = idx.size();
        bool clipped = false;
        for (size_t k = 0; k < n; ++k) {
            size_t i = (cursor + k) % n;
            Vec2 a = outer[idx[(i + n - 1) % n]], b = outer[idx[i]], c = outer[idx[(i + 1) % n]];
            if (orient(a, b, c) <= 0) continue;
            bool blocked = false;
            for (size_t m = 0; m < n && !blocked; ++m) {
                Vec2 p = outer[idx[m]];
                if (p == a || p == b || p == c) continue;
                if (inTriangle(p, a, b, c)) blocked = true;
            }
            if (blocked) continue;
            out.push_back(a); out.push_back(b); out.push_back(c);
            idx.erase(idx.begin() + static_cast<long>(i));
            cursor = i % idx.size();
            clipped = true;
            break;
        }
        if (!clipped) {
            // Remove a collinear / zero-area vertex; otherwise the input is not simple.
            bool removed = false;
            for (size_t i = 0; i < n; ++i) {
                Vec2 a = outer[idx[(i + n - 1) % n]], b = outer[idx[i]], c = outer[idx[(i + 1) % n]];
                if (orient(a, b, c) == 0) { idx.erase(idx.begin() + static_cast<long>(i)); removed = true; break; }
            }
            if (!removed) return fail("ear clipping failed (polygon not simple)");
        }
        if (++guard > 100000) return fail("triangulation did not terminate");
    }
    Vec2 a = outer[idx[0]], b = outer[idx[1]], c = outer[idx[2]];
    if (orient(a, b, c) != 0) { out.push_back(a); out.push_back(b); out.push_back(c); }
    return true;
}

int segmentsForRadius(double radiusPx) {
    const double tol = 0.2;  // max chord deviation in pixels
    if (radiusPx <= tol * 2) return 8;
    double step = 2.0 * std::acos(1.0 - tol / radiusPx);
    int n = static_cast<int>(std::ceil(2.0 * kPi / step));
    return std::min(256, std::max(12, n));
}

std::vector<Vec2> ellipsePoints(Vec2 center, double rx, double ry, double rotationRad, int segments) {
    std::vector<Vec2> pts;
    pts.reserve(static_cast<size_t>(segments));
    for (int i = 0; i < segments; ++i) {
        double t = 2.0 * kPi * i / segments;
        pts.push_back(center + rotate({rx * std::cos(t), ry * std::sin(t)}, rotationRad));
    }
    return pts;
}

namespace {
void addDisc(Vec2 c, double r, std::vector<Vec2>& out) {
    int seg = segmentsForRadius(r);
    auto pts = ellipsePoints(c, r, r, 0, seg);
    for (int i = 0; i < seg; ++i) {
        out.push_back(c);
        out.push_back(pts[static_cast<size_t>(i)]);
        out.push_back(pts[static_cast<size_t>((i + 1) % seg)]);
    }
}
}  // namespace

void strokePolyline(const std::vector<Vec2>& ptsIn, bool closed, double width, Cap cap, Join join,
                    std::vector<Vec2>& out, double miterLimit) {
    if (width <= 0) return;
    std::vector<Vec2> pts = dedupe(ptsIn);
    double hw = width * 0.5;
    if (pts.size() == 1) {
        if (cap == Cap::Round) addDisc(pts[0], hw, out);
        else if (cap == Cap::Square) {
            Vec2 p = pts[0];
            out.insert(out.end(), {p + Vec2{-hw, -hw}, p + Vec2{hw, -hw}, p + Vec2{hw, hw},
                                   p + Vec2{-hw, -hw}, p + Vec2{hw, hw}, p + Vec2{-hw, hw}});
        }
        return;
    }
    if (closed && pts.size() < 3) closed = false;
    if (!closed && cap == Cap::Square) {
        pts.front() = pts.front() - normalize(pts[1] - pts[0]) * hw;
        size_t n = pts.size();
        pts.back() = pts.back() + normalize(pts[n - 1] - pts[n - 2]) * hw;
    }
    size_t n = pts.size();
    size_t segCount = closed ? n : n - 1;
    for (size_t i = 0; i < segCount; ++i) {
        Vec2 p0 = pts[i], p1 = pts[(i + 1) % n];
        Vec2 nrm = perp(normalize(p1 - p0)) * hw;
        out.insert(out.end(), {p0 + nrm, p0 - nrm, p1 - nrm, p0 + nrm, p1 - nrm, p1 + nrm});
    }
    // Joins at interior vertices (all vertices when closed).
    for (size_t i = closed ? 0 : 1; i < (closed ? n : n - 1); ++i) {
        Vec2 prev = pts[(i + n - 1) % n], p = pts[i], next = pts[(i + 1) % n];
        Vec2 d0 = normalize(p - prev), d1 = normalize(next - p);
        if (join == Join::Round) { addDisc(p, hw, out); continue; }
        double turn = cross(d0, d1);
        if (std::fabs(turn) < 1e-9 && dot(d0, d1) > 0) continue;  // straight
        double s = turn > 0 ? -1.0 : 1.0;  // outer side of the turn
        Vec2 a = p + perp(d0) * (hw * s), b = p + perp(d1) * (hw * s);
        out.insert(out.end(), {p, a, b});
        if (join == Join::Miter) {
            Vec2 bis = normalize(perp(d0) + perp(d1));
            double cosHalf = dot(bis, perp(d0));
            if (cosHalf > 1e-6 && 1.0 / cosHalf <= miterLimit) {
                Vec2 m = p + bis * (hw * s / cosHalf);
                out.insert(out.end(), {a, m, b});
            }
        }
    }
    if (!closed && cap == Cap::Round) {
        addDisc(pts.front(), hw, out);
        addDisc(pts.back(), hw, out);
    }
}

}  // namespace kage
