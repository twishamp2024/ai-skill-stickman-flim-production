#include <algorithm>
#include <cmath>

#include "kage/raster.hpp"

namespace kage {

namespace {

inline uint8_t to8(float v) { return static_cast<uint8_t>(std::lround(std::min(1.0f, std::max(0.0f, v)) * 255.0f)); }

class SoftwareRenderer : public Renderer {
public:
    SoftwareRenderer(int w, int h, int ss) : w_(w), h_(h), ss_(std::max(1, std::min(8, ss))) {
        buf_.resize(static_cast<size_t>(w_ * ss_) * static_cast<size_t>(h_ * ss_) * 3);
    }
    std::string name() const override { return "soft"; }
    std::string describe() const override {
        return "software rasterizer " + std::to_string(w_) + "x" + std::to_string(h_) + " ssaa=" + std::to_string(ss_) + "x" +
               std::to_string(ss_);
    }

    void render(const DrawList& dl, const Color& bg, std::vector<uint8_t>& rgb) override {
        const int W = w_ * ss_, H = h_ * ss_;
        uint8_t br = to8(bg.r), bgc = to8(bg.g), bb = to8(bg.b);
        for (size_t i = 0; i < buf_.size(); i += 3) { buf_[i] = br; buf_[i + 1] = bgc; buf_[i + 2] = bb; }
        const auto& v = dl.verts;
        for (size_t i = 0; i + 2 < v.size(); i += 3) fillTriangle(v[i], v[i + 1], v[i + 2], W, H);
        resolve(rgb);
    }

private:
    void fillTriangle(const Vertex& A, const Vertex& B, const Vertex& C, int W, int H) {
        double ax = A.x * ss_, ay = A.y * ss_, bx = B.x * ss_, by = B.y * ss_, cx = C.x * ss_, cy = C.y * ss_;
        double area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
        if (std::fabs(area) < 1e-12) return;
        if (area < 0) { std::swap(bx, cx); std::swap(by, cy); }
        int x0 = std::max(0, static_cast<int>(std::floor(std::min({ax, bx, cx}))));
        int y0 = std::max(0, static_cast<int>(std::floor(std::min({ay, by, cy}))));
        int x1 = std::min(W - 1, static_cast<int>(std::ceil(std::max({ax, bx, cx}))));
        int y1 = std::min(H - 1, static_cast<int>(std::ceil(std::max({ay, by, cy}))));
        if (x0 > x1 || y0 > y1) return;
        // Edge (p -> q): e(s) = (q - p) x (s - p). Ties resolved by an
        // antisymmetric top-left rule so shared edges are filled exactly once.
        struct Edge { double px, py, dx, dy; bool tl; };
        auto mk = [](double px, double py, double qx, double qy) {
            double dx = qx - px, dy = qy - py;
            return Edge{px, py, dx, dy, dy < 0 || (dy == 0 && dx > 0)};
        };
        Edge e[3] = {mk(ax, ay, bx, by), mk(bx, by, cx, cy), mk(cx, cy, ax, ay)};
        const uint8_t r = to8(A.r), g = to8(A.g), b = to8(A.b);
        const float a = A.a;
        const bool opaque = a >= 0.999f;
        for (int y = y0; y <= y1; ++y) {
            double sy = y + 0.5;
            for (int x = x0; x <= x1; ++x) {
                double sx = x + 0.5;
                bool inside = true;
                for (const Edge& ed : e) {
                    double w = ed.dx * (sy - ed.py) - ed.dy * (sx - ed.px);
                    if (w < 0 || (w == 0 && !ed.tl)) { inside = false; break; }
                }
                if (!inside) continue;
                uint8_t* p = &buf_[(static_cast<size_t>(y) * W + x) * 3];
                if (opaque) { p[0] = r; p[1] = g; p[2] = b; }
                else {
                    p[0] = static_cast<uint8_t>(std::lround(r * a + p[0] * (1 - a)));
                    p[1] = static_cast<uint8_t>(std::lround(g * a + p[1] * (1 - a)));
                    p[2] = static_cast<uint8_t>(std::lround(b * a + p[2] * (1 - a)));
                }
            }
        }
    }

    void resolve(std::vector<uint8_t>& rgb) {
        rgb.assign(static_cast<size_t>(w_) * h_ * 3, 0);
        const int W = w_ * ss_;
        const unsigned n = static_cast<unsigned>(ss_ * ss_);
        for (int y = 0; y < h_; ++y) {
            for (int x = 0; x < w_; ++x) {
                unsigned acc[3] = {0, 0, 0};
                for (int sy = 0; sy < ss_; ++sy) {
                    const uint8_t* row = &buf_[(static_cast<size_t>(y * ss_ + sy) * W + x * ss_) * 3];
                    for (int sx = 0; sx < ss_; ++sx) { acc[0] += row[sx * 3]; acc[1] += row[sx * 3 + 1]; acc[2] += row[sx * 3 + 2]; }
                }
                uint8_t* o = &rgb[(static_cast<size_t>(y) * w_ + x) * 3];
                for (int k = 0; k < 3; ++k) o[k] = static_cast<uint8_t>((acc[k] + n / 2) / n);
            }
        }
    }

    int w_, h_, ss_;
    std::vector<uint8_t> buf_;
};

}  // namespace

std::unique_ptr<Renderer> makeSoftwareRenderer(int width, int height, int supersample) {
    return std::make_unique<SoftwareRenderer>(width, height, supersample);
}

}  // namespace kage
