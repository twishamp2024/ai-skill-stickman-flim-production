// Minimal, dependency-free JSON reader/writer used by the Kage engine.
// Objects preserve key order so that iteration (and therefore rendering) is deterministic.
#pragma once
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace kage::json {

struct ParseError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct Value {
    enum class Type { Null, Bool, Number, String, Array, Object };
    Type type = Type::Null;
    bool b = false;
    double n = 0.0;
    std::string s;
    std::vector<Value> a;
    std::vector<std::pair<std::string, Value>> o;

    bool isNull() const { return type == Type::Null; }
    bool isNumber() const { return type == Type::Number; }
    bool isString() const { return type == Type::String; }
    bool isArray() const { return type == Type::Array; }
    bool isObject() const { return type == Type::Object; }
    bool isBool() const { return type == Type::Bool; }

    // Object lookup; returns nullptr when absent or when this is not an object.
    const Value* get(const std::string& key) const;
    bool has(const std::string& key) const { return get(key) != nullptr; }
};

Value parse(const std::string& text);
Value parseFile(const std::string& path);

// Streaming writer for compact, deterministic JSON output (probe and event files).
class Writer {
public:
    explicit Writer(std::string& out) : out_(out) {}
    Writer& beginObject();
    Writer& endObject();
    Writer& beginArray();
    Writer& endArray();
    Writer& key(const std::string& k);
    Writer& value(double v);
    Writer& value(int v) { return value(static_cast<double>(v)); }
    Writer& value(bool v);
    Writer& value(const std::string& v);
    Writer& value(const char* v) { return value(std::string(v)); }
    Writer& null();

private:
    void comma();
    std::string& out_;
    std::vector<bool> first_;
    bool afterKey_ = false;
};

std::string escape(const std::string& s);

}  // namespace kage::json
