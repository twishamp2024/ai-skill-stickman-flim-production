// Dependency-free PNG writer (stored deflate blocks). Files are larger than a
// compressed PNG but are exact, portable and readable by every decoder.
#include <cstdio>
#include <memory>

#include "kage/raster.hpp"

namespace kage {

namespace {

uint32_t crcTable[256];
bool crcInit = false;

uint32_t crc32(const uint8_t* data, size_t n, uint32_t crc = 0xFFFFFFFFu) {
    if (!crcInit) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            crcTable[i] = c;
        }
        crcInit = true;
    }
    for (size_t i = 0; i < n; ++i) crc = crcTable[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    return crc;
}

void be32(std::vector<uint8_t>& v, uint32_t x) {
    v.push_back(static_cast<uint8_t>(x >> 24)); v.push_back(static_cast<uint8_t>(x >> 16));
    v.push_back(static_cast<uint8_t>(x >> 8)); v.push_back(static_cast<uint8_t>(x));
}

void chunk(std::vector<uint8_t>& out, const char* type, const std::vector<uint8_t>& data) {
    be32(out, static_cast<uint32_t>(data.size()));
    std::vector<uint8_t> td(type, type + 4);
    td.insert(td.end(), data.begin(), data.end());
    out.insert(out.end(), td.begin(), td.end());
    be32(out, crc32(td.data(), td.size()) ^ 0xFFFFFFFFu);
}

}  // namespace

bool writePNG(const std::string& path, int w, int h, const std::vector<uint8_t>& rgb) {
    if (rgb.size() != static_cast<size_t>(w) * h * 3) return false;
    std::vector<uint8_t> raw;
    raw.reserve(static_cast<size_t>(h) * (w * 3 + 1));
    for (int y = 0; y < h; ++y) {
        raw.push_back(0);  // filter: none
        raw.insert(raw.end(), rgb.begin() + static_cast<long>(y) * w * 3, rgb.begin() + static_cast<long>(y + 1) * w * 3);
    }
    std::vector<uint8_t> z = {0x78, 0x01};
    uint32_t a = 1, b = 0;
    for (uint8_t c : raw) { a = (a + c) % 65521; b = (b + a) % 65521; }
    for (size_t off = 0; off < raw.size() || off == 0;) {
        size_t n = std::min<size_t>(65535, raw.size() - off);
        bool last = off + n >= raw.size();
        z.push_back(last ? 1 : 0);
        z.push_back(static_cast<uint8_t>(n)); z.push_back(static_cast<uint8_t>(n >> 8));
        z.push_back(static_cast<uint8_t>(~n)); z.push_back(static_cast<uint8_t>((~n) >> 8));
        z.insert(z.end(), raw.begin() + static_cast<long>(off), raw.begin() + static_cast<long>(off + n));
        off += n;
        if (last) break;
    }
    be32(z, (b << 16) | a);

    std::vector<uint8_t> png = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    std::vector<uint8_t> ihdr;
    be32(ihdr, static_cast<uint32_t>(w)); be32(ihdr, static_cast<uint32_t>(h));
    ihdr.insert(ihdr.end(), {8, 2, 0, 0, 0});  // 8-bit RGB
    chunk(png, "IHDR", ihdr);
    chunk(png, "IDAT", z);
    chunk(png, "IEND", {});
    std::unique_ptr<FILE, int (*)(FILE*)> f(std::fopen(path.c_str(), "wb"), &std::fclose);
    if (!f) return false;
    return std::fwrite(png.data(), 1, png.size(), f.get()) == png.size();
}

}  // namespace kage
