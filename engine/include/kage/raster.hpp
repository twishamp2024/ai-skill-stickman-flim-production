// Rasterization backends. Both consume the same DrawList, so geometry,
// layering and color decisions are made once, independent of the backend.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "kage/geometry.hpp"

namespace kage {

class Renderer {
public:
    virtual ~Renderer() = default;
    virtual std::string name() const = 0;
    virtual std::string describe() const = 0;  // device / settings, for logs
    // Renders `dl` over `background` into tightly packed RGB8 rows, top row first.
    virtual void render(const DrawList& dl, const Color& background, std::vector<uint8_t>& rgb) = 0;
};

// Reference CPU rasterizer: supersampled edge-function fill with a box
// resolve. Bit-exact deterministic for a given binary and input.
std::unique_ptr<Renderer> makeSoftwareRenderer(int width, int height, int supersample);

// OpenGL 3.3 core renderer via an invisible GLFW window and a multisampled
// framebuffer. Returns nullptr (with reason) when no context can be created.
std::unique_ptr<Renderer> makeGLRenderer(int width, int height, int msaa, std::string* reason);
bool glBackendCompiled();

bool writePNG(const std::string& path, int width, int height, const std::vector<uint8_t>& rgb);

}  // namespace kage
