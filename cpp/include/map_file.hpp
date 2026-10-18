// MapFile —— 解析 GNU ld 生成的 .map 文件
//
// .map 是一份"谁把什么东西放到了哪个地址"的账本。
// ELF 告诉你每一节多大，.map 才能告诉你"这一节里是谁占的"。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fwres {

// 一条"贡献记录"：某个目标文件往某个节里塞了多少字节
struct MapEntry {
    std::string   section;    // 节名，例如 .literal.foo 或 .flash.text
    std::string   object;     // 贡献者，例如 esp-idf/freertos/libfreertos.a(tasks.c.obj)
    std::uint64_t addr = 0;   // 链接地址
    std::uint64_t size = 0;   // 字节数
};

class MapFile {
public:
    explicit MapFile(const std::string& path);

    const std::string& path() const { return path_; }
    const std::vector<MapEntry>& entries() const { return entries_; }

private:
    std::string path_;
    std::vector<MapEntry> entries_;
};

}  // namespace fwres