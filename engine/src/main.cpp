// kage_render: command-line front end of the Kage engine.
// Exit codes: 0 ok, 1 usage, 2 invalid scene, 3 backend unavailable,
//             4 render/IO failure, 75 injected transient fault (tests only).
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

#include "kage/frame.hpp"
#include "kage/raster.hpp"
#include "kage/version.hpp"

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

using namespace kage;

namespace {

void usage() {
    std::fprintf(stderr,
                 "kage_render %s\n"
                 "usage: kage_render SCENE.json [options]\n"
                 "  --info                 print scene summary JSON and exit\n"
                 "  --validate             validate scene + geometry for every frame, no rasterization\n"
                 "  --backend auto|gl|soft rasterizer (auto: OpenGL when a context is available)\n"
                 "  --ssaa N               software supersampling factor (default 3)\n"
                 "  --msaa N               OpenGL multisample count (default 8)\n"
                 "  --style final|flat|silhouette\n"
                 "  --frames A:B           frame range [A, B) (default: all frames)\n"
                 "  --frame N              single frame (same as --frames N:N+1)\n"
                 "  --png PATH             write the first rendered frame as PNG\n"
                 "  --png-dir DIR          write frames as DIR/frame_NNNNN.png\n"
                 "  --png-every K          with --png-dir: only every K-th frame\n"
                 "  --raw PATH|-           stream frames as raw rgb24 (for ffmpeg)\n"
                 "  --probe PATH           per-frame probe JSON (framing, joints, posture)\n"
                 "  --events PATH          animation events JSON (footsteps, attachments)\n"
                 "  --stats                timing summary on stderr\n",
                 KAGE_ENGINE_VERSION);
}

std::string probeJson(const Scene& scene, const std::vector<FrameProbe>& probes, const std::string& backend) {
    std::string s;
    json::Writer w(s);
    w.beginObject();
    w.key("kage_probe_version").value("1.0");
    w.key("engine_version").value(KAGE_ENGINE_VERSION);
    w.key("shot_id").value(scene.shotId);
    w.key("backend").value(backend);
    w.key("width").value(scene.width);
    w.key("height").value(scene.height);
    w.key("safe_area").value(scene.safeArea);
    w.key("frames").beginArray();
    for (const auto& p : probes) {
        w.beginObject();
        w.key("frame").value(p.frame);
        w.key("t").value(p.t);
        w.key("triangles").value(static_cast<double>(p.triangles));
        w.key("camera").beginObject().key("x").value(p.camera.pos.x).key("y").value(p.camera.pos.y)
            .key("height").value(p.camera.height).endObject();
        w.key("entities").beginArray();
        for (const auto& e : p.entities) {
            w.beginObject();
            w.key("id").value(e.id).key("kind").value(e.kind).key("drawn").value(e.drawn).key("important").value(e.important);
            w.key("bbox").beginArray().value(e.bbox[0]).value(e.bbox[1]).value(e.bbox[2]).value(e.bbox[3]).endArray();
            w.key("visible_fraction").value(e.visibleFraction).key("in_safe_area").value(e.inSafeArea);
            if (!e.attachedTo.empty()) w.key("attached_to").value(e.attachedTo);
            if (e.kind == "character") {
                w.key("root").beginArray().value(e.root.x).value(e.root.y).endArray();
                w.key("head").beginArray().value(e.head.x).value(e.head.y).endArray();
                w.key("hand_l").beginArray().value(e.handL.x).value(e.handL.y).endArray();
                w.key("hand_r").beginArray().value(e.handR.x).value(e.handR.y).endArray();
                w.key("sole_y").beginArray().value(e.soleY[0]).value(e.soleY[1]).endArray();
                w.key("head_px").beginArray().value(e.headPx[0]).value(e.headPx[1]).value(e.headPx[2]).endArray();
                w.key("facing").value(e.facing).key("posture").value(e.posture).key("mouth").value(e.mouth);
            }
            w.endObject();
        }
        w.endArray();
        w.endObject();
    }
    w.endArray();
    w.endObject();
    return s;
}

std::string eventsJson(const Scene& scene, const MotionEvaluator& motion) {
    std::string s;
    json::Writer w(s);
    w.beginObject();
    w.key("kage_events_version").value("1.0");
    w.key("shot_id").value(scene.shotId);
    w.key("time_offset").value(scene.timeOffset);
    w.key("footsteps").beginArray();
    for (const auto& ch : scene.characters)
        for (const auto& e : motion.footEvents(ch))
            w.beginObject().key("t").value(e.t).key("character").value(e.character).key("foot").value(e.foot ? "r" : "l")
                .key("x").value(e.x).key("kind").value(e.kind).endObject();
    w.endArray();
    w.key("attachments").beginArray();
    for (const auto& p : scene.props)
        for (const auto& e : motion.attachments(p.id))
            w.beginObject().key("t").value(e.t).key("prop").value(p.id).key("to").value(e.character.empty() ? "world" : e.character)
                .key("from_action").value(e.fromAction).endObject();
    w.endArray();
    w.endObject();
    return s;
}

bool writeText(const std::string& path, const std::string& text) {
    std::ofstream f(path, std::ios::binary);
    f << text << "\n";
    return static_cast<bool>(f);
}

}  // namespace

