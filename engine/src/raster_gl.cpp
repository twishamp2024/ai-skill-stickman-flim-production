// OpenGL backend. Platform differences are confined to this file: the context
// comes from GLFW and every GL entry point is resolved at runtime through
// glfwGetProcAddress, so no system GL headers or loader libraries are needed.
#include "kage/raster.hpp"

#ifdef KAGE_HAVE_GLFW
#define GLFW_INCLUDE_NONE
#include <GLFW/glfw3.h>

#include <cstddef>
#include <cstring>

namespace kage {

namespace {

using GLenum = unsigned int;
using GLuint = unsigned int;
using GLint = int;
using GLsizei = int;
using GLboolean = unsigned char;
using GLbitfield = unsigned int;
using GLfloat = float;
using GLchar = char;
using GLsizeiptr = std::ptrdiff_t;

constexpr GLenum GL_TRIANGLES = 0x0004, GL_SRC_ALPHA = 0x0302, GL_ONE_MINUS_SRC_ALPHA = 0x0303, GL_BLEND = 0x0BE2,
                 GL_UNSIGNED_BYTE = 0x1401, GL_FLOAT = 0x1406, GL_RGBA = 0x1908, GL_RGBA8 = 0x8058,
                 GL_COLOR_BUFFER_BIT = 0x4000, GL_NEAREST = 0x2600, GL_VERSION = 0x1F02, GL_RENDERER = 0x1F01,
                 GL_ARRAY_BUFFER = 0x8892, GL_STREAM_DRAW = 0x88E0, GL_FRAGMENT_SHADER = 0x8B30,
                 GL_VERTEX_SHADER = 0x8B31, GL_COMPILE_STATUS = 0x8B81, GL_LINK_STATUS = 0x8B82,
                 GL_FRAMEBUFFER = 0x8D40, GL_RENDERBUFFER = 0x8D41, GL_READ_FRAMEBUFFER = 0x8CA8,
                 GL_DRAW_FRAMEBUFFER = 0x8CA9, GL_COLOR_ATTACHMENT0 = 0x8CE0, GL_FRAMEBUFFER_COMPLETE = 0x8CD5,
                 GL_MAX_SAMPLES = 0x8D57, GL_PACK_ALIGNMENT = 0x0D05, GL_MULTISAMPLE = 0x809D;

#define KAGE_GL_FUNCS(X)                                                                                   \
    X(void, Viewport, GLint, GLint, GLsizei, GLsizei)                                                      \
    X(void, ClearColor, GLfloat, GLfloat, GLfloat, GLfloat)                                                \
    X(void, Clear, GLbitfield)                                                                             \
    X(void, Enable, GLenum)                                                                                \
    X(void, BlendFunc, GLenum, GLenum)                                                                     \
    X(const unsigned char*, GetString, GLenum)                                                             \
    X(void, GetIntegerv, GLenum, GLint*)                                                                   \
    X(void, PixelStorei, GLenum, GLint)                                                                    \
    X(void, ReadPixels, GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void*)                             \
    X(void, Finish)                                                                                        \
    X(void, GenFramebuffers, GLsizei, GLuint*)                                                             \
    X(void, BindFramebuffer, GLenum, GLuint)                                                               \
    X(GLenum, CheckFramebufferStatus, GLenum)                                                              \
    X(void, GenRenderbuffers, GLsizei, GLuint*)                                                            \
    X(void, BindRenderbuffer, GLenum, GLuint)                                                              \
    X(void, RenderbufferStorage, GLenum, GLenum, GLsizei, GLsizei)                                         \
    X(void, RenderbufferStorageMultisample, GLenum, GLsizei, GLenum, GLsizei, GLsizei)                     \
    X(void, FramebufferRenderbuffer, GLenum, GLenum, GLenum, GLuint)                                       \
    X(void, BlitFramebuffer, GLint, GLint, GLint, GLint, GLint, GLint, GLint, GLint, GLbitfield, GLenum)   \
    X(GLuint, CreateShader, GLenum)                                                                        \
    X(void, ShaderSource, GLuint, GLsizei, const GLchar* const*, const GLint*)                             \
    X(void, CompileShader, GLuint)                                                                         \
    X(void, GetShaderiv, GLuint, GLenum, GLint*)                                                           \
    X(void, GetShaderInfoLog, GLuint, GLsizei, GLsizei*, GLchar*)                                          \
    X(GLuint, CreateProgram)                                                                               \
    X(void, AttachShader, GLuint, GLuint)                                                                  \
    X(void, LinkProgram, GLuint)                                                                           \
    X(void, GetProgramiv, GLuint, GLenum, GLint*)                                                          \
    X(void, UseProgram, GLuint)                                                                            \
    X(GLint, GetUniformLocation, GLuint, const GLchar*)                                                    \
    X(void, Uniform2f, GLint, GLfloat, GLfloat)                                                            \
    X(void, GenVertexArrays, GLsizei, GLuint*)                                                             \
    X(void, BindVertexArray, GLuint)                                                                       \
    X(void, GenBuffers, GLsizei, GLuint*)                                                                  \
    X(void, BindBuffer, GLenum, GLuint)                                                                    \
    X(void, BufferData, GLenum, GLsizeiptr, const void*, GLenum)                                           \
    X(void, VertexAttribPointer, GLuint, GLint, GLenum, GLboolean, GLsizei, const void*)                   \
    X(void, EnableVertexAttribArray, GLuint)                                                               \
    X(void, DrawArrays, GLenum, GLint, GLsizei)

struct GL {
#define X(ret, name, ...) ret (*name)(__VA_ARGS__) = nullptr;
    KAGE_GL_FUNCS(X)
#undef X
    bool load(std::string* missing) {
#define X(ret, name, ...)                                                                   \
    name = reinterpret_cast<ret (*)(__VA_ARGS__)>(glfwGetProcAddress("gl" #name));          \
    if (!name) { *missing = "gl" #name; return false; }
        KAGE_GL_FUNCS(X)
#undef X
        return true;
    }
};

const char* kVS = R"(#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec4 aColor;
uniform vec2 uViewport;
out vec4 vColor;
void main() {
    vColor = aColor;
    gl_Position = vec4(aPos.x / uViewport.x * 2.0 - 1.0, 1.0 - aPos.y / uViewport.y * 2.0, 0.0, 1.0);
})";

const char* kFS = R"(#version 330 core
in vec4 vColor;
out vec4 fragColor;
void main() { fragColor = vColor; })";

class GLRenderer : public Renderer {
public:
    ~GLRenderer() override {
        if (window_) glfwDestroyWindow(window_);
        glfwTerminate();
    }

