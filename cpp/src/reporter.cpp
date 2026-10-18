#include "reporter.hpp"

#include <algorithm>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace fwres {

Region region_of(std::uint64_t addr) {
    if (addr >= 0x40080000ULL && addr < 0x400A0000ULL) return Region::Iram;
    if (addr >= 0x40070000ULL && addr < 0x40072000ULL) return Region::Rtc;
    if (addr >= 0x3FF80000ULL && addr < 0x3FF82000ULL) return Region::Rtc;
    if (addr >= 0x50000000ULL && addr < 0x50002000ULL) return Region::Rtc;
    if (addr >= 0x3FF90000ULL && addr < 0x40000000ULL) return Region::Dram;
    if (addr >= 0x400D0000ULL && addr < 0x40400000ULL) return Region::FlashIrom;
    if (addr >= 0x3F400000ULL && addr < 0x3F800000ULL) return Region::FlashDrom;
    return Region::Other;
}

const char* region_name(Region r) {
    switch (r) {
        case Region::FlashIrom: return "FLASH 指令段 (IROM)";
        case Region::FlashDrom: return "FLASH 只读数据 (DROM)";
        case Region::Iram:      return "IRAM  指令 RAM";
        case Region::Dram:      return "DRAM  数据 RAM";
        case Region::Rtc:       return "RTC   低功耗 RAM";
        case Region::Other:     return "未映射(不计入)";
    }
    return "?";
}

std::size_t display_width(const std::string& s) {
    std::size_t w = 0;
    for (std::size_t i = 0; i < s.size();) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80)                { w += 1; i += 1; }
        else if ((c & 0xE0) == 0xC0) { w += 1; i += 2; }
        else if ((c & 0xF0) == 0xE0) { w += 2; i += 3; }
        else                         { w += 2; i += 4; }
    }
    return w;
}

std::string pad_display(const std::string& s, std::size_t width) {
    const std::size_t w = display_width(s);
    return s + std::string(width > w ? width - w : 0, ' ');
}

std::string commas(std::uint64_t v) {
    std::string s = std::to_string(v);
    for (int i = static_cast<int>(s.size()) - 3; i > 0; i -= 3) {
        s.insert(static_cast<std::size_t>(i), ",");
    }
    return s;
}

std::string human(std::uint64_t v) {
    const char* unit[] = {"B", "KB", "MB", "GB"};
    double d = static_cast<double>(v);
    int u = 0;
    while (d >= 1024.0 && u < 3) {
        d /= 1024.0;
        ++u;
    }
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.2f %s", d, unit[u]);
    return std::string(buf);
}

void TextReporter::write(std::ostream& out, const ReportData& d) const {
    out << "=== fwres 构建产物分析 ===\n";
    out << "文件      : " << d.path << "\n";
    out << "位宽/字节序: " << (d.bits == 32 ? "32 位" : "64 位") << " / "
        << (d.little_endian ? "小端" : "大端") << "\n";
    out << "架构      : " << d.arch << " (" << d.machine << ")\n";
    out << "入口地址  : 0x" << std::hex << d.entry << std::dec << "\n";
    out << "节数量    : " << d.section_count << "\n\n";

    out << "区域                     字节数          换算\n";
    out << "---------------------------------------------------\n";
    for (std::size_t i = 0; i < kRegionCount; ++i) {
        const std::string label = pad_display(region_name(static_cast<Region>(i)), 26);
        out << label << commas(d.bytes[i]) << " B   (" << human(d.bytes[i]) << ")\n";
    }
    out << "---------------------------------------------------\n";
    out << "FLASH 合计 = " << commas(d.flash) << " B (" << human(d.flash) << ")\n";
    out << "RAM   合计 = " << commas(d.ram) << " B (" << human(d.ram) << ")\n";
    out << "未计入(调试/符号等) = " << commas(d.non_alloc) << " B\n";

    if (!d.sections.empty()) {
        out << "\n=== 全部节 (按大小降序) ===\n";
        std::vector<const SectionRow*> all;
        all.reserve(d.sections.size());
        for (const auto& s : d.sections) all.push_back(&s);
        std::sort(all.begin(), all.end(), [](const SectionRow* a, const SectionRow* b) {
            return a->size > b->size;
        });
        for (const auto* s : all) {
            std::string flags;
            flags += s->alloc ? 'A' : '-';
            flags += s->writable ? 'W' : '-';
            flags += s->code ? 'X' : '-';
            flags += s->nobits ? 'B' : '-';
            out << pad_display(s->name, 30) << flags << "  0x"
                << std::hex << s->addr << std::dec << "  " << commas(s->size) << " B\n";
        }
    }
}

namespace {

std::string json_escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"':  o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n";  break;
            case '\r': o += "\\r";  break;
            case '\t': o += "\\t";  break;
            default:   o += c;      break;
        }
    }
    return o;
}

}  // namespace

void JsonReporter::write(std::ostream& out, const ReportData& d) const {
    out << "{\n";
    out << "  \"tool\": \"fwres\",\n";
    out << "  \"file\": \"" << json_escape(d.path) << "\",\n";
    out << "  \"arch\": \"" << json_escape(d.arch) << "\",\n";
    out << "  \"machine\": " << d.machine << ",\n";
    out << "  \"bits\": " << d.bits << ",\n";
    out << "  \"little_endian\": " << (d.little_endian ? "true" : "false") << ",\n";
    out << "  \"entry\": \"0x" << std::hex << d.entry << std::dec << "\",\n";
    out << "  \"section_count\": " << d.section_count << ",\n";
    out << "  \"flash_bytes\": " << d.flash << ",\n";
    out << "  \"ram_bytes\": " << d.ram << ",\n";
    out << "  \"debug_bytes\": " << d.non_alloc << ",\n";
    out << "  \"regions\": {\n";
    for (std::size_t i = 0; i < kRegionCount; ++i) {
        out << "    \"" << region_name(static_cast<Region>(i)) << "\": " << d.bytes[i];
        out << (i + 1 < kRegionCount ? ",\n" : "\n");
    }
    out << "  }";
    if (!d.sections.empty()) {
        out << ",\n  \"sections\": [\n";
        for (std::size_t i = 0; i < d.sections.size(); ++i) {
            const SectionRow& s = d.sections[i];
            out << "    {\"name\": \"" << json_escape(s.name) << "\", \"addr\": \"0x"
                << std::hex << s.addr << std::dec << "\", \"size\": " << s.size
                << ", \"alloc\": " << (s.alloc ? "true" : "false")
                << ", \"writable\": " << (s.writable ? "true" : "false")
                << ", \"code\": " << (s.code ? "true" : "false")
                << ", \"nobits\": " << (s.nobits ? "true" : "false") << "}";
            out << (i + 1 < d.sections.size() ? ",\n" : "\n");
        }
        out << "  ]";
    }
    out << "\n}\n";
}

std::unique_ptr<Reporter> make_reporter(const std::string& kind) {
    if (kind == "json") {
        return std::unique_ptr<Reporter>(new JsonReporter());
    }
    if (kind == "text") {
        return std::unique_ptr<Reporter>(new TextReporter());
    }
    throw std::runtime_error("不支持的报告格式: " + kind);
}

}  // namespace fwres