int main(int argc, char** argv) {
    if (const char* marker = std::getenv("KAGE_FAULT_INJECT_ONCE")) {
        // Test hook: fail transiently once per marker file to exercise retries.
        std::ifstream exists(marker);
        if (!exists) {
            std::ofstream(marker) << "injected\n";
            std::fprintf(stderr, "kage_render: injected transient fault (KAGE_FAULT_INJECT_ONCE)\n");
            return 75;
        }
    }
    if (argc < 2) { usage(); return 1; }
    std::string first = argv[1];
    if (first == "--version") { std::printf("%s\n", KAGE_ENGINE_VERSION); return 0; }
    if (first == "--help" || first == "-h") { usage(); return 0; }

    std::string scenePath = first, backend = "auto", pngPath, pngDir, rawPath, probePath, eventsPath, styleName = "final";
    int ssaa = 3, msaa = 8, pngEvery = 1, f0 = 0, f1 = -1;
    bool info = false, validate = false, stats = false;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) { std::fprintf(stderr, "missing value for %s\n", a.c_str()); std::exit(1); }
            return argv[++i];
        };
        try {
            if (a == "--info") info = true;
            else if (a == "--validate") validate = true;
            else if (a == "--stats") stats = true;
            else if (a == "--backend") backend = next();
            else if (a == "--ssaa") ssaa = std::stoi(next());
            else if (a == "--msaa") msaa = std::stoi(next());
            else if (a == "--style") styleName = next();
            else if (a == "--png") pngPath = next();
            else if (a == "--png-dir") pngDir = next();
            else if (a == "--png-every") pngEvery = std::max(1, std::stoi(next()));
            else if (a == "--raw") rawPath = next();
            else if (a == "--probe") probePath = next();
            else if (a == "--events") eventsPath = next();
            else if (a == "--frame") { f0 = std::stoi(next()); f1 = f0 + 1; }
            else if (a == "--frames") {
                std::string r = next();
                size_t c = r.find(':');
                if (c == std::string::npos) throw std::invalid_argument("expected A:B");
                f0 = std::stoi(r.substr(0, c));
                f1 = std::stoi(r.substr(c + 1));
            } else { std::fprintf(stderr, "unknown option %s\n", a.c_str()); usage(); return 1; }
        } catch (const std::exception&) {
            std::fprintf(stderr, "invalid value for %s\n", a.c_str());
            return 1;
        }
    }
    RenderStyle style;
    if (!parseRenderStyle(styleName, style)) { std::fprintf(stderr, "unknown style %s\n", styleName.c_str()); return 1; }

    Scene scene;
    try {
        scene = loadSceneFile(scenePath);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "kage_render: invalid scene: %s\n", e.what());
        return 2;
    }
    if (f1 < 0) f1 = scene.frameCount;
    if (f0 < 0 || f1 > scene.frameCount || f0 >= f1) {
        std::fprintf(stderr, "kage_render: frame range %d:%d outside [0, %d)\n", f0, f1, scene.frameCount);
        return 1;
    }
    MotionEvaluator motion(scene);
    FrameBuilder builder(scene, motion, style);

    if (info) {
        std::string s;
        json::Writer w(s);
        w.beginObject().key("shot_id").value(scene.shotId).key("frame_count").value(scene.frameCount)
            .key("fps").value(scene.fps.value()).key("duration").value(scene.frameCount / scene.fps.value())
            .key("width").value(scene.width).key("height").value(scene.height)
            .key("characters").value(static_cast<double>(scene.characters.size()))
            .key("props").value(static_cast<double>(scene.props.size()))
            .key("gl_compiled").value(glBackendCompiled()).endObject();
        std::printf("%s\n", s.c_str());
        return 0;
    }
    if (!eventsPath.empty() && !writeText(eventsPath, eventsJson(scene, motion))) {
        std::fprintf(stderr, "kage_render: cannot write %s\n", eventsPath.c_str());
        return 4;
    }

    std::vector<FrameProbe> probes;
    if (validate) {
        try {
            size_t maxTris = 0;
            for (int f = f0; f < f1; ++f) {
                DrawList dl;
                FrameProbe p;
                builder.build(f, dl, &p);
                maxTris = std::max(maxTris, dl.triangleCount());
                if (!probePath.empty()) probes.push_back(p);
            }
            if (!probePath.empty() && !writeText(probePath, probeJson(scene, probes, "none"))) return 4;
            std::printf("{\"valid\":true,\"frames\":%d,\"max_triangles\":%zu}\n", f1 - f0, maxTris);
            return 0;
        } catch (const std::exception& e) {
            std::printf("{\"valid\":false,\"error\":%s}\n", json::escape(e.what()).c_str());
            return 2;
        }
    }

    std::unique_ptr<Renderer> renderer;
    if (backend == "gl" || backend == "auto") {
        std::string reason;
        renderer = makeGLRenderer(scene.width, scene.height, msaa, &reason);
        if (!renderer) {
            if (backend == "gl") { std::fprintf(stderr, "kage_render: OpenGL backend unavailable: %s\n", reason.c_str()); return 3; }
            std::fprintf(stderr, "kage_render: OpenGL unavailable (%s); using software rasterizer\n", reason.c_str());
        }
    } else if (backend != "soft") {
        std::fprintf(stderr, "unknown backend %s\n", backend.c_str());
        return 1;
    }
    if (!renderer) renderer = makeSoftwareRenderer(scene.width, scene.height, ssaa);
    if (stats) std::fprintf(stderr, "kage_render: backend %s\n", renderer->describe().c_str());

    FILE* raw = nullptr;
    if (rawPath == "-") {
#ifdef _WIN32
        _setmode(_fileno(stdout), _O_BINARY);
#endif
        raw = stdout;
    } else if (!rawPath.empty()) {
        raw = std::fopen(rawPath.c_str(), "wb");
        if (!raw) { std::fprintf(stderr, "kage_render: cannot open %s\n", rawPath.c_str()); return 4; }
    }

    auto tStart = std::chrono::steady_clock::now();
    double buildMs = 0, rasterMs = 0;
    std::vector<uint8_t> rgb;
    try {
        for (int f = f0; f < f1; ++f) {
            auto a = std::chrono::steady_clock::now();
            DrawList dl;
            FrameProbe p;
            builder.build(f, dl, probePath.empty() ? nullptr : &p);
            auto b = std::chrono::steady_clock::now();
            renderer->render(dl, scene.background, rgb);
            auto c = std::chrono::steady_clock::now();
            buildMs += std::chrono::duration<double, std::milli>(b - a).count();
            rasterMs += std::chrono::duration<double, std::milli>(c - b).count();
            if (!probePath.empty()) probes.push_back(p);
            if (raw && std::fwrite(rgb.data(), 1, rgb.size(), raw) != rgb.size()) throw std::runtime_error("raw write failed");
            if (!pngPath.empty() && f == f0 && !writePNG(pngPath, scene.width, scene.height, rgb))
                throw std::runtime_error("cannot write " + pngPath);
            if (!pngDir.empty() && (f - f0) % pngEvery == 0) {
                char name[64];
                std::snprintf(name, sizeof name, "/frame_%05d.png", f);
                if (!writePNG(pngDir + name, scene.width, scene.height, rgb)) throw std::runtime_error("cannot write " + pngDir + name);
            }
        }
    } catch (const SceneError& e) {
        std::fprintf(stderr, "kage_render: invalid geometry: %s\n", e.what());
        return 2;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "kage_render: render failed: %s\n", e.what());
        return 4;
    }
    if (raw && raw != stdout) std::fclose(raw);
    if (raw == stdout) std::fflush(stdout);
    if (!probePath.empty() && !writeText(probePath, probeJson(scene, probes, renderer->name()))) return 4;
    if (stats) {
        double total = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - tStart).count();
        int n = f1 - f0;
        std::fprintf(stderr, "kage_render: %d frames in %.1f ms (build %.2f ms/frame, raster %.2f ms/frame, %.1f fps)\n", n, total,
                     buildMs / n, rasterMs / n, n * 1000.0 / std::max(1e-6, total));
    }
    return 0;
}