    bool init(int w, int h, int msaa, std::string* reason) {
        w_ = w; h_ = h;
#ifdef __APPLE__
        glfwInitHint(GLFW_COCOA_CHDIR_RESOURCES, GLFW_FALSE);
        glfwInitHint(GLFW_COCOA_MENUBAR, GLFW_FALSE);
#endif
        if (!glfwInit()) { *reason = "glfwInit failed (no display/window server?)"; return false; }
        glfwWindowHint(GLFW_VISIBLE, GLFW_FALSE);
        glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
        glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
        glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
        glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, GLFW_TRUE);
        window_ = glfwCreateWindow(64, 64, "kage", nullptr, nullptr);
        if (!window_) { *reason = "could not create an OpenGL 3.3 core context"; return false; }
        glfwMakeContextCurrent(window_);
        std::string missing;
        if (!gl_.load(&missing)) { *reason = "missing GL entry point " + missing; return false; }

        GLint maxSamples = 0;
        gl_.GetIntegerv(GL_MAX_SAMPLES, &maxSamples);
        samples_ = std::max(1, std::min(msaa, static_cast<int>(maxSamples)));
        GLuint rb[2];
        gl_.GenFramebuffers(2, fbo_);
        gl_.GenRenderbuffers(2, rb);
        gl_.BindRenderbuffer(GL_RENDERBUFFER, rb[0]);
        gl_.RenderbufferStorageMultisample(GL_RENDERBUFFER, samples_, GL_RGBA8, w, h);
        gl_.BindFramebuffer(GL_FRAMEBUFFER, fbo_[0]);
        gl_.FramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb[0]);
        if (gl_.CheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { *reason = "MSAA framebuffer incomplete"; return false; }
        gl_.BindRenderbuffer(GL_RENDERBUFFER, rb[1]);
        gl_.RenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, w, h);
        gl_.BindFramebuffer(GL_FRAMEBUFFER, fbo_[1]);
        gl_.FramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb[1]);
        if (gl_.CheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { *reason = "resolve framebuffer incomplete"; return false; }

        auto compile = [&](GLenum type, const char* src) -> GLuint {
            GLuint s = gl_.CreateShader(type);
            gl_.ShaderSource(s, 1, &src, nullptr);
            gl_.CompileShader(s);
            GLint ok = 0;
            gl_.GetShaderiv(s, GL_COMPILE_STATUS, &ok);
            if (!ok) {
                char log[1024] = {0};
                gl_.GetShaderInfoLog(s, sizeof log, nullptr, log);
                *reason = std::string("shader compile failed: ") + log;
                return 0;
            }
            return s;
        };
        GLuint vs = compile(GL_VERTEX_SHADER, kVS), fs = compile(GL_FRAGMENT_SHADER, kFS);
        if (!vs || !fs) return false;
        prog_ = gl_.CreateProgram();
        gl_.AttachShader(prog_, vs);
        gl_.AttachShader(prog_, fs);
        gl_.LinkProgram(prog_);
        GLint linked = 0;
        gl_.GetProgramiv(prog_, GL_LINK_STATUS, &linked);
        if (!linked) { *reason = "shader link failed"; return false; }
        gl_.GenVertexArrays(1, &vao_);
        gl_.BindVertexArray(vao_);
        gl_.GenBuffers(1, &vbo_);
        gl_.BindBuffer(GL_ARRAY_BUFFER, vbo_);
        gl_.VertexAttribPointer(0, 2, GL_FLOAT, 0, sizeof(Vertex), reinterpret_cast<void*>(offsetof(Vertex, x)));
        gl_.EnableVertexAttribArray(0);
        gl_.VertexAttribPointer(1, 4, GL_FLOAT, 0, sizeof(Vertex), reinterpret_cast<void*>(offsetof(Vertex, r)));
        gl_.EnableVertexAttribArray(1);
        const unsigned char* ver = gl_.GetString(GL_VERSION);
        const unsigned char* ren = gl_.GetString(GL_RENDERER);
        device_ = std::string(ren ? reinterpret_cast<const char*>(ren) : "?") + " / GL " +
                  (ver ? reinterpret_cast<const char*>(ver) : "?");
        return true;
    }

