// Small 2D math toolkit: vectors, affine transforms, easing, colors and
// deterministic hash noise. All animation math is a pure function of time.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

namespace kage {

constexpr double kPi = 3.14159265358979323846;
inline double deg2rad(double d) { return d * kPi / 180.0; }
inline double rad2deg(double r) { return r * 180.0 / kPi; }
inline double clamp01(double v) { return std::min(1.0, std::max(0.0, v)); }
inline double lerp(double a, double b, double t) { return a + (b - a) * t; }
inline double smoothstep(double u) { u = clamp01(u); return u * u * (3.0 - 2.0 * u); }

struct Vec2 {
    double x = 0, y = 0;
    Vec2() = default;
    Vec2(double x_, double y_) : x(x_), y(y_) {}
    Vec2 operator+(Vec2 o) const { return {x + o.x, y + o.y}; }
    Vec2 operator-(Vec2 o) const { return {x - o.x, y - o.y}; }
    Vec2 operator*(double s) const { return {x * s, y * s}; }
    Vec2& operator+=(Vec2 o) { x += o.x; y += o.y; return *this; }
    bool operator==(Vec2 o) const { return x == o.x && y == o.y; }
};

inline double dot(Vec2 a, Vec2 b) { return a.x * b.x + a.y * b.y; }
inline double cross(Vec2 a, Vec2 b) { return a.x * b.y - a.y * b.x; }
inline double length(Vec2 v) { return std::sqrt(dot(v, v)); }
inline Vec2 normalize(Vec2 v) { double l = length(v); return l > 1e-12 ? v * (1.0 / l) : Vec2{0, 0}; }
inline Vec2 perp(Vec2 v) { return {-v.y, v.x}; }
inline Vec2 lerp(Vec2 a, Vec2 b, double t) { return {lerp(a.x, b.x, t), lerp(a.y, b.y, t)}; }
// Counter-clockwise rotation by `rad`.
inline Vec2 rotate(Vec2 v, double rad) {
    double c = std::cos(rad), s = std::sin(rad);
    return {c * v.x - s * v.y, s * v.x + c * v.y};
}

// 2D affine transform: p' = [a c; b d] p + t
struct Affine {
    double a = 1, b = 0, c = 0, d = 1, tx = 0, ty = 0;
    Vec2 apply(Vec2 p) const { return {a * p.x + c * p.y + tx, b * p.x + d * p.y + ty}; }
    Vec2 applyLinear(Vec2 p) const { return {a * p.x + c * p.y, b * p.x + d * p.y}; }
    // Composition: (this * o).apply(p) == this->apply(o.apply(p))
    Affine operator*(const Affine& o) const {
        Affine r;
        r.a = a * o.a + c * o.b;
        r.b = b * o.a + d * o.b;
        r.c = a * o.c + c * o.d;
        r.d = b * o.c + d * o.d;
        r.tx = a * o.tx + c * o.ty + tx;
        r.ty = b * o.tx + d * o.ty + ty;
        return r;
    }
    double det() const { return a * d - b * c; }
    static Affine translate(Vec2 t) { Affine r; r.tx = t.x; r.ty = t.y; return r; }
    static Affine rotate(double rad) {
        Affine r;
        double cs = std::cos(rad), sn = std::sin(rad);
        r.a = cs; r.b = sn; r.c = -sn; r.d = cs;
        return r;
    }
    static Affine scale(double sx, double sy) { Affine r; r.a = sx; r.d = sy; return r; }
};

enum class Ease { Linear, In, Out, InOut, Step };

inline double applyEase(Ease e, double u) {
    u = clamp01(u);
    switch (e) {
        case Ease::Linear: return u;
        case Ease::In: return u * u;
        case Ease::Out: return 1.0 - (1.0 - u) * (1.0 - u);
        case Ease::InOut: return smoothstep(u);
        case Ease::Step: return u < 1.0 ? 0.0 : 1.0;
    }
    return u;
}

inline bool parseEase(const std::string& s, Ease& out) {
    if (s == "linear") out = Ease::Linear;
    else if (s == "in") out = Ease::In;
    else if (s == "out") out = Ease::Out;
    else if (s == "in_out") out = Ease::InOut;
    else if (s == "step") out = Ease::Step;
    else return false;
    return true;
}

// Position fraction for a trapezoidal velocity profile (accelerate over `ramp`
// fraction of the interval, cruise, decelerate). Continuous and monotonic.
inline double trapezoid(double u, double ramp) {
    u = clamp01(u);
    ramp = std::min(0.5, std::max(1e-6, ramp));
    double vmax = 1.0 / (1.0 - ramp);
    if (u < ramp) return vmax * u * u / (2.0 * ramp);
    if (u > 1.0 - ramp) { double w = 1.0 - u; return 1.0 - vmax * w * w / (2.0 * ramp); }
    return vmax * (ramp / 2.0 + (u - ramp));
}

// sRGB color with straight alpha, components in [0,1].
struct Color {
    float r = 0, g = 0, b = 0, a = 1;
};

inline bool parseColor(const std::string& s, Color& out) {
    if (s.size() != 7 && s.size() != 9) return false;
    if (s[0] != '#') return false;
    auto hex = [](char ch) -> int {
        if (ch >= '0' && ch <= '9') return ch - '0';
        if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
        if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
        return -1;
    };
    int v[4] = {0, 0, 0, 255};
    for (size_t k = 0; k < (s.size() - 1) / 2; ++k) {
        int hi = hex(s[1 + 2 * k]), lo = hex(s[2 + 2 * k]);
        if (hi < 0 || lo < 0) return false;
        v[k] = hi * 16 + lo;
    }
    out = {v[0] / 255.f, v[1] / 255.f, v[2] / 255.f, v[3] / 255.f};
    return true;
}

// Deterministic integer hash (lowbias32) and derived noise helpers.
inline uint32_t hash32(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352dU;
    x ^= x >> 15; x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}
inline double hashUnit(uint32_t seed, int32_t i) {
    return hash32(seed ^ hash32(static_cast<uint32_t>(i) * 0x9E3779B9U)) / 4294967295.0;
}
// Smooth 1D value noise in [-1, 1].
inline double valueNoise(uint32_t seed, double x) {
    double fl = std::floor(x);
    int32_t i = static_cast<int32_t>(fl);
    double f = smoothstep(x - fl);
    return lerp(hashUnit(seed, i), hashUnit(seed, i + 1), f) * 2.0 - 1.0;
}
inline uint32_t hashString(const std::string& s) {
    uint32_t h = 2166136261U;  // FNV-1a
    for (unsigned char ch : s) { h ^= ch; h *= 16777619U; }
    return h;
}

}  // namespace kage
