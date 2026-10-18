#include "map_file.hpp"

#include <cstdint>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fwres {
namespace {

bool is_hex_token(const std::string& s) {
    return s.size() > 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X');
}

// 目标文件长这样：esp-idf/freertos/libfreertos.a(tasks.c.obj)
// 或者没有归档括号：CMakeFiles/x.dir/main.c.obj
bool looks_like_object(const std::string& s) {
    if (s.empty()) return false;
    const std::string::size_type n = s.size();
    if (n >= 3 && s.compare(n - 3, 3, ".o)") == 0) return true;
    if (n >= 5 && s.compare(n - 5, 5, ".obj)") == 0) return true;
    if (s.size() >= 4 && s.compare(s.size() - 4, 4, ".obj") == 0) return true;
    if (s.size() >= 2 && s.compare(s.size() - 2, 2, ".o") == 0) return true;
    return false;
}

std::uint64_t to_hex(const std::string& s) {
    try {
        return static_cast<std::uint64_t>(std::stoull(s, nullptr, 16));
    } catch (const std::exception&) {
        return 0;
    }
}

}  // namespace

MapFile::MapFile(const std::string& path) : path_(path) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("无法打开 map 文件: " + path);
    }

    std::string line;
    bool in_map = false;
    std::string pending_section;   // "节名独占一行"时的缓存

    while (std::getline(in, line)) {
        // 只看 "Linker script and memory map" 之后的部分
        if (!in_map) {
            if (line.find("Linker script and memory map") != std::string::npos) {
                in_map = true;
            }
            continue;
        }
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }

        std::istringstream ss(line);
        std::vector<std::string> tok;
        std::string t;
        while (ss >> t) {
            tok.push_back(t);
        }
        if (tok.empty()) continue;

        if (tok[0][0] == '*') continue;   // 通配符规则行，不是实际贡献

        // 找前两个 0x... 字段：第一个是地址，第二个是大小
        std::size_t first_hex = std::string::npos;
        std::size_t second_hex = std::string::npos;
        for (std::size_t i = 0; i < tok.size(); ++i) {
            if (!is_hex_token(tok[i])) continue;
            if (first_hex == std::string::npos) {
                first_hex = i;
            } else {
                second_hex = i;
                break;
            }
        }

        if (first_hex == std::string::npos) {
            // 整行只有一个字段且不是数字：这是"节名独占一行"的写法，
            // 真正的地址和大小在下一行。节名太长时 GNU ld 就会这么排版。
            if (tok.size() == 1) {
                pending_section = tok[0];
            }
            continue;
        }
        if (second_hex == std::string::npos) continue;   // 链接符号行，只有地址没有大小

        if (!looks_like_object(tok.back())) {
            // 节头（.flash.text  0xADDR  0xSIZE）说的是这一节的总量，
            // 不是某个人贡献的，记下名字留给后面的两行式贡献用。
            if (first_hex == 1) {
                pending_section = tok[0];
            }
            continue;
        }

        MapEntry e;
        e.section = (first_hex == 0) ? pending_section : tok[0];
        if (e.section.empty()) {
            e.section = "<未知>";
        }
        e.addr   = to_hex(tok[first_hex]);
        e.size   = to_hex(tok[second_hex]);
        e.object = tok.back();
        entries_.push_back(std::move(e));
    }
}

}  // namespace fwres