    std::string name() const override { return "gl"; }
    std::string describe() const override {
        return "OpenGL " + std::to_string(w_) + "x" + std::to_string(h_) + " msaa=" + std::to_string(samples_) + " (" + device_ + ")";
    }

    void render(const DrawList& dl, const Color& bg, std::vector<uint8_t>& rgb) override {
        gl_.BindFramebuffer(GL_FRAMEBUFFER, fbo_[0]);
        gl_.Viewport(0, 0, w_, h_);
        gl_.Enable(GL_MULTISAMPLE);
        gl_.Enable(GL_BLEND);
        gl_.BlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
        gl_.ClearColor(bg.r, bg.g, bg.b, 1.0f);
        gl_.Clear(GL_COLOR_BUFFER_BIT);
        gl_.UseProgram(prog_);
        gl_.Uniform2f(gl_.GetUniformLocation(prog_, "uViewport"), static_cast<float>(w_), static_cast<float>(h_));
        gl_.BindVertexArray(vao_);
        gl_.BindBuffer(GL_ARRAY_BUFFER, vbo_);
        gl_.BufferData(GL_ARRAY_BUFFER, static_cast<GLsizeiptr>(dl.verts.size() * sizeof(Vertex)), dl.verts.data(), GL_STREAM_DRAW);
        gl_.DrawArrays(GL_TRIANGLES, 0, static_cast<GLsizei>(dl.verts.size()));
        gl_.BindFramebuffer(GL_READ_FRAMEBUFFER, fbo_[0]);
        gl_.BindFramebuffer(GL_DRAW_FRAMEBUFFER, fbo_[1]);
        gl_.BlitFramebuffer(0, 0, w_, h_, 0, 0, w_, h_, GL_COLOR_BUFFER_BIT, GL_NEAREST);
        gl_.BindFramebuffer(GL_READ_FRAMEBUFFER, fbo_[1]);
        gl_.PixelStorei(GL_PACK_ALIGNMENT, 1);
        rgba_.resize(static_cast<size_t>(w_) * h_ * 4);
        gl_.ReadPixels(0, 0, w_, h_, GL_RGBA, GL_UNSIGNED_BYTE, rgba_.data());
        rgb.resize(static_cast<size_t>(w_) * h_ * 3);
        for (int y = 0; y < h_; ++y) {  // GL rows are bottom-up
            const uint8_t* src = &rgba_[static_cast<size_t>(h_ - 1 - y) * w_ * 4];
            uint8_t* dst = &rgb[static_cast<size_t>(y) * w_ * 3];
            for (int x = 0; x < w_; ++x) { dst[x * 3] = src[x * 4]; dst[x * 3 + 1] = src[x * 4 + 1]; dst[x * 3 + 2] = src[x * 4 + 2]; }
        }
    }

private:
    GLFWwindow* window_ = nullptr;
    GL gl_;
    GLuint fbo_[2] = {0, 0}, prog_ = 0, vao_ = 0, vbo_ = 0;
    int w_ = 0, h_ = 0, samples_ = 1;
    std::string device_;
    std::vector<uint8_t> rgba_;
};

}  // namespace

bool glBackendCompiled() { return true; }

std::unique_ptr<Renderer> makeGLRenderer(int width, int height, int msaa, std::string* reason) {
    auto r = std::make_unique<GLRenderer>();
    if (!r->init(width, height, msaa, reason)) return nullptr;
    return r;
}

}  // namespace kage

#else

namespace kage {
bool glBackendCompiled() { return false; }
std::unique_ptr<Renderer> makeGLRenderer(int, int, int, std::string* reason) {
    *reason = "engine was built without GLFW/OpenGL support (KAGE_WITH_GL=OFF or GLFW not found)";
    return nullptr;
}
}  // namespace kage

#endif
