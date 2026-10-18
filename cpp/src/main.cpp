// fwres —— 嵌入式固件资源分析器（命令行入口）
//
// 用法: fwres <app.elf> [--sections] [--json] [--flash-kb N] [--ram-kb N]
//       fwres --map <app.map>
// 退出码: 0 成功 / 1 解析失败 / 2 参数错误 / 3 超预算
#include "elf_file.hpp"
#include "map_file.hpp"
#include "reporter.hpp"
#include "telemetry.hpp"

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

void usage() {
    std::cout << "fwres —— 嵌入式固件资源分析器\n"
                 "用法: fwres <app.elf> [--sections] [--json] [--flash-kb N] [--ram-kb N]\n"
                 "      fwres --map <app.map>\n"
                 "  --sections   打印全部节\n"
                 "  --json       以 JSON 输出（方便脚本/CI 解析）\n"
                 "  --flash-kb   设 Flash 预算，超过则退出码 3\n"
                 "  --ram-kb     设 RAM 预算，超过则退出码 3\n";
}

}  // namespace

int main(int argc, char** argv) {
    bool show_all = false;
    bool json = false;
    std::string path;
    std::string map_path;
    std::string runtime_path;
    std::uint64_t flash_limit = 0;
    std::uint64_t ram_limit   = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--runtime") {
            if (i + 1 >= argc) { std::cerr << "--runtime 后面要跟 CSV 路径\n"; return 2; }
            runtime_path = argv[++i];
        } else if (a == "--map") {
            if (i + 1 >= argc) { std::cerr << "--map 后面要跟 .map 文件路径\n"; return 2; }
            map_path = argv[++i];
        } else if (a == "--flash-kb" || a == "--ram-kb") {
            if (i + 1 >= argc) { std::cerr << a << " 后面要跟数字\n"; return 2; }
            std::uint64_t kb = 0;
            try { kb = static_cast<std::uint64_t>(std::stoull(argv[++i])); }
            catch (const std::exception&) { std::cerr << "预算必须是整数 KB\n"; return 2; }
            if (a == "--flash-kb") flash_limit = kb * 1024ULL;
            else ram_limit = kb * 1024ULL;
        } else if (a == "--sections") {
            show_all = true;
        } else if (a == "--json") {
            json = true;
        } else if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (!a.empty() && a[0] == '-') {
            std::cerr << "未知参数: " << a << "\n";
            return 2;
        } else if (path.empty()) {
            path = a;
        } else {
            std::cerr << "一次只能分析一个 ELF 文件\n";
            return 2;
        }
    }
    if (path.empty() && map_path.empty()) {
        usage();
        return 2;
    }

    try {
        // ---------- map 路径 ----------
        if (!map_path.empty()) {
            fwres::MapFile mf(map_path);
            std::map<std::string, std::uint64_t> by_obj;
            std::map<std::string, std::uint64_t> by_lib;
            std::map<fwres::Region, std::uint64_t> region_end;
            std::uint64_t mflash = 0, mram = 0;
            for (const auto& e : mf.entries()) {
                const fwres::Region r = fwres::region_of(e.addr);
                if (r == fwres::Region::Other) continue;
                std::uint64_t counted = e.size;
                const auto it_end = region_end.find(r);
                if (it_end != region_end.end() && e.addr < it_end->second) {
                    const std::uint64_t stop = e.addr + e.size;
                    counted = (stop > it_end->second) ? (stop - it_end->second) : 0;
                }
                if (counted == 0) continue;
                region_end[r] = std::max(region_end[r], e.addr + e.size);
                if (r == fwres::Region::FlashIrom || r == fwres::Region::FlashDrom) mflash += counted;
                if (r == fwres::Region::Iram || r == fwres::Region::Dram || r == fwres::Region::Rtc) mram += counted;
                by_obj[e.object] += counted;
                std::string lib = e.object;
                const std::size_t lp = lib.find(char(40));
                if (lp != std::string::npos) lib = lib.substr(0, lp);
                by_lib[lib] += counted;
            }

            std::cout << "=== fwres map 文件分析 ===\n";
            std::cout << "文件      : " << map_path << "\n";
            std::cout << "贡献记录  : " << mf.entries().size() << " 条\n";
            std::cout << "FLASH     : " << fwres::commas(mflash) << " B (" << fwres::human(mflash) << ")\n";
            std::cout << "RAM       : " << fwres::commas(mram) << " B (" << fwres::human(mram) << ")\n";

            std::vector<std::pair<std::uint64_t, std::string>> v;
            for (const auto& kv : by_lib) v.emplace_back(kv.second, kv.first);
            std::sort(v.begin(), v.end(), [](const std::pair<std::uint64_t, std::string>& a,
                                             const std::pair<std::uint64_t, std::string>& b) {
                return a.first > b.first;
            });
            std::cout << "\n--- Flash 占用 Top 15（按库/组件）---\n";
            for (std::size_t k = 0; k < v.size() && k < 15; ++k) {
                std::cout << fwres::pad_display(fwres::human(v[k].first), 11) << v[k].second << "\n";
            }
            v.clear();
            for (const auto& kv : by_obj) v.emplace_back(kv.second, kv.first);
            std::sort(v.begin(), v.end(), [](const std::pair<std::uint64_t, std::string>& a,
                                             const std::pair<std::uint64_t, std::string>& b) {
                return a.first > b.first;
            });
            std::cout << "\n--- Flash 占用 Top 10（按目标文件）---\n";
            for (std::size_t k = 0; k < v.size() && k < 10; ++k) {
                std::cout << fwres::pad_display(fwres::human(v[k].first), 11) << v[k].second << "\n";
            }
            return 0;
        }

        // ---------- ELF 路径 ----------
        fwres::ElfFile elf(path);

        fwres::ReportData d;
        d.path = path;
        d.arch = fwres::machine_name(elf.machine());
        d.machine = elf.machine();
        d.bits = (elf.elf_class() == 1) ? 32 : 64;
        d.little_endian = (elf.data_encoding() == 1);
        d.entry = elf.entry();
        d.section_count = elf.sections().size();

        for (const auto& s : elf.sections()) {
            if (!s.is_alloc()) {
                d.non_alloc += s.size;
                continue;
            }
            d.bytes[static_cast<std::size_t>(fwres::region_of(s.addr))] += s.size;
        }
        d.flash = d.bytes[0] + d.bytes[1];
        d.ram   = d.bytes[2] + d.bytes[3] + d.bytes[4];

        if (show_all) {
            for (const auto& s : elf.sections()) {
                if (s.size == 0) continue;
                fwres::SectionRow row;
                row.name = s.name;
                row.addr = s.addr;
                row.size = s.size;
                row.alloc = s.is_alloc();
                row.writable = s.is_writable();
                row.code = s.is_code();
                row.nobits = s.is_nobits();
                d.sections.push_back(row);
            }
        }

        std::unique_ptr<fwres::Reporter> rep = fwres::make_reporter(json ? "json" : "text");
        rep->write(std::cout, d);

        bool runtime_ok = true;
        if (!runtime_path.empty()) {
            fwres::Telemetry tel(runtime_path);
            runtime_ok = fwres::write_runtime_section(std::cout, d, tel);
        }

        if (flash_limit != 0 || ram_limit != 0) {
            bool ok = true;
            if (flash_limit != 0 && d.flash > flash_limit) ok = false;
            if (ram_limit != 0 && d.ram > ram_limit) ok = false;
            std::ostream& msg = json ? std::cerr : std::cout;
            msg << "\n=== 预算检查 ===\n";
            if (flash_limit != 0) msg << "Flash  " << fwres::commas(d.flash) << " / " << fwres::commas(flash_limit) << " B  " << (d.flash > flash_limit ? "超预算" : "通过") << "\n";
            if (ram_limit != 0)   msg << "RAM    " << fwres::commas(d.ram) << " / " << fwres::commas(ram_limit) << " B  " << (d.ram > ram_limit ? "超预算" : "通过") << "\n";
            if (!ok) { msg << "结果: 超预算，退出码 3（CI 会判失败）\n"; return 3; }
            msg << "结果: 在预算内\n";
        }
        if (!runtime_ok) return 4;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "解析失败: " << e.what() << "\n";
        return 1;
    }
}
