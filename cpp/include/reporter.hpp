// Reporter —— 报告输出层
//
// 设计意图：解析逻辑只负责算出数据，输出格式交给具体的 Reporter。
// 以后要加 HTML、CSV、JUnit，只需要再派生一个类，main 一行都不用改。
#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <ostream>
#include <string>
#include <vector>

namespace fwres {

// ESP32 地址映射分区
enum class Region { FlashIrom, FlashDrom, Iram, Dram, Rtc, Other };
constexpr std::size_t kRegionCount = 6;

Region region_of(std::uint64_t addr);
const char* region_name(Region r);

// 报告需要的一行节信息（只在 --sections 时填）
struct SectionRow {
    std::string   name;
    std::uint64_t addr = 0;
    std::uint64_t size = 0;
    bool alloc = false;
    bool writable = false;
    bool code = false;
    bool nobits = false;
};

// 报告需要的全部数据。Reporter 只读它，不负责产生它。
struct ReportData {
    std::string path;
    std::string arch;
    unsigned    machine = 0;
    int         bits = 32;
    bool        little_endian = true;
    std::uint64_t entry = 0;
    std::size_t   section_count = 0;
    std::array<std::uint64_t, kRegionCount> bytes{};
    std::uint64_t flash = 0;
    std::uint64_t ram = 0;
    std::uint64_t non_alloc = 0;
    std::vector<SectionRow> sections;
};

// 抽象基类：纯虚函数，谁派生谁实现
class Reporter {
public:
    virtual ~Reporter() = default;                                   // 虚析构，派生类才能被正确销毁
    virtual void write(std::ostream& out, const ReportData& d) const = 0;
    virtual const char* name() const = 0;
};

class TextReporter : public Reporter {
public:
    void write(std::ostream& out, const ReportData& d) const override;
    const char* name() const override { return "text"; }
};

class JsonReporter : public Reporter {
public:
    void write(std::ostream& out, const ReportData& d) const override;
    const char* name() const override { return "json"; }
};

// 工厂：按名字造对象。返回 unique_ptr，调用方不用管 delete。
std::unique_ptr<Reporter> make_reporter(const std::string& kind);

// 输出用的几个小工具（main 里的 map 分支也会用）
std::string commas(std::uint64_t v);
std::string human(std::uint64_t v);
std::string pad_display(const std::string& s, std::size_t width);

}  // namespace fwres