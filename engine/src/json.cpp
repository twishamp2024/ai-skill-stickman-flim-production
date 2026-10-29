#include "kage/json.hpp"

#include <cmath>
#include <cstdio>
#include <fstream>
#include <sstream>

namespace kage::json {

const Value* Value::get(const std::string& key) const {
    if (type != Type::Object) return nullptr;
    for (const auto& kv : o)
        if (kv.first == key) return &kv.second;
    return nullptr;
}

namespace {

class Parser {
public:
    explicit Parser(const std::string& t) : t_(t) {}

    Value parseDocument() {
        Value v = parseValue(0);
        skipWs();
        if (i_ != t_.size()) fail("trailing characters after JSON document");
        return v;
    }

private:
    [[noreturn]] void fail(const std::string& msg) const {
        size_t line = 1, col = 1;
        for (size_t k = 0; k < i_ && k < t_.size(); ++k) {
            if (t_[k] == '\n') { ++line; col = 1; } else { ++col; }
        }
        throw ParseError("JSON parse error at line " + std::to_string(line) + ", column " +
                         std::to_string(col) + ": " + msg);
    }

    void skipWs() {
        while (i_ < t_.size() && (t_[i_] == ' ' || t_[i_] == '\t' || t_[i_] == '\n' || t_[i_] == '\r')) ++i_;
    }

    char peek() {
        skipWs();
        if (i_ >= t_.size()) fail("unexpected end of input");
        return t_[i_];
    }

    void expectLiteral(const char* lit) {
        for (const char* p = lit; *p; ++p, ++i_)
            if (i_ >= t_.size() || t_[i_] != *p) fail(std::string("expected '") + lit + "'");
    }

    Value parseValue(int depth) {
        if (depth > 256) fail("nesting too deep");
        char c = peek();
        Value v;
        if (c == '{') {
            v.type = Value::Type::Object;
            ++i_;
            if (peek() == '}') { ++i_; return v; }
            while (true) {
                if (peek() != '"') fail("expected object key string");
                std::string k = parseString();
                if (peek() != ':') fail("expected ':' after object key");
                ++i_;
                for (const auto& kv : v.o)
                    if (kv.first == k) fail("duplicate object key '" + k + "'");
                v.o.emplace_back(std::move(k), parseValue(depth + 1));
                char d = peek();
                ++i_;
                if (d == '}') break;
                if (d != ',') fail("expected ',' or '}' in object");
            }
        } else if (c == '[') {
            v.type = Value::Type::Array;
            ++i_;
            if (peek() == ']') { ++i_; return v; }
            while (true) {
                v.a.push_back(parseValue(depth + 1));
                char d = peek();
                ++i_;
                if (d == ']') break;
                if (d != ',') fail("expected ',' or ']' in array");
            }
        } else if (c == '"') {
            v.type = Value::Type::String;
            v.s = parseString();
        } else if (c == 't') {
            expectLiteral("true");
            v.type = Value::Type::Bool;
            v.b = true;
        } else if (c == 'f') {
            expectLiteral("false");
            v.type = Value::Type::Bool;
        } else if (c == 'n') {
            expectLiteral("null");
        } else {
            v.type = Value::Type::Number;
            v.n = parseNumber();
        }
        return v;
    }

    double parseNumber() {
        size_t start = i_;
        if (t_[i_] == '-') ++i_;
        auto digits = [&] {
            size_t s = i_;
            while (i_ < t_.size() && t_[i_] >= '0' && t_[i_] <= '9') ++i_;
            return i_ > s;
        };
        if (!digits()) fail("invalid number");
        if (i_ < t_.size() && t_[i_] == '.') { ++i_; if (!digits()) fail("invalid fraction"); }
        if (i_ < t_.size() && (t_[i_] == 'e' || t_[i_] == 'E')) {
            ++i_;
            if (i_ < t_.size() && (t_[i_] == '+' || t_[i_] == '-')) ++i_;
            if (!digits()) fail("invalid exponent");
        }
        // Locale-independent conversion.
        std::istringstream ss(t_.substr(start, i_ - start));
        ss.imbue(std::locale::classic());
        double d = 0;
        ss >> d;
        if (!std::isfinite(d)) fail("number out of range");
        return d;
    }

    static void appendUtf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out += static_cast<char>(cp);
        else if (cp < 0x800) { out += static_cast<char>(0xC0 | (cp >> 6)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }

    unsigned parseHex4() {
        if (i_ + 4 > t_.size()) fail("truncated unicode escape");
        unsigned v = 0;
        for (int k = 0; k < 4; ++k) {
            char h = t_[i_++];
            v <<= 4;
            if (h >= '0' && h <= '9') v |= h - '0';
            else if (h >= 'a' && h <= 'f') v |= h - 'a' + 10;
            else if (h >= 'A' && h <= 'F') v |= h - 'A' + 10;
            else fail("invalid unicode escape");
        }
        return v;
    }

    std::string parseString() {
        ++i_;  // opening quote
        std::string out;
        while (true) {
            if (i_ >= t_.size()) fail("unterminated string");
            char c = t_[i_++];
            if (c == '"') break;
            if (static_cast<unsigned char>(c) < 0x20) fail("control character in string");
            if (c != '\\') { out += c; continue; }
            if (i_ >= t_.size()) fail("unterminated escape");
            char e = t_[i_++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = parseHex4();
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        if (i_ + 6 > t_.size() || t_[i_] != '\\' || t_[i_ + 1] != 'u') fail("lone surrogate");
                        i_ += 2;
                        unsigned lo = parseHex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    appendUtf8(out, cp);
                    break;
                }
                default: fail("invalid escape");
            }
        }
        return out;
    }

    const std::string& t_;
    size_t i_ = 0;
};

}  // namespace

Value parse(const std::string& text) { return Parser(text).parseDocument(); }

Value parseFile(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw ParseError("cannot open file: " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    return parse(ss.str());
}

std::string escape(const std::string& s) {
    std::string out = "\"";
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out + "\"";
}

void Writer::comma() {
    if (afterKey_) { afterKey_ = false; return; }
    if (!first_.empty()) {
        if (!first_.back()) out_ += ',';
        first_.back() = false;
    }
}

Writer& Writer::beginObject() { comma(); out_ += '{'; first_.push_back(true); return *this; }
Writer& Writer::endObject() { out_ += '}'; first_.pop_back(); return *this; }
Writer& Writer::beginArray() { comma(); out_ += '['; first_.push_back(true); return *this; }
Writer& Writer::endArray() { out_ += ']'; first_.pop_back(); return *this; }
Writer& Writer::key(const std::string& k) { comma(); out_ += escape(k); out_ += ':'; afterKey_ = true; return *this; }
Writer& Writer::value(bool v) { comma(); out_ += v ? "true" : "false"; return *this; }
Writer& Writer::value(const std::string& v) { comma(); out_ += escape(v); return *this; }
Writer& Writer::null() { comma(); out_ += "null"; return *this; }
Writer& Writer::value(double v) {
    comma();
    if (!std::isfinite(v)) { out_ += "null"; return *this; }
    char buf[32];
    // Fixed precision keeps probe files byte-stable across runs.
    std::snprintf(buf, sizeof buf, "%.5f", v);
    std::string s(buf);
    if (s.find('.') != std::string::npos) {
        while (!s.empty() && s.back() == '0') s.pop_back();
        if (!s.empty() && s.back() == '.') s.pop_back();
    }
    if (s == "-0") s = "0";
    out_ += s;
    return *this;
}

}  // namespace kage::